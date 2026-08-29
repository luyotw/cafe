"""Durable recovery primitives for Global catalog publication transactions."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Callable, Optional


class CatalogRecoveryError(RuntimeError):
    """Raised before catalog reads when an incomplete transaction cannot recover."""


RecoveryInjector = Callable[[str, Optional[str]], None]
_MAX_TRANSACTION_RECORDS = 512
_MAX_EVIDENCE_TEXT = 512
_MAX_JOURNAL_BYTES = 1024 * 1024
_TRANSACTION_STATUSES = {
    "staging",
    "prepared",
    "publishing",
    "committed",
    "rolled_back",
    "rollback_incomplete",
}
_RECORD_STATES = {"pending", "backed_up", "published"}


def _noop_injector(_boundary: str, _entry_id: Optional[str]) -> None:
    return None


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory_chain(path: Path, stop: Path) -> None:
    """Persist directory entries from a leaf through an inclusive stable root."""
    current = path
    stop = stop.resolve()
    while True:
        fsync_directory(current)
        if current.resolve() == stop:
            return
        if current == current.parent or not current.resolve().is_relative_to(stop):
            raise CatalogRecoveryError("Catalog durability path escapes its root")
        current = current.parent


def fsync_tree(path: Path) -> None:
    """Persist copied file/tree bytes and directory entries before publication."""
    if path.is_symlink():
        fsync_directory(path.parent)
        return
    if path.is_file():
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(path.parent)
        return
    files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
    for item in files:
        descriptor = os.open(item, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    directories = [item for item in path.rglob("*") if item.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)
    fsync_directory(path)
    fsync_directory(path.parent)


def write_json_durable(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _bounded_text(value: BaseException | str) -> str:
    text = str(value) or type(value).__name__
    return text[:_MAX_EVIDENCE_TEXT]


def _require_real_directory(path: Path, *, within: Optional[Path] = None) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CatalogRecoveryError(f"Catalog recovery directory is unavailable: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise CatalogRecoveryError(f"Catalog recovery directory is unsafe: {path}")
    try:
        resolved = path.resolve(strict=True)
        if within is not None and not resolved.is_relative_to(within.resolve(strict=True)):
            raise CatalogRecoveryError(f"Catalog recovery directory escapes its root: {path}")
    except OSError as exc:
        raise CatalogRecoveryError(f"Catalog recovery directory is unsafe: {path}") from exc
    return resolved


def _validate_transaction_location(transaction: Path, global_root: Path) -> None:
    global_root = Path(global_root)
    transactions_root = global_root / ".catalog-transactions"
    if transaction.parent != transactions_root:
        raise CatalogRecoveryError("Catalog transaction is outside its owning root")
    _require_real_directory(global_root)
    _require_real_directory(transactions_root, within=global_root)
    _require_real_directory(transaction, within=transactions_root)


def _confined_leaf(root: Path, relative: Path) -> Path:
    resolved_root = _require_real_directory(root)
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CatalogRecoveryError(
                f"Catalog recovery ancestor is unavailable: {current}"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise CatalogRecoveryError(f"Catalog recovery ancestor is unsafe: {current}")
        try:
            if not current.resolve(strict=True).is_relative_to(resolved_root):
                raise CatalogRecoveryError(f"Catalog recovery ancestor escapes its root: {current}")
        except OSError as exc:
            raise CatalogRecoveryError(f"Catalog recovery ancestor is unsafe: {current}") from exc
    leaf = root / relative
    if not leaf.parent.resolve(strict=False).is_relative_to(resolved_root):
        raise CatalogRecoveryError(f"Catalog recovery path escapes its root: {leaf}")
    return leaf


def _valid_digest(value: str, *, allow_missing: bool) -> bool:
    return (allow_missing and value == "missing") or (
        len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    )


def _validated_records(payload: dict[str, object]) -> list[dict[str, str]]:
    from cafe.catalogs.resolver import (
        CatalogKind,
        CatalogResolver,
        CatalogValidationError,
    )

    raw_records = payload.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or len(raw_records) > _MAX_TRANSACTION_RECORDS
    ):
        raise CatalogRecoveryError("Catalog transaction record set is invalid or unbounded")
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_paths: set[Path] = set()
    for raw_record in raw_records:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "entry_id",
            "relative_path",
            "old_digest",
            "new_digest",
            "state",
        }:
            raise CatalogRecoveryError("Catalog transaction contains an invalid record")
        if any(not isinstance(value, str) for value in raw_record.values()):
            raise CatalogRecoveryError("Catalog transaction contains an invalid record")
        record = {
            "entry_id": raw_record["entry_id"],
            "relative_path": raw_record["relative_path"],
            "old_digest": raw_record["old_digest"],
            "new_digest": raw_record["new_digest"],
            "state": raw_record["state"],
        }
        relative = Path(record["relative_path"])
        try:
            prefix, key = record["entry_id"].split(":", 1)
            kind = CatalogKind(prefix)
            normalized = CatalogResolver._validate_key(kind, key)
        except (ValueError, CatalogValidationError) as exc:
            raise CatalogRecoveryError("Catalog transaction record identity is unsafe") from exc
        expected_relative = Path(CatalogResolver._DIRECTORIES[kind]) / (
            CatalogResolver._relative_entry_path(kind, normalized)
        )
        if (
            not record["entry_id"]
            or record["entry_id"] in seen
            or relative in seen_paths
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or key != normalized
            or relative != expected_relative
            or not _valid_digest(record["old_digest"], allow_missing=True)
            or not _valid_digest(record["new_digest"], allow_missing=False)
            or record["state"] not in _RECORD_STATES
            or (record["old_digest"] == "missing" and record["state"] == "backed_up")
        ):
            raise CatalogRecoveryError("Catalog transaction record is unsafe")
        seen.add(record["entry_id"])
        seen_paths.add(relative)
        records.append(record)
    return records


def _read_transaction_journal(transaction: Path, global_root: Path) -> dict[str, object]:
    _validate_transaction_location(transaction, global_root)
    journal = transaction / "transaction.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(journal, flags)
    except OSError as exc:
        raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_JOURNAL_BYTES:
            raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}")
    return payload


def _validated_transaction_payload(
    payload: dict[str, object], transaction: Path, global_root: Path
) -> list[dict[str, str]]:
    _validate_transaction_location(transaction, global_root)
    if set(payload) != {"schema_version", "status", "records"}:
        raise CatalogRecoveryError("Catalog transaction journal schema is invalid")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise CatalogRecoveryError("Catalog transaction journal schema is invalid")
    if not isinstance(payload["status"], str) or payload["status"] not in _TRANSACTION_STATUSES:
        raise CatalogRecoveryError("Catalog transaction journal status is invalid")
    records = _validated_records(payload)
    if payload["status"] in {"staging", "prepared"} and any(
        record["state"] != "pending" for record in records
    ):
        raise CatalogRecoveryError("Catalog transaction journal progress is invalid")
    if payload["status"] == "committed" and any(
        record["state"] != "published" for record in records
    ):
        raise CatalogRecoveryError("Catalog transaction journal progress is invalid")
    backup_root = transaction / "backups"
    _require_real_directory(backup_root, within=transaction)
    for record in records:
        relative = Path(record["relative_path"])
        _confined_leaf(global_root, relative)
        _confined_leaf(backup_root, relative)
    return records


def _validate_recovery_content(
    records: list[dict[str, str]], transaction: Path, global_root: Path
) -> None:
    from cafe.catalogs.resolver import content_digest

    backup_root = transaction / "backups"
    for record in records:
        relative = Path(record["relative_path"])
        target = global_root / relative
        backup = backup_root / relative
        if record["old_digest"] == "missing":
            if path_exists(backup):
                raise CatalogRecoveryError(
                    "Catalog recovery has an unexpected backup for a missing entry"
                )
            if path_exists(target) and content_digest(target) != record["new_digest"]:
                raise CatalogRecoveryError(
                    "Catalog recovery target does not match transaction intent"
                )
            continue
        if path_exists(backup):
            if content_digest(backup) != record["old_digest"]:
                raise CatalogRecoveryError(
                    "Catalog recovery backup does not match transaction intent"
                )
            if path_exists(target) and content_digest(target) != record["new_digest"]:
                raise CatalogRecoveryError(
                    "Catalog recovery target does not match transaction intent"
                )
        elif not path_exists(target) or content_digest(target) != record["old_digest"]:
            raise CatalogRecoveryError("Catalog recovery pre-update content is unavailable")


def recover_catalog_transaction(
    transaction: Path,
    global_root: Path,
    *,
    failure_injector: RecoveryInjector = _noop_injector,
    cause: BaseException | str = "interrupted publication",
    _payload: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Restore one incomplete publication to its durable pre-update state."""
    from cafe.catalogs.resolver import content_digest

    journal = transaction / "transaction.json"
    payload = _payload or _read_transaction_journal(transaction, global_root)
    records = _validated_transaction_payload(payload, transaction, global_root)
    _validate_recovery_content(records, transaction, global_root)
    backup_root = transaction / "backups"
    selected = [record["entry_id"] for record in records]
    published = [record["entry_id"] for record in records if record["state"] == "published"]
    restored: list[str] = []
    rollback_errors: list[str] = []

    for record in reversed(records):
        entry_id = record["entry_id"]
        relative = Path(record["relative_path"])
        target = global_root / relative
        backup = backup_root / relative
        try:
            if record["old_digest"] == "missing":
                if path_exists(backup):
                    raise OSError("unexpected backup for a previously missing entry")
                if path_exists(target):
                    if content_digest(target) != record["new_digest"]:
                        raise OSError("published content does not match transaction intent")
                    failure_injector("rollback_remove", entry_id)
                    _remove_path(target)
                    fsync_directory_chain(target.parent, global_root)
                continue

            if path_exists(backup):
                if content_digest(backup) != record["old_digest"]:
                    raise OSError("backup digest does not match transaction intent")
                if path_exists(target):
                    if content_digest(target) != record["new_digest"]:
                        raise OSError("published content does not match transaction intent")
                    failure_injector("rollback_remove", entry_id)
                    _remove_path(target)
                    fsync_directory_chain(target.parent, global_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                failure_injector("rollback_restore", entry_id)
                os.replace(backup, target)
                fsync_directory_chain(target.parent, global_root)
                if backup.parent != target.parent:
                    fsync_directory(backup.parent)
            elif not path_exists(target) or content_digest(target) != record["old_digest"]:
                raise OSError("approved pre-update content is unavailable")
            restored.append(entry_id)
        except Exception as exc:
            rollback_errors.append(f"{entry_id}: {_bounded_text(exc)}")

    if not rollback_errors:
        for record in records:
            target = global_root / Path(record["relative_path"])
            if content_digest(target) != record["old_digest"]:
                rollback_errors.append(
                    f"{record['entry_id']}: pre-update digest verification failed"
                )

    status = "incomplete" if rollback_errors else "rolled_back"
    evidence: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "selected": selected,
        "published": published,
        "restored": restored,
        "error": _bounded_text(cause),
        "rollback_errors": rollback_errors[:_MAX_TRANSACTION_RECORDS],
        "backup_root": str(backup_root),
    }
    payload["status"] = "rollback_incomplete" if rollback_errors else "rolled_back"
    write_json_durable(journal, payload)
    write_json_durable(transaction / "recovery.json", evidence)
    return evidence


