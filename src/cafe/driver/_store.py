"""Bounded, contract-specific persistence.  Runtime state never uses this store."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

from cafe.core.packet_io import atomic_write_bytes, canonical_json, sha256_bytes

from ._schema import validate_contract


CONTRACT_FILENAME = "contract.json"
LOCK_FILENAME = "contract.lock"
MAX_CONTRACT_BYTES = 256 * 1024


def contract_path(issue_dir: Path) -> Path:
    return Path(issue_dir) / "driver" / CONTRACT_FILENAME


def _safe_driver_directory(issue_dir: Path, *, create: bool) -> Path:
    issue = Path(issue_dir)
    if issue.exists() and issue.is_symlink():
        raise ValueError("issue directory must not be a symlink")
    if create:
        issue.mkdir(parents=True, exist_ok=True)
    if not issue.is_dir():
        raise ValueError("issue directory is unavailable")
    driver = issue / "driver"
    if driver.exists() and driver.is_symlink():
        raise ValueError("driver directory must not be a symlink")
    if create:
        driver.mkdir(mode=0o700, exist_ok=True)
    if not driver.is_dir():
        raise ValueError("driver directory is unavailable")
    return driver


@contextmanager
def contract_lock(issue_dir: Path) -> Iterator[None]:
    """Serialize activation/replacement; fail closed if a process lock cannot be held."""
    driver = _safe_driver_directory(issue_dir, create=True)
    lock_path = driver / LOCK_FILENAME
    if lock_path.exists() and lock_path.is_symlink():
        raise ValueError("contract lock must not be a symlink")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise ValueError("cannot acquire Driver contract lock") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _decode_exact(content: bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("contract JSON contains duplicate keys")
            result[key] = value
        return result

    if len(content) > MAX_CONTRACT_BYTES:
        raise ValueError("contract exceeds the maximum bounded size")
    try:
        document = json.loads(content.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("contract JSON is unreadable") from exc
    if not isinstance(document, dict):
        raise ValueError("contract JSON must be an object")
    return document


def load_contract(
    issue_dir: Path, *, issue_name: str | None = None, workflow_id: str | None = None
) -> tuple[dict[str, Any], str]:
    """Load the sole authority after bounded, symlink-safe validation."""
    driver = _safe_driver_directory(issue_dir, create=False)
    path = driver / CONTRACT_FILENAME
    if not path.is_file() or path.is_symlink():
        raise ValueError("Driver contract is missing or unsafe")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError("Driver contract is unreadable") from exc
    return validate_contract(_decode_exact(content), issue_name=issue_name, workflow_id=workflow_id), sha256_bytes(content)


def write_contract(
    issue_dir: Path,
    document: Mapping[str, Any],
    *,
    expected_predecessor_sha256: str | None,
) -> str:
    """Atomically install one validated replacement while holding ``contract_lock``."""
    driver = _safe_driver_directory(issue_dir, create=True)
    path = driver / CONTRACT_FILENAME
    if path.exists() and path.is_symlink():
        raise ValueError("Driver contract must not be a symlink")
    exists = path.exists()
    if expected_predecessor_sha256 is None:
        if exists:
            raise ValueError("Driver contract already exists")
    else:
        if not exists:
            raise ValueError("Driver contract predecessor is missing")
        actual = sha256_bytes(path.read_bytes())
        if actual != expected_predecessor_sha256:
            raise ValueError("Driver contract predecessor is stale")
    validated = validate_contract(document)
    content = canonical_json(validated)
    if len(content) > MAX_CONTRACT_BYTES:
        raise ValueError("contract exceeds the maximum bounded size")
    atomic_write_bytes(path, content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return sha256_bytes(content)
