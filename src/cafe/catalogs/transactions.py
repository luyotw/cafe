"""Durable recovery primitives for Global catalog publication transactions."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable, Optional


class CatalogRecoveryError(RuntimeError):
    """Raised before catalog reads when an incomplete transaction cannot recover."""


RecoveryInjector = Callable[[str, Optional[str]], None]
_MAX_TRANSACTION_RECORDS = 512
_MAX_EVIDENCE_TEXT = 512


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
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
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


def _validated_records(
    payload: dict[str, object], global_root: Path
) -> list[dict[str, str]]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or len(raw_records) > _MAX_TRANSACTION_RECORDS:
        raise CatalogRecoveryError("Catalog transaction record set is invalid or unbounded")
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            raise CatalogRecoveryError("Catalog transaction contains an invalid record")
        record = {
            "entry_id": str(raw_record.get("entry_id", "")),
            "relative_path": str(raw_record.get("relative_path", "")),
            "old_digest": str(raw_record.get("old_digest", "")),
            "new_digest": str(raw_record.get("new_digest", "")),
            "state": str(raw_record.get("state", "pending")),
        }
        relative = Path(record["relative_path"])
        if (
            not record["entry_id"]
            or record["entry_id"] in seen
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] not in {"playbooks", "skills", "agents"}
            or record["old_digest"] == ""
            or record["new_digest"] == ""
        ):
            raise CatalogRecoveryError("Catalog transaction record is unsafe")
        seen.add(record["entry_id"])
        records.append(record)
    return records


def recover_catalog_transaction(
    transaction: Path,
    global_root: Path,
    *,
    failure_injector: RecoveryInjector = _noop_injector,
    cause: BaseException | str = "interrupted publication",
) -> dict[str, object]:
    """Restore one incomplete publication to its durable pre-update state."""
    from cafe.catalogs.resolver import content_digest

    journal = transaction / "transaction.json"
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}") from exc
    if not isinstance(payload, dict):
        raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}")
    records = _validated_records(payload, global_root)
    backup_root = transaction / "backups"
    selected = [record["entry_id"] for record in records]
    published = [
        record["entry_id"] for record in records if record["state"] == "published"
    ]
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
                    failure_injector("rollback_remove", entry_id)
                    _remove_path(target)
                    fsync_directory_chain(target.parent, global_root)
                continue

            if path_exists(backup):
                if content_digest(backup) != record["old_digest"]:
                    raise OSError("backup digest does not match transaction intent")
                if path_exists(target):
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
    if not transactions_root.is_dir():
        return
    for transaction in sorted(transactions_root.iterdir(), key=lambda item: item.name):
        if not transaction.is_dir():
            continue
        journal = transaction / "transaction.json"
        if not journal.is_file():
            evidence_path = transaction / "recovery.json"
            if not evidence_path.is_file():
                write_json_durable(
                    evidence_path,
                    {
                        "schema_version": 1,
                        "status": "incomplete",
                        "selected": [],
                        "published": [],
                        "restored": [],
                        "error": "durable transaction intent is missing",
                        "rollback_errors": [
                            "catalog state cannot be recovered without a journal"
                        ],
                        "backup_root": str(transaction / "backups"),
                    },
                )
            raise CatalogRecoveryError(
                f"Unjournaled catalog transaction requires recovery: {evidence_path}"
            )
        try:
            payload = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogRecoveryError(
                f"Invalid catalog transaction journal: {journal}"
            ) from exc
        if not isinstance(payload, dict):
            raise CatalogRecoveryError(f"Invalid catalog transaction journal: {journal}")
        status = payload.get("status")
        if status == "committed":
            shutil.rmtree(transaction)
            fsync_directory(transactions_root)
            continue
        if status == "rolled_back":
            continue
        evidence = recover_catalog_transaction(transaction, global_root)
        if evidence["status"] != "rolled_back":
            raise CatalogRecoveryError(
                f"Catalog transaction recovery is incomplete: {transaction / 'recovery.json'}"
            )
