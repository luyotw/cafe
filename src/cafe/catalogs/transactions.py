"""Durable recovery primitives for Global catalog publication transactions."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from cafe.catalogs.resolver import MAX_CATALOG_OPERATION_ENTRIES


class CatalogRecoveryError(RuntimeError):
    """Raised before catalog reads when an incomplete transaction cannot recover."""


RecoveryInjector = Callable[[str, Optional[str]], None]
_MAX_EVIDENCE_TEXT = 512
_MAX_JOURNAL_BYTES = 1024 * 1024
_COMMITTED_CLEANUP_PREFIX = ".committed-"
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


def _filesystem_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


@dataclass(frozen=True)
class BoundDirectory:
    """A no-follow directory chain held open across a namespace mutation."""

    path: Path
    descriptor: int
    identities: tuple[tuple[Path, tuple[int, int, int]], ...]

    def verify(self) -> None:
        for path, expected in self.identities:
            try:
                current = path.lstat()
            except OSError as exc:
                raise CatalogRecoveryError(
                    f"Catalog directory identity changed before mutation: {path}"
                ) from exc
            if _filesystem_identity(current) != expected:
                raise CatalogRecoveryError(
                    f"Catalog directory identity changed before mutation: {path}"
                )


@contextmanager
def bound_directory(
    root: Path,
    relative: Path,
    *,
    create: bool = False,
) -> Iterator[BoundDirectory]:
    """Open a descendant directory without following a replaceable ancestor."""
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogRecoveryError("Catalog directory path escapes its root")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_before = root.lstat()
        if not stat.S_ISDIR(root_before.st_mode):
            raise CatalogRecoveryError(f"Catalog directory is unsafe: {root}")
        root_descriptor = os.open(root, flags)
    except OSError as exc:
        raise CatalogRecoveryError(f"Catalog directory is unsafe: {root}") from exc
    descriptors = [root_descriptor]
    identities: list[tuple[Path, tuple[int, int, int]]] = []
    try:
        root_identity = _filesystem_identity(root_before)
        if _filesystem_identity(os.fstat(root_descriptor)) != root_identity:
            raise CatalogRecoveryError(f"Catalog directory identity changed: {root}")
        identities.append((root, root_identity))
        current_descriptor = root_descriptor
        current_path = root
        for part in relative.parts:
            if part in {"", "."}:
                continue
            if create:
                try:
                    os.mkdir(part, dir_fd=current_descriptor)
                    os.fsync(current_descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise CatalogRecoveryError(
                        f"Catalog directory cannot be created safely: {current_path / part}"
                    ) from exc
            try:
                before = os.stat(part, dir_fd=current_descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode):
                    raise CatalogRecoveryError(
                        f"Catalog directory is unsafe: {current_path / part}"
                    )
                descriptor = os.open(part, flags, dir_fd=current_descriptor)
            except OSError as exc:
                raise CatalogRecoveryError(
                    f"Catalog directory is unsafe: {current_path / part}"
                ) from exc
            descriptors.append(descriptor)
            identity = _filesystem_identity(before)
            if _filesystem_identity(os.fstat(descriptor)) != identity:
                raise CatalogRecoveryError(
                    f"Catalog directory identity changed: {current_path / part}"
                )
            current_descriptor = descriptor
            current_path /= part
            identities.append((current_path, identity))
        bound = BoundDirectory(current_path, current_descriptor, tuple(identities))
        bound.verify()
        yield bound
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def entry_exists(directory: BoundDirectory, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CatalogRecoveryError(
            f"Catalog entry is unavailable: {directory.path / name}"
        ) from exc
    return True


def move_without_replacement(
    source_name: str,
    destination_name: str,
    *,
    source_directory: BoundDirectory,
    destination_directory: BoundDirectory,
) -> None:
    """Atomically move one entry between bound parents without overwriting."""
    source_directory.verify()
    destination_directory.verify()
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    flags = 1  # Linux RENAME_NOREPLACE
    if rename is None:
        rename = getattr(library, "renameatx_np", None)
        flags = 4  # macOS RENAME_EXCL
    if rename is None:
        raise NotImplementedError("Atomic no-replacement rename is unavailable")
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    result = rename(
        source_directory.descriptor,
        os.fsencode(source_name),
        destination_directory.descriptor,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination_name)
        if error == errno.ENOENT:
            raise FileNotFoundError(error, os.strerror(error), source_name)
        if error in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise NotImplementedError("Filesystem lacks atomic no-replacement rename")
        raise OSError(error, os.strerror(error))
    os.fsync(destination_directory.descriptor)
    if source_directory.descriptor != destination_directory.descriptor:
        os.fsync(source_directory.descriptor)
    source_directory.verify()
    destination_directory.verify()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        or len(raw_records) > MAX_CATALOG_OPERATION_ENTRIES
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
    for record in records:
        _confined_leaf(global_root, Path(record["relative_path"]))
    if payload["status"] == "committed":
        return records
    backup_root = transaction / "backups"
    _require_real_directory(backup_root, within=transaction)
    for record in records:
        relative = Path(record["relative_path"])
        _confined_leaf(backup_root, relative)
    return records


def _validate_committed_content(
    records: list[dict[str, str]], global_root: Path
) -> None:
    from cafe.catalogs.resolver import content_digest

    for record in records:
        target = _confined_leaf(global_root, Path(record["relative_path"]))
        if not path_exists(target) or content_digest(target) != record["new_digest"]:
            raise CatalogRecoveryError(
                "Committed catalog content does not match transaction intent"
            )


def retire_committed_transaction(transaction: Path, global_root: Path) -> None:
    """Atomically remove committed work from the recovery namespace before cleanup."""
    _validate_transaction_location(transaction, global_root)
    transactions_root = transaction.parent
    cleanup = transactions_root / f"{_COMMITTED_CLEANUP_PREFIX}{transaction.name}"
    if path_exists(cleanup):
        raise CatalogRecoveryError(f"Catalog transaction cleanup path exists: {cleanup}")
    os.replace(transaction, cleanup)
    fsync_directory(transactions_root)
    shutil.rmtree(cleanup)
    fsync_directory(transactions_root)


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


def _retain_published_for_recovery(
    target: Path,
    removed: Path,
    *,
    expected_digest: str,
    entry_id: str,
    target_directory: BoundDirectory,
    removed_directory: BoundDirectory,
    failure_injector: RecoveryInjector,
) -> None:
    """Move published bytes to transaction evidence without consuming a replacement."""
    from cafe.catalogs.resolver import content_digest

    if content_digest(target) != expected_digest:
        raise OSError("published content does not match transaction intent")
    if entry_exists(removed_directory, removed.name):
        raise OSError("published removal evidence already exists")
    failure_injector("rollback_remove", entry_id)
    try:
        move_without_replacement(
            target.name,
            removed.name,
            source_directory=target_directory,
            destination_directory=removed_directory,
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise OSError("published content changed before rollback removal") from exc
    removed_directory.verify()
    if content_digest(removed) == expected_digest:
        return
    try:
        move_without_replacement(
            removed.name,
            target.name,
            source_directory=removed_directory,
            destination_directory=target_directory,
        )
    except Exception as exc:
        raise OSError("intervening target was retained as recovery evidence") from exc
    raise OSError("published content changed before rollback removal")


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
    _validate_transaction_location(transaction, global_root)
    try:
        records = _validated_transaction_payload(payload, transaction, global_root)
    except CatalogRecoveryError as exc:
        try:
            evidence_records = _validated_records(payload)
        except CatalogRecoveryError:
            evidence_records = []
        evidence = {
            "schema_version": 1,
            "status": "incomplete",
            "selected": [record["entry_id"] for record in evidence_records],
            "published": [
                record["entry_id"] for record in evidence_records if record["state"] == "published"
            ],
            "restored": [],
            "error": _bounded_text(cause),
            "rollback_errors": [_bounded_text(exc)],
            "backup_root": str(transaction / "backups"),
        }
        write_json_durable(transaction / "recovery.json", evidence)
        return evidence
    backup_root = transaction / "backups"
    try:
        _validate_recovery_content(records, transaction, global_root)
    except CatalogRecoveryError as exc:
        evidence = {
            "schema_version": 1,
            "status": "incomplete",
            "selected": [record["entry_id"] for record in records],
            "published": [
                record["entry_id"] for record in records if record["state"] == "published"
            ],
            "restored": [],
            "error": _bounded_text(cause),
            "rollback_errors": [_bounded_text(exc)],
            "backup_root": str(backup_root),
        }
        payload["status"] = "rollback_incomplete"
        write_json_durable(journal, payload)
        write_json_durable(transaction / "recovery.json", evidence)
        return evidence
    selected = [record["entry_id"] for record in records]
    published = [record["entry_id"] for record in records if record["state"] == "published"]
    restored: list[str] = []
    rollback_errors: list[str] = []
    removed_root = transaction / "removed"

    for record in reversed(records):
        entry_id = record["entry_id"]
        relative = Path(record["relative_path"])
        target = global_root / relative
        backup = backup_root / relative
        removed = removed_root / relative
        try:
            with (
                bound_directory(
                    global_root,
                    relative.parent,
                    create=True,
                ) as target_directory,
                bound_directory(
                    backup_root,
                    relative.parent,
                    create=True,
                ) as backup_directory,
                bound_directory(
                    transaction,
                    Path("removed") / relative.parent,
                    create=True,
                ) as removed_directory,
            ):
                target_exists = entry_exists(target_directory, target.name)
                backup_exists = entry_exists(backup_directory, backup.name)
                removed_exists = entry_exists(removed_directory, removed.name)

                if backup_exists and content_digest(backup) != record["old_digest"]:
                    raise OSError("backup digest does not match transaction intent")
                if removed_exists and content_digest(removed) != record["new_digest"]:
                    raise OSError("removed publication evidence does not match intent")

                if target_exists and backup_exists:
                    _retain_published_for_recovery(
                        target,
                        removed,
                        expected_digest=record["new_digest"],
                        entry_id=entry_id,
                        target_directory=target_directory,
                        removed_directory=removed_directory,
                        failure_injector=failure_injector,
                    )
                    target_exists = False

                if record["old_digest"] == "missing":
                    if backup_exists:
                        raise OSError("unexpected backup for a previously missing entry")
                    if target_exists:
                        _retain_published_for_recovery(
                            target,
                            removed,
                            expected_digest=record["new_digest"],
                            entry_id=entry_id,
                            target_directory=target_directory,
                            removed_directory=removed_directory,
                            failure_injector=failure_injector,
                        )
                    target_directory.verify()
                    if entry_exists(target_directory, target.name):
                        raise OSError("previously missing target was not removed")
                    continue

                if backup_exists:
                    failure_injector("rollback_restore", entry_id)
                    try:
                        move_without_replacement(
                            backup.name,
                            target.name,
                            source_directory=backup_directory,
                            destination_directory=target_directory,
                        )
                    except (FileExistsError, FileNotFoundError) as exc:
                        raise OSError("target changed before rollback restore") from exc
                    except NotImplementedError as exc:
                        raise OSError("atomic rollback restore is unavailable") from exc
                elif not target_exists or content_digest(target) != record["old_digest"]:
                    raise OSError("approved pre-update content is unavailable")

                target_directory.verify()
                if (
                    not entry_exists(target_directory, target.name)
                    or content_digest(target) != record["old_digest"]
                ):
                    raise OSError("pre-update digest verification failed")
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
        "rollback_errors": rollback_errors[:MAX_CATALOG_OPERATION_ENTRIES],
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
        if transaction.name.startswith(_COMMITTED_CLEANUP_PREFIX):
            _validate_transaction_location(transaction, global_root)
            shutil.rmtree(transaction)
            fsync_directory(transactions_root)
            continue
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
        records = _validated_transaction_payload(payload, transaction, global_root)
        status = payload.get("status")
        if status == "committed":
            _validate_committed_content(records, global_root)
            retire_committed_transaction(transaction, global_root)
            continue
        if status == "rolled_back":
            continue
        evidence = recover_catalog_transaction(transaction, global_root, _payload=payload)
        if evidence["status"] != "rolled_back":
            raise CatalogRecoveryError(
                f"Catalog transaction recovery is incomplete: {transaction / 'recovery.json'}"
            )
