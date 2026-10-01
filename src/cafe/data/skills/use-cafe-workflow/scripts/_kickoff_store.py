"""Private, versioned local storage for reusable kickoff inputs and evidence."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class StoreError(OSError):
    """A local record could not be safely persisted."""


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - Windows fallback
            pass
        os.close(descriptor)


def atomic_write_text(path: Path, text: str) -> None:
    """Publish a complete private file; failed writes leave the old bytes intact."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise StoreError(f"Could not persist kickoff store: {path}") from exc


class VersionedJsonStore:
    """Persist a named collection with atomic replacement and record recovery."""

    def __init__(self, path: Path, *, schema_version: int, collection: str) -> None:
        self.path = Path(path)
        self.schema_version = schema_version
        self.collection = collection

    def read(self) -> dict[str, dict[str, Any]]:
        with _lock(self.path):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(document, dict) or document.get("schema_version") != self.schema_version:
            return {}
        records = document.get(self.collection)
        if not isinstance(records, dict):
            return {}
        return {
            key: value
            for key, value in records.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _write_unlocked(self, records: dict[str, dict[str, Any]]) -> None:
        atomic_write_text(self.path, json.dumps(
            {"schema_version": self.schema_version, self.collection: records},
            sort_keys=True, indent=2,
        ) + "\n")

    def write(self, records: dict[str, dict[str, Any]]) -> None:
        with _lock(self.path):
            self._write_unlocked(records)

    def update(self, mutator) -> dict[str, dict[str, Any]]:
        """Apply one read-modify-replace transaction without losing peer updates."""
        with _lock(self.path):
            records = self._read_unlocked()
            updated = mutator(records)
            if not isinstance(updated, dict) or any(
                not isinstance(key, str) or not isinstance(value, dict)
                for key, value in updated.items()
            ):
                raise StoreError("Kickoff store update must return string-keyed records")
            self._write_unlocked(updated)
            return updated


def repository_identity(project_root: Path) -> str:
    """Return a stable local identity shared by linked worktrees only."""
    root = Path(project_root).expanduser().resolve()
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 0 and result.stdout.strip():
        return f"git:{Path(result.stdout.strip()).resolve()}"
    return f"path:{root}"