def recover_catalog_transactions(global_root: Path) -> None:
    """Recover incomplete transactions while the caller holds the catalog lock."""
    transactions_root = global_root / ".catalog-transactions"
    if not path_exists(transactions_root):
        return
    _require_real_directory(global_root)
    _require_real_directory(transactions_root, within=global_root)
    for transaction in sorted(transactions_root.iterdir(), key=lambda item: item.name):
        _validate_transaction_location(transaction, global_root)
        journal = transaction / "transaction.json"
        try:
            journal_metadata = journal.lstat()
        except FileNotFoundError:
            evidence_path = transaction / "recovery.json"
            if not path_exists(evidence_path):
                write_json_durable(
                    evidence_path,
                    {
                        "schema_version": 1,
                        "status": "incomplete",
                        "selected": [],
                        "published": [],
                        "restored": [],
                        "error": "durable transaction intent is missing",
                        "rollback_errors": ["catalog state cannot be recovered without a journal"],
                        "backup_root": str(transaction / "backups"),
                    },
                )
            raise CatalogRecoveryError(
                f"Unjournaled catalog transaction requires recovery: {evidence_path}"
            )
        except OSError as exc:
            raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}") from exc
        if not stat.S_ISREG(journal_metadata.st_mode):
            raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}")
        payload = _read_transaction_journal(transaction, global_root)
        _validated_transaction_payload(payload, transaction, global_root)
        status = payload.get("status")
        if status == "committed":
            shutil.rmtree(transaction)
            fsync_directory(transactions_root)
            continue
        if status == "rolled_back":
            continue
        evidence = recover_catalog_transaction(transaction, global_root, _payload=payload)
        if evidence["status"] != "rolled_back":
            raise CatalogRecoveryError(
                f"Catalog transaction recovery is incomplete: {transaction / 'recovery.json'}"
            )
