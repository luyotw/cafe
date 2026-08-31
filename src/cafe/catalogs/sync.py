"""Combined comparison and transactional project-to-Global publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

import yaml

from cafe.catalogs.migration import AgentSnapshotMigrator
from cafe.catalogs.resolver import (
    MAX_CATALOG_BYTES,
    MAX_CATALOG_DEPTH,
    MAX_CATALOG_DISCOVERY_ENTRIES,
    MAX_CATALOG_NODES,
    MAX_CATALOG_OPERATION_ENTRIES,
    CatalogEntry,
    CatalogKind,
    CatalogOperationLimitError,
    CatalogResolver,
    CatalogValidationError,
    bounded_directory_names,
    content_digest,
    global_catalog_lock,
    read_valid_agent_definition,
)
from cafe.catalogs.transactions import (
    bound_directory,
    entry_exists,
    entry_identity,
    fsync_directory,
    fsync_tree,
    move_without_replacement,
    recover_catalog_transaction,
    retire_committed_transaction,
    write_json_durable,
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
    effective_digests: dict[str, str]

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
            "effective_digests": self.effective_digests,
            "entries": [item.as_dict() for item in self.entries],
        }


@dataclass(frozen=True)
class OverBudgetDiscovery:
    """One complete, hard-bounded discovery result for an oversized catalog."""

    affected_entry_ids: tuple[str, ...]
    comparison_token: str
    compared_entry_count: int
    effective_digests: dict[str, str]
    discovery_complete: bool = True
    discovery_entry_limit: int = MAX_CATALOG_DISCOVERY_ENTRIES

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "over_budget",
            "entry_limit": MAX_CATALOG_OPERATION_ENTRIES,
            "discovery_entry_limit": self.discovery_entry_limit,
            "discovery_complete": self.discovery_complete,
            "compared_entry_count": self.compared_entry_count,
            "comparison_token": self.comparison_token,
            "effective_digests": self.effective_digests,
            "affected_entry_ids": list(self.affected_entry_ids),
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
    if kind is CatalogKind.AGENT:
        try:
            read_valid_agent_definition(path, key)
        except CatalogValidationError as exc:
            raise CatalogSyncError(str(exc)) from exc
        return
    marker = path / "SKILL.md"
    metadata = _frontmatter(marker)
    expected_name = key
    if metadata.get("name") != expected_name:
        raise CatalogSyncError(
            f"Catalog frontmatter name must match {expected_name!r}: {marker}"
        )


@dataclass
class _CopyBudget:
    source: Path
    nodes: int = 0
    bytes: int = 0

    def add_node(self, depth: int) -> None:
        if depth > MAX_CATALOG_DEPTH:
            raise CatalogSyncError(f"Catalog entry exceeds copy depth limit: {self.source}")
        self.nodes += 1
        if self.nodes > MAX_CATALOG_NODES:
            raise CatalogSyncError(f"Catalog entry exceeds copy node limit: {self.source}")

    def add_bytes(self, size: int) -> None:
        self.bytes += size
        if self.bytes > MAX_CATALOG_BYTES:
            raise CatalogSyncError(f"Catalog entry exceeds copy byte limit: {self.source}")


def _confined_symlink_target(link: Path, authority_root: Path) -> Path:
    target_path = Path(os.readlink(link))
    if not target_path.is_absolute():
        target_path = link.parent / target_path
    try:
        prospective = target_path.resolve(strict=False)
        if not prospective.is_relative_to(authority_root):
            raise CatalogSyncError(f"Catalog symlink target escapes entry authority: {link}")
        target = target_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CatalogSyncError(f"Catalog symlink target is unavailable: {link}") from exc
    if not target.is_relative_to(authority_root):
        raise CatalogSyncError(f"Catalog symlink target escapes entry authority: {link}")
    return target


def _directory_descriptor_path(descriptor: int) -> Path:
    for descriptor_root in (Path("/proc/self/fd"), Path("/dev/fd")):
        candidate = descriptor_root / str(descriptor)
        if candidate.exists():
            return candidate
    raise CatalogSyncError("Descriptor-backed catalog traversal is unavailable")


def _copy_node(
    source: Path,
    destination: Path,
    *,
    authority_root: Path,
    materialize_symlinks: bool,
    budget: _CopyBudget,
    depth: int,
    active_nodes: set[tuple[int, int, int]],
) -> None:
    budget.add_node(depth)
    metadata = source.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        if not materialize_symlinks:
            os.symlink(os.readlink(source), destination)
            return
        target = _confined_symlink_target(source, authority_root)
        target_metadata = target.lstat()
        target_identity = (
            target_metadata.st_dev,
            target_metadata.st_ino,
            stat.S_IFMT(target_metadata.st_mode),
        )
        if target_identity in active_nodes:
            raise CatalogSyncError(f"Catalog symlink cycle cannot be materialized: {source}")
        _copy_node(
            target,
            destination,
            authority_root=authority_root,
            materialize_symlinks=True,
            budget=budget,
            depth=depth,
            active_nodes=active_nodes,
        )
        return

    identity = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
    if identity in active_nodes:
        raise CatalogSyncError(f"Catalog node cycle cannot be materialized: {source}")
    active_nodes.add(identity)
    try:
        if stat.S_ISREG(metadata.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(source, flags)
            try:
                opened = os.fstat(descriptor)
                opened_identity = (
                    opened.st_dev,
                    opened.st_ino,
                    stat.S_IFMT(opened.st_mode),
                )
                if opened_identity != identity:
                    raise StaleComparisonError(f"Catalog source changed while copying: {source}")
                with os.fdopen(descriptor, "rb", closefd=False) as source_handle:
                    with destination.open("xb") as destination_handle:
                        while True:
                            remaining = MAX_CATALOG_BYTES - budget.bytes
                            chunk = source_handle.read(min(65536, remaining + 1))
                            if not chunk:
                                break
                            budget.add_bytes(len(chunk))
                            destination_handle.write(chunk)
                destination.chmod(mode)
            finally:
                os.close(descriptor)
            return
        if stat.S_ISDIR(metadata.st_mode):
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(source, flags)
            try:
                opened = os.fstat(descriptor)
                opened_identity = (
                    opened.st_dev,
                    opened.st_ino,
                    stat.S_IFMT(opened.st_mode),
                )
                if opened_identity != identity:
                    raise StaleComparisonError(f"Catalog source changed while copying: {source}")
                children = bounded_directory_names(
                    descriptor,
                    max_entries=MAX_CATALOG_NODES - budget.nodes,
                    limit_error=lambda: CatalogSyncError(
                        f"Catalog entry exceeds copy node limit: {budget.source}"
                    ),
                )
                destination.mkdir(mode=mode)
                anchored = _directory_descriptor_path(descriptor)
                for name in children:
                    _copy_node(
                        anchored / name,
                        destination / name,
                        authority_root=authority_root,
                        materialize_symlinks=materialize_symlinks,
                        budget=budget,
                        depth=depth + 1,
                        active_nodes=active_nodes,
                    )
                destination.chmod(mode)
            finally:
                os.close(descriptor)
            return
        raise CatalogSyncError(f"Unsupported catalog node: {source}")
    finally:
        active_nodes.remove(identity)


def _copy_entry(source: Path, destination: Path, *, expected_digest: str) -> None:
    try:
        current_digest = content_digest(source)
    except CatalogValidationError as exc:
        raise StaleComparisonError(f"Catalog source changed before staging: {source}") from exc
    if current_digest != expected_digest:
        raise StaleComparisonError(f"Catalog source changed before staging: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.copy-{uuid.uuid4().hex}")
    budget = _CopyBudget(source=source)
    try:
        if source.is_symlink():
            authority_root = source.parent.resolve(strict=True)
            copy_source = _confined_symlink_target(source, authority_root)
            materialize_symlinks = True
        else:
            copy_source = source
            authority_root = (
                source.resolve(strict=True)
                if source.is_dir()
                else source.parent.resolve(strict=True)
            )
            materialize_symlinks = False
        _copy_node(
            copy_source,
            temporary,
            authority_root=authority_root,
            materialize_symlinks=materialize_symlinks,
            budget=budget,
            depth=0,
            active_nodes=set(),
        )
        if content_digest(temporary) != expected_digest:
            raise StaleComparisonError(f"Catalog source changed while staging: {source}")
        os.replace(temporary, destination)
    except BaseException:
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _global_lock(global_root: Path) -> Iterator[None]:
    with global_catalog_lock(global_root, exclusive=True):
        yield


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
        effective_digests: dict[str, str],
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
            "effective_digests": effective_digests,
            "entries": [item.as_dict() for item in entries],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _effective_digests(
        entries: Sequence[CatalogEntry], kinds: Sequence[CatalogKind]
    ) -> dict[str, str]:
        by_kind: dict[CatalogKind, list[dict[str, str]]] = {
            kind: [] for kind in kinds
        }
        for entry in entries:
            by_kind[entry.kind].append(
                {
                    "entry_id": entry.entry_id,
                    "source": entry.source,
                    "digest": entry.digest,
                }
            )
        return {
            kind.value: hashlib.sha256(
                json.dumps(
                    sorted(by_kind[kind], key=lambda item: item["entry_id"]),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            for kind in kinds
        }

    def compare(
        self,
        *,
        kinds: Optional[Iterable[CatalogKind]] = None,
        entry_ids: Optional[Sequence[str]] = None,
    ) -> ComparisonReport:
        operation_limit = (
            MAX_CATALOG_OPERATION_ENTRIES
            if entry_ids is None
            else MAX_CATALOG_DISCOVERY_ENTRIES
        )
        with global_catalog_lock(self.resolver.global_root):
            return self._compare(
                kinds=kinds,
                entry_ids=entry_ids,
                operation_limit=operation_limit,
            )

    def discover_over_budget(
        self,
        *,
        kinds: Optional[Iterable[CatalogKind]] = None,
    ) -> OverBudgetDiscovery:
        """Identify every difference once within the catalog discovery hard limit."""
        with global_catalog_lock(self.resolver.global_root):
            report = self._compare(
                kinds=kinds,
                operation_limit=MAX_CATALOG_DISCOVERY_ENTRIES,
            )
        return OverBudgetDiscovery(
            affected_entry_ids=tuple(item.entry_id for item in report.differences),
            comparison_token=report.token,
            compared_entry_count=len(report.entries),
            effective_digests=report.effective_digests,
        )

    def _compare(
        self,
        *,
        kinds: Optional[Iterable[CatalogKind]] = None,
        entry_ids: Optional[Sequence[str]] = None,
        operation_limit: int = MAX_CATALOG_OPERATION_ENTRIES,
    ) -> ComparisonReport:
        selected_kinds = self._normalize_kinds(kinds)
        if entry_ids is not None and len(entry_ids) > MAX_CATALOG_OPERATION_ENTRIES:
            raise CatalogOperationLimitError(MAX_CATALOG_OPERATION_ENTRIES)
        requested = None if entry_ids is None else set(entry_ids)
        requested_identities: dict[str, tuple[CatalogKind, str]] = {}
        if requested is not None:
            if len(requested) != len(entry_ids):
                raise CatalogSyncError("Duplicate catalog entry filters are not allowed")
            for entry_id in requested:
                kind, key = self._parse_entry_id(entry_id)
                if kind not in selected_kinds:
                    raise CatalogSyncError(f"Entry filter is outside selected kinds: {entry_id}")
                requested_identities[entry_id] = (kind, key)

        effective_entries = self.resolver.entries(
            selected_kinds,
            max_entries=operation_limit,
        )
        project_entries = self.resolver.project_entries(
            selected_kinds,
            max_entries=operation_limit,
        )
        effective_digests = self._effective_digests(effective_entries, selected_kinds)
        blocked_agents = (
            AgentSnapshotMigrator(self.resolver).publication_blocked_entry_ids()
            if CatalogKind.AGENT in selected_kinds
            else set()
        )
        project_entries = [
            entry for entry in project_entries if entry.entry_id not in blocked_agents
        ]
        available = {entry.entry_id for entry in project_entries}
        if requested is not None:
            unknown = sorted(requested - available)
            if unknown:
                raise CatalogSyncError(f"No project catalog entry for: {', '.join(unknown)}")

        scope_items: list[ComparisonItem] = []
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
            scope_items.append(
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
        scope_items.sort(key=lambda item: item.entry_id)
        comparison_items = (
            scope_items
            if requested is None
            else [item for item in scope_items if item.entry_id in requested]
        )
        roots = tuple(
            dict.fromkeys((self.resolver.canonical_root, self.resolver.project_root))
        )
        token = self._comparison_token(
            scope_items,
            effective_digests,
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
            effective_digests=effective_digests,
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
        if len(selected) > MAX_CATALOG_OPERATION_ENTRIES:
            raise CatalogOperationLimitError(MAX_CATALOG_OPERATION_ENTRIES)
        if not selected or len(set(selected)) != len(selected):
            raise CatalogSyncError("Approved entries must be non-empty and unique")
        for entry_id in selected:
            self._parse_entry_id(entry_id)

        selected_kinds = self._normalize_kinds(kinds)
        with _global_lock(self.resolver.global_root):
            blocked = AgentSnapshotMigrator(self.resolver).publication_blocked_entry_ids()
            blocked_selection = sorted(set(selected) & blocked)
            if blocked_selection:
                raise CatalogSyncError(
                    "Agent migration decision required before publication: "
                    + ", ".join(blocked_selection)
                )
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
            transactions_root = transaction.parent
            fsync_directory(transaction)
            fsync_directory(transactions_root)
            fsync_directory(self.resolver.global_root)
            fsync_directory(self.resolver.global_root.parent)
            records: list[dict[str, str]] = []
            for entry_id in selected:
                item = differences[entry_id]
                records.append(
                    {
                        "entry_id": entry_id,
                        "relative_path": item.global_path.relative_to(
                            self.resolver.global_root
                        ).as_posix(),
                        "old_digest": item.global_digest,
                        "new_digest": item.project_digest,
                        "state": "pending",
                    }
                )
            journal = transaction / "transaction.json"
            journal_payload: dict[str, object] = {
                "schema_version": 1,
                "status": "staging",
                "records": records,
            }
            write_json_durable(journal, journal_payload)
            try:
                for entry_id in selected:
                    item = differences[entry_id]
                    relative = item.global_path.relative_to(self.resolver.global_root)
                    staged = staged_root / relative
                    self.failure_injector("stage", entry_id)
                    _copy_entry(
                        item.project_path,
                        staged,
                        expected_digest=item.project_digest,
                    )
                    if content_digest(staged) != item.project_digest:
                        raise CatalogSyncError(f"Staged content changed: {entry_id}")
                    _validate_publishable(CatalogKind(item.kind), item.key, staged)
                fsync_tree(staged_root)
                journal_payload["status"] = "prepared"
                write_json_durable(journal, journal_payload)

                self.failure_injector("pre_publish", None)
                before_publish = self.compare(kinds=selected_kinds, entry_ids=entry_ids)
                if before_publish.token != comparison_token:
                    raise StaleComparisonError(
                        "Catalog contents changed while staging; compare again"
                    )

                journal_payload["status"] = "publishing"
                write_json_durable(journal, journal_payload)
                for index, entry_id in enumerate(selected):
                    item = differences[entry_id]
                    record = records[index]
                    target = item.global_path
                    relative = target.relative_to(self.resolver.global_root)
                    backup = backup_root / relative
                    staged = staged_root / relative
                    with (
                        bound_directory(
                            self.resolver.global_root,
                            relative.parent,
                            create=True,
                        ) as target_directory,
                        bound_directory(
                            backup_root,
                            relative.parent,
                            create=True,
                        ) as backup_directory,
                        bound_directory(staged_root, relative.parent) as staged_directory,
                    ):
                        target_exists = entry_exists(target_directory, target.name)
                        if target_exists and item.global_digest == "missing":
                            raise StaleComparisonError(
                                f"Global content changed during publication: {entry_id}"
                            )
                        if target_exists:
                            target_identity = entry_identity(
                                target_directory,
                                target.name,
                            )
                            if content_digest(target) != item.global_digest:
                                raise StaleComparisonError(
                                    f"Global content changed during publication: {entry_id}"
                                )
                            try:
                                move_without_replacement(
                                    target.name,
                                    backup.name,
                                    source_directory=target_directory,
                                    destination_directory=backup_directory,
                                    expected_source_identity=target_identity,
                                    expected_source_digest=item.global_digest,
                                )
                            except (FileExistsError, FileNotFoundError) as exc:
                                raise StaleComparisonError(
                                    f"Global content changed during publication: {entry_id}"
                                ) from exc
                            except NotImplementedError as exc:
                                raise CatalogSyncError(
                                    "Atomic no-replacement publication is unavailable"
                                ) from exc
                            backup_directory.verify()
                            if content_digest(backup) != item.global_digest:
                                raise StaleComparisonError(
                                    f"Global content changed during publication: {entry_id}"
                                )
                            record["state"] = "backed_up"
                            write_json_durable(journal, journal_payload)
                            self.failure_injector("backed_up", entry_id)
                        elif item.global_digest != "missing":
                            raise StaleComparisonError(
                                f"Global content changed during publication: {entry_id}"
                            )
                        staged_identity = entry_identity(
                            staged_directory,
                            staged.name,
                        )
                        try:
                            move_without_replacement(
                                staged.name,
                                target.name,
                                source_directory=staged_directory,
                                destination_directory=target_directory,
                                expected_source_identity=staged_identity,
                                expected_source_digest=item.project_digest,
                            )
                        except (FileExistsError, FileNotFoundError) as exc:
                            raise StaleComparisonError(
                                f"Global content changed during publication: {entry_id}"
                            ) from exc
                        except NotImplementedError as exc:
                            raise CatalogSyncError(
                                "Atomic no-replacement publication is unavailable"
                            ) from exc
                    record["state"] = "published"
                    write_json_durable(journal, journal_payload)
                    self.failure_injector("published", entry_id)

                self.failure_injector("post_check", None)
                after = self.compare(kinds=selected_kinds, entry_ids=entry_ids)
                remaining = {item.entry_id for item in after.differences}
                failed = [entry_id for entry_id in selected if entry_id in remaining]
                if failed:
                    raise CatalogSyncError(
                        "Post-publication verification failed: " + ", ".join(failed)
                    )
                journal_payload["status"] = "committed"
                write_json_durable(journal, journal_payload)
            except BaseException as exc:
                receipt = transaction / "recovery.json"
                recover_catalog_transaction(
                    transaction,
                    self.resolver.global_root,
                    failure_injector=self.failure_injector,
                    cause=exc,
                )
                if not isinstance(exc, Exception):
                    raise
                raise CatalogSyncError(
                    f"Catalog publication failed; recovery receipt: {receipt}"
                ) from exc

            retire_committed_transaction(transaction, self.resolver.global_root)
            return SyncResult(tuple(selected), after)
