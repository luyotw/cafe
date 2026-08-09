"""Schema-neutral durable packet persistence helpers.

The helpers deliberately know nothing about delta or context packet fields.  Each
caller supplies its own validation and identity checks before reusing bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_metadata(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {"path": source.as_posix(), "state": "missing"}
    if not source.is_file():
        return {"path": source.as_posix(), "state": "not_file"}
    try:
        content = source.read_bytes()
    except OSError:
        return {"path": source.as_posix(), "state": "unreadable"}
    return {"path": source.as_posix(), "state": "file", "bytes": len(content), "sha256": sha256_bytes(content)}


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compact_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_or_persist_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    validate: Callable[[Any], None],
    matches_identity: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist immutable JSON once, or return a validated matching prior copy."""
    if path.exists():
        try:
            content = path.read_bytes()
            persisted = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid persisted packet: {path}") from exc
        actual = sha256_bytes(content)
        if expected_sha256 is not None and actual != expected_sha256:
            raise ValueError(f"Persisted packet hash mismatch: {path}")
        validate(persisted)
        if not matches_identity(persisted, value):
            raise ValueError(f"Persisted packet identity mismatch: {path}")
        packet = dict(persisted)
    else:
        packet = dict(value)
        validate(packet)
        content = canonical_json(packet)
        atomic_write_bytes(path, content)
    return packet, {"path": path.as_posix(), "bytes": len(content), "sha256": sha256_bytes(content)}
