"""Bounded, contract-specific persistence.  Runtime state never uses this store."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from cafe.core.packet_io import atomic_write_bytes, canonical_json, sha256_bytes

from ._schema import validate_contract

CONTRACT_FILENAME = "contract.json"
LOCK_FILENAME = "contract.lock"
MAX_CONTRACT_BYTES = 256 * 1024


class ManagerContractMissingError(ValueError):
    """Raised only when no contract authority exists at the expected location."""


class ManagerContractUnsafeError(ValueError):
    """Raised when a present contract location cannot be trusted as authority."""


def contract_path(issue_dir: Path) -> Path:
    return Path(issue_dir) / "manager" / CONTRACT_FILENAME


def _role_contract_present(issue_dir: Path, role: str) -> bool:
    """Distinguish an absent role record from an unsafe present path."""
    issue = Path(issue_dir)
    path = issue / role / CONTRACT_FILENAME
    try:
        _reject_symlink_ancestors(issue)
    except ValueError as exc:
        raise ValueError(
            f"Unsafe role contract path {path}; inspect the path and restore or remove the "
            "symlink before retrying."
        ) from exc
    directory = issue / role
    try:
        directory_stat = directory.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(
            f"Cannot inspect role contract path {path}; recover the path before retrying."
        ) from exc
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ValueError(
            f"Unsafe role contract path {path}; restore or remove the role directory before retrying."
        )
    try:
        record_stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(
            f"Cannot inspect role contract path {path}; recover the path before retrying."
        ) from exc
    if stat.S_ISLNK(record_stat.st_mode) or not stat.S_ISREG(record_stat.st_mode):
        raise ValueError(
            f"Unsafe role contract path {path}; restore or remove the alias before retrying."
        )
    return True


def select_authority_directory(issue_dir: Path) -> Path:
    """Select the sole writable role directory, rejecting unproven dual records."""
    issue = Path(issue_dir)
    manager_path = issue / "manager" / CONTRACT_FILENAME
    driver_path = issue / "driver" / CONTRACT_FILENAME
    manager_present = _role_contract_present(issue, "manager")
    driver_present = _role_contract_present(issue, "driver")
    if manager_present and driver_present:
        from cafe.driver._store import load_contract as load_driver_contract

        try:
            manager, _ = load_contract(issue)
            driver, _ = load_driver_contract(issue, allow_legacy_upgrade=True)
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"Cannot validate both role contracts for {issue}; inspect {manager_path} and "
                f"{driver_path}, then recover the confirmed authority before retrying: {exc}"
            ) from exc
        projected = dict(driver)
        projected["manager"] = projected.pop("driver")
        for field in ("confirmation_contract", "task_contract"):
            owner = projected.get(field)
            if isinstance(owner, dict) and "driver_confirmable" in owner:
                owner["manager_confirmable"] = owner.pop("driver_confirmable")
        handoffs = projected.get("reactive_user_handoffs")
        if isinstance(handoffs, dict):
            if handoffs.get("alignment_checkpoint") == "driver_resolvable_when_clear":
                handoffs["alignment_checkpoint"] = "manager_resolvable_when_clear"
            if handoffs.get("need_clarification") == "driver_confirmable":
                handoffs["need_clarification"] = "manager_confirmable"
        fields = (
            "locales",
            "delivery_contract",
            "confirmation_contract",
            "reactive_user_handoffs",
            "phases",
            "proactive_review",
            "manager",
            "checkout",
            "task_contract",
        )
        if any(manager.get(field) != projected.get(field) for field in fields):
            raise ValueError(
                f"Manager and legacy Driver contracts conflict for {issue}; the Driver contract "
                f"remains authoritative. Inspect {driver_path} and {manager_path}, then recover "
                "the matching record before retrying."
            )
        return issue / "driver"
    if driver_present:
        return issue / "driver"
    return issue / "manager"


def _reject_symlink_ancestors(path: Path) -> None:
    """Keep a caller-provided issue root inside its lexical, non-aliased tree."""
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("Manager contract paths must not traverse a symlink")


def _safe_manager_directory(issue_dir: Path, *, create: bool) -> Path:
    issue = Path(issue_dir)
    _reject_symlink_ancestors(issue)
    if issue.exists() and issue.is_symlink():
        raise ValueError("issue directory must not be a symlink")
    if create:
        issue.mkdir(parents=True, exist_ok=True)
    if not issue.is_dir():
        raise ValueError("issue directory is unavailable")
    manager = issue / "manager"
    if manager.exists() and manager.is_symlink():
        raise ValueError("manager directory must not be a symlink")
    if create:
        manager.mkdir(mode=0o700, exist_ok=True)
    if not manager.is_dir():
        raise ValueError("manager directory is unavailable")
    return manager


@contextmanager
def contract_lock(issue_dir: Path, *, blocking: bool = True) -> Iterator[None]:
    """Serialize activation/replacement; fail closed if a process lock cannot be held."""
    _assert_manager_write_authority(issue_dir)
    manager = _safe_manager_directory(issue_dir, create=True)
    lock_path = manager / LOCK_FILENAME
    if lock_path.exists() and lock_path.is_symlink():
        raise ValueError("contract lock must not be a symlink")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            if not blocking and isinstance(exc, BlockingIOError):
                raise
            raise ValueError("cannot acquire Manager contract lock") from exc
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


def _read_bounded(path: Path, *, label: str) -> bytes:
    """Check type and byte budget before allocating or parsing untrusted input."""
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} is unsafe")
    if metadata.st_size > MAX_CONTRACT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONTRACT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if len(content) > MAX_CONTRACT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    return content


def load_contract(
    issue_dir: Path,
    *,
    issue_name: str | None = None,
    workflow_id: str | None = None,
    allow_legacy_upgrade: bool = False,
) -> tuple[dict[str, Any], str]:
    """Load the sole authority after bounded, symlink-safe validation."""
    issue = Path(issue_dir)
    try:
        manager = _safe_manager_directory(issue, create=False)
    except ValueError as exc:
        if not issue.exists() or not (issue / "manager").exists():
            raise ManagerContractMissingError("Manager contract is missing") from exc
        raise ManagerContractUnsafeError("Manager contract is unsafe") from exc
    path = manager / CONTRACT_FILENAME
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ManagerContractMissingError("Manager contract is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagerContractUnsafeError("Manager contract is unsafe")
    content = _read_bounded(path, label="Manager contract")
    return (
        validate_contract(
            _decode_exact(content),
            issue_name=issue_name,
            workflow_id=workflow_id,
            allow_legacy_upgrade=allow_legacy_upgrade,
        ),
        sha256_bytes(content),
    )


def write_contract(
    issue_dir: Path,
    document: Mapping[str, Any],
    *,
    expected_predecessor_sha256: str | None,
) -> str:
    """Atomically install one validated replacement while holding ``contract_lock``."""
    _assert_manager_write_authority(issue_dir)
    manager = _safe_manager_directory(issue_dir, create=True)
    path = manager / CONTRACT_FILENAME
    if path.exists() and path.is_symlink():
        raise ValueError("Manager contract must not be a symlink")
    exists = path.exists()
    if expected_predecessor_sha256 is None:
        if exists:
            raise ValueError("Manager contract already exists")
    else:
        if not exists:
            raise ValueError("Manager contract predecessor is missing")
        actual = sha256_bytes(_read_bounded(path, label="Manager contract predecessor"))
        if actual != expected_predecessor_sha256:
            raise ValueError("Manager contract predecessor is stale")
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


def write_updated_contract(
    issue_dir: Path,
    document: Mapping[str, Any],
    *,
    expected_predecessor_sha256: str,
) -> str:
    """Atomically replace a supported contract after an exact CAS check."""
    _assert_manager_write_authority(issue_dir)
    manager = _safe_manager_directory(issue_dir, create=False)
    path = manager / CONTRACT_FILENAME
    actual = sha256_bytes(_read_bounded(path, label="Manager contract predecessor"))
    if actual != expected_predecessor_sha256:
        raise ValueError("Manager contract predecessor is stale")
    validated = validate_contract(document, allow_legacy_upgrade=True)
    content = canonical_json(validated)
    if len(content) > MAX_CONTRACT_BYTES:
        raise ValueError("contract exceeds the maximum bounded size")
    atomic_write_bytes(path, content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return sha256_bytes(content)


def _assert_manager_write_authority(issue_dir: Path) -> None:
    """Keep an existing legacy Driver record as the only writable authority."""
    issue = Path(issue_dir)
    manager_exists = (issue / "manager" / CONTRACT_FILENAME).exists()
    driver_exists = (issue / "driver" / CONTRACT_FILENAME).exists()
    if driver_exists:
        if manager_exists:
            select_authority_directory(issue)
        raise ValueError("legacy Driver authority remains writable only through its compatibility adapter")
