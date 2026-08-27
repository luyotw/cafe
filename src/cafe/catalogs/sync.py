"""Combined comparison and transactional project-to-Global publication."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

import yaml

from cafe.catalogs.resolver import (
    CatalogEntry,
    CatalogKind,
    CatalogResolver,
    CatalogValidationError,
    content_digest,
)


class CatalogSyncError(ValueError):
    """Raised when comparison or publication cannot be completed safely."""


class StaleComparisonError(CatalogSyncError):
    """Raised when approved comparison content changed before publication."""


@dataclass(frozen=True)
class ComparisonItem:
    entry_id: str
    kind: str
    key: str
    effective_source: str
    project_path: Path
    global_path: Path
    project_digest: str
    global_digest: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "key": self.key,
            "effective_source": self.effective_source,
            "project_path": str(self.project_path),
            "global_path": str(self.global_path),
            "project_digest": self.project_digest,
            "global_digest": self.global_digest,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComparisonReport:
    status: str
    token: str
    entries: tuple[ComparisonItem, ...]
    project_roots: tuple[Path, ...]
    global_root: Path

    @property
    def differences(self) -> tuple[ComparisonItem, ...]:
        return tuple(item for item in self.entries if item.reason != "identical")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "comparison_token": self.token,
            "project_roots": [str(path) for path in self.project_roots],
            "global_root": str(self.global_root),
            "compared_count": len(self.entries),
            "difference_count": len(self.differences),
            "entries": [item.as_dict() for item in self.entries],
        }


@dataclass(frozen=True)
class SyncResult:
    updated: tuple[str, ...]
    comparison: ComparisonReport

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "updated",
            "updated": list(self.updated),
            "comparison": self.comparison.as_dict(),
        }


FailureInjector = Callable[[str, Optional[str]], None]


def _noop_injector(_boundary: str, _entry_id: Optional[str]) -> None:
    return None


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise CatalogSyncError(f"Catalog entry is missing YAML frontmatter: {path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise CatalogSyncError(f"Catalog entry has unterminated YAML frontmatter: {path}")
    try:
        metadata = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise CatalogSyncError(f"Catalog entry has invalid YAML frontmatter: {path}") from exc
    if not isinstance(metadata, dict):
        raise CatalogSyncError(f"Catalog frontmatter must be a mapping: {path}")
    return metadata


def _validate_publishable(kind: CatalogKind, key: str, path: Path) -> None:
    if kind is CatalogKind.PLAYBOOK:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise CatalogSyncError(f"Invalid playbook {key}: {path}") from exc
        if not isinstance(document, dict):
            raise CatalogSyncError(f"Invalid playbook {key}: expected a mapping")
        return
    marker = path / "SKILL.md" if kind is CatalogKind.PHASE else path
    metadata = _frontmatter(marker)
    expected_name = key if kind is CatalogKind.PHASE else key.split("/", 1)[1]
    if metadata.get("name") != expected_name:
        raise CatalogSyncError(
            f"Catalog frontmatter name must match {expected_name!r}: {marker}"
        )


def _copy_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


@contextmanager
def _global_lock(global_root: Path) -> Iterator[None]:
    global_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(global_root / ".catalog-sync.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class CatalogSyncService:
    """Compare all project catalogs and publish an approved selection atomically."""

    def __init__(
        self,
        resolver: CatalogResolver,
        *,
        failure_injector: FailureInjector = _noop_injector,
    ) -> None:
        self.resolver = resolver
        self.failure_injector = failure_injector

    @staticmethod
    def _normalize_kinds(kinds: Optional[Iterable[CatalogKind]]) -> tuple[CatalogKind, ...]:
        selected = tuple(kinds or CatalogKind)
        if len(set(selected)) != len(selected):
            raise CatalogSyncError("Duplicate catalog kinds are not allowed")
        return selected

    @staticmethod
    def _parse_entry_id(entry_id: str) -> tuple[CatalogKind, str]:
        try:
            prefix, key = entry_id.split(":", 1)
            kind = CatalogKind(prefix)
            CatalogResolver._validate_key(kind, key)
        except (ValueError, AttributeError, CatalogValidationError) as exc:
            raise CatalogSyncError(f"Invalid catalog entry ID: {entry_id!r}") from exc
        return kind, key

    def _global_path(self, entry: CatalogEntry) -> Path:
        root = self.resolver.global_root / self.resolver._DIRECTORIES[entry.kind]
        return self.resolver.candidate_path(entry.kind, entry.key, root)

    @staticmethod
    def _comparison_token(
        entries: Sequence[ComparisonItem],
        roots: tuple[Path, ...],
        global_root: Path,
        kinds: Sequence[CatalogKind],
        scope_ids: Optional[Sequence[str]],
    ) -> str:
        payload = {
            "schema_version": 1,
            "project_roots": [str(path) for path in roots],
            "global_root": str(global_root),
            "kinds": [kind.value for kind in kinds],
            "entry_scope": sorted(scope_ids) if scope_ids is not None else None,
            "entries": [item.as_dict() for item in entries],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def compare(
        self,
        *,
        kinds: Optional[Iterable[CatalogKind]] = None,
        entry_ids: Optional[Sequence[str]] = None,
    ) -> ComparisonReport:
        selected_kinds = self._normalize_kinds(kinds)
        requested = None if entry_ids is None else set(entry_ids)
        if requested is not None:
            if len(requested) != len(entry_ids):
                raise CatalogSyncError("Duplicate catalog entry filters are not allowed")
            for entry_id in requested:
                kind, _key = self._parse_entry_id(entry_id)
                if kind not in selected_kinds:
                    raise CatalogSyncError(f"Entry filter is outside selected kinds: {entry_id}")

        project_entries = self.resolver.project_entries(selected_kinds)
        available = {entry.entry_id for entry in project_entries}
        if requested is not None:
            unknown = sorted(requested - available)
            if unknown:
                raise CatalogSyncError(f"No project catalog entry for: {', '.join(unknown)}")
            project_entries = [entry for entry in project_entries if entry.entry_id in requested]

        comparison_items: list[ComparisonItem] = []
        for entry in project_entries:
            _validate_publishable(entry.kind, entry.key, entry.path)
            global_path = self._global_path(entry)
            if not global_path.exists() and not global_path.is_symlink():
                global_digest = "missing"
                reason = "missing_global"
            else:
                global_digest = content_digest(global_path)
                try:
                    _validate_publishable(entry.kind, entry.key, global_path)
                except (CatalogSyncError, CatalogValidationError, OSError):
                    reason = "invalid_global"
                else:
                    reason = (
                        "identical" if global_digest == entry.digest else "content_mismatch"
                    )
            comparison_items.append(
                ComparisonItem(
                    entry_id=entry.entry_id,
                    kind=entry.kind.value,
                    key=entry.key,
                    effective_source=entry.source,
                    project_path=entry.path,
                    global_path=global_path,
                    project_digest=entry.digest,
                    global_digest=global_digest,
                    reason=reason,
                )
            )
        comparison_items.sort(key=lambda item: item.entry_id)
        roots = tuple(
            dict.fromkeys((self.resolver.canonical_root, self.resolver.project_root))
        )
        token = self._comparison_token(
            comparison_items,
            roots,
            self.resolver.global_root,
            selected_kinds,
            entry_ids,
        )
        status = (
            "no_project_entries"
            if not comparison_items
            else (
                "differences"
                if any(item.reason != "identical" for item in comparison_items)
                else "identical"
            )
        )
        return ComparisonReport(
            status=status,
            token=token,
            entries=tuple(comparison_items),
            project_roots=roots,
            global_root=self.resolver.global_root,
        )

    def _validate_selection(
        self, report: ComparisonReport, selected: Sequence[str]
    ) -> dict[str, ComparisonItem]:
        if not selected:
            raise CatalogSyncError("Publication requires at least one approved entry")
        if len(set(selected)) != len(selected):
            raise CatalogSyncError("Duplicate approved entries are not allowed")
        for entry_id in selected:
            self._parse_entry_id(entry_id)
        differences = {item.entry_id: item for item in report.differences}
        unknown = [entry_id for entry_id in selected if entry_id not in differences]
        if unknown:
            raise CatalogSyncError(
                "Approved entries are unknown or already identical: " + ", ".join(unknown)
            )
        return differences

    def sync(
        self,
        comparison_token: str,
        selected: Sequence[str],
        *,
        kinds: Optional[Iterable[CatalogKind]] = None,
        entry_ids: Optional[Sequence[str]] = None,
    ) -> SyncResult:
        """Publish only selected entries after a lock-time comparison recheck."""
        if len(comparison_token) != 64 or any(
            character not in "0123456789abcdef" for character in comparison_token
        ):
            raise CatalogSyncError("A valid comparison token is required")
        if not selected or len(set(selected)) != len(selected):
            raise CatalogSyncError("Approved entries must be non-empty and unique")
        for entry_id in selected:
            self._parse_entry_id(entry_id)

        selected_kinds = self._normalize_kinds(kinds)
        with _global_lock(self.resolver.global_root):
            current = self.compare(kinds=selected_kinds, entry_ids=entry_ids)
            if current.token != comparison_token:
                raise StaleComparisonError(
                    "Catalog contents changed after approval; run a fresh comparison"
                )
            differences = self._validate_selection(current, selected)
            transaction = (
                self.resolver.global_root / ".catalog-transactions" / uuid.uuid4().hex
            )
            staged_root = transaction / "staged"
            backup_root = transaction / "backups"
            staged_root.mkdir(parents=True)
            backup_root.mkdir(parents=True)
            published: list[str] = []
            backed_up: list[str] = []
            try:
                for entry_id in selected:
                    item = differences[entry_id]
                    relative = item.global_path.relative_to(self.resolver.global_root)
                    staged = staged_root / relative
                    self.failure_injector("stage", entry_id)
                    _copy_entry(item.project_path, staged)
                    _validate_publishable(CatalogKind(item.kind), item.key, staged)
                    if content_digest(staged) != item.project_digest:
                        raise CatalogSyncError(f"Staged content changed: {entry_id}")

                self.failure_injector("pre_publish", None)
                before_publish = self.compare(kinds=selected_kinds, entry_ids=entry_ids)
                if before_publish.token != comparison_token:
                    raise StaleComparisonError(
                        "Catalog contents changed while staging; compare again"
                    )

                for entry_id in selected:
                    item = differences[entry_id]
                    target = item.global_path
                    relative = target.relative_to(self.resolver.global_root)
                    backup = backup_root / relative
                    staged = staged_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() or target.is_symlink():
                        os.replace(target, backup)
                        backed_up.append(entry_id)
                        if content_digest(backup) != item.global_digest:
                            raise StaleComparisonError(
                                f"Global content changed during publication: {entry_id}"
                            )
                    elif item.global_digest != "missing":
                        raise StaleComparisonError(
                            f"Global content changed during publication: {entry_id}"
                        )
                    os.replace(staged, target)
                    published.append(entry_id)
                    self.failure_injector("published", entry_id)

                self.failure_injector("post_check", None)
                after = self.compare(kinds=selected_kinds, entry_ids=entry_ids)
                remaining = {item.entry_id for item in after.differences}
                failed = [entry_id for entry_id in selected if entry_id in remaining]
                if failed:
                    raise CatalogSyncError(
                        "Post-publication verification failed: " + ", ".join(failed)
                    )
            except Exception as exc:
                rollback_errors: list[str] = []
                restored: list[str] = []
                for entry_id in reversed(published):
                    try:
                        self.failure_injector("rollback_remove", entry_id)
                        _remove_path(differences[entry_id].global_path)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"remove {entry_id}: {rollback_exc}")
                for entry_id in reversed(backed_up):
                    target = differences[entry_id].global_path
                    relative = target.relative_to(self.resolver.global_root)
                    backup = backup_root / relative
                    try:
                        self.failure_injector("rollback_restore", entry_id)
                        if target.exists() or target.is_symlink():
                            raise OSError("rollback target still exists")
                        os.replace(backup, target)
                        restored.append(entry_id)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"restore {entry_id}: {rollback_exc}")
                receipt = transaction / "recovery.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "incomplete" if rollback_errors else "rolled_back",
                            "selected": list(selected),
                            "published": published,
                            "restored": restored,
                            "error": str(exc),
                            "rollback_errors": rollback_errors,
                            "backup_root": str(backup_root),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                raise CatalogSyncError(
                    f"Catalog publication failed; recovery receipt: {receipt}"
                ) from exc

            shutil.rmtree(transaction)
            return SyncResult(tuple(selected), after)
