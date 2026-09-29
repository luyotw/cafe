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


class VersionedJsonStore:
    """Persist a named collection with atomic replacement and record recovery."""

    def __init__(self, path: Path, *, schema_version: int, collection: str) -> None:
        self.path = Path(path)
        self.schema_version = schema_version
        self.collection = collection

    def read(self) -> dict[str, dict[str, Any]]:
        with _lock(self.path):
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

    def write(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _lock(self.path):
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        {"schema_version": self.schema_version, self.collection: records},
                        stream,
                        sort_keys=True,
                        indent=2,
                    )
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self.path.chmod(0o600)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise StoreError(f"Could not persist kickoff store: {self.path}") from exc


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
