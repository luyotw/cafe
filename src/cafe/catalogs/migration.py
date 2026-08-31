"""Conservative migration for project agent files created by legacy preparation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from cafe.catalogs.resolver import (
    CatalogKind,
    CatalogResolver,
    CatalogValidationError,
    content_digest,
    global_catalog_lock,
    read_valid_agent_definition,
)
from cafe.catalogs.transactions import write_json_durable

_MANIFEST_VERSION = 2
_MANIFEST_OPERATION = "agent_snapshot_migration"
_MANIFEST_FIELDS = {
    "version",
    "operation",
    "token",
    "canonical_root",
    "project_root",
    "transaction_root",
    "status",
    "items",
    "retired",
    "preserved",
}
_RECORD_FIELDS = {
    "entry_id",
    "path",
    "digest",
    "fallback_digest",
    "status",
    "effect",
    "action",
    "retired_path",
    "state",
}


class StaleMigrationDecision(RuntimeError):
    """Raised when migration approval no longer matches current content."""


class MigrationDecisionError(ValueError):
    """Raised when a migration selection is incomplete or invalid."""


@dataclass(frozen=True)
class MigrationItem:
    entry_id: str
    path: Path
    digest: str
    fallback_digest: str
    status: str
    effect: str = "shadows_fallback"


@dataclass(frozen=True)
class MigrationPreview:
    token: str
    items: tuple[MigrationItem, ...]


@dataclass(frozen=True)
class MigrationResult:
    retired: tuple[Path, ...]
    preserved: tuple[Path, ...]
    manifest: Path


TrackedCheck = Callable[[Path], bool]
FailureInjector = Callable[[str, Optional[str]], None]


def _noop_injector(_boundary: str, _entry_id: Optional[str]) -> None:
    return None


def _default_is_tracked(path: Path) -> bool:
    project_root = path
    while project_root != project_root.parent and not (project_root / ".git").exists():
        project_root = project_root.parent
    if not (project_root / ".git").exists():
        return False
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return False
    result = subprocess.run(
        ("git", "ls-files", "--error-unmatch", "--", relative.as_posix()),
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _filesystem_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


def _open_file_digest(descriptor: int, mode: int) -> str:
    digest = hashlib.sha256()
    digest.update(f"F\0.\0{stat.S_IMODE(mode):o}\0".encode())
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 65536), b""):
        digest.update(chunk)
    digest.update(b"\0")
    return digest.hexdigest()


def _retired_content_digest(path: Path, source: Path) -> str:
    """Digest a retired root symlink as if it remained at its approved source."""
    if path.is_symlink() and path != source:
        return content_digest(path, root_symlink_base=source.parent)
    return content_digest(path)


@contextmanager
def _bound_agent_source(path: Path, expected_digest: str) -> Iterator[tuple[int, int, int]]:
    """Hold and validate the approved filesystem object through retirement."""
    before = path.lstat()
    identity = _filesystem_identity(before)
    if stat.S_ISLNK(before.st_mode):
        if content_digest(path) != expected_digest:
            raise StaleMigrationDecision("Agent source changed before retirement")
        after = path.lstat()
        if _filesystem_identity(after) != identity:
            raise StaleMigrationDecision("Agent source identity changed before retirement")
        yield identity
        return
    if not stat.S_ISREG(before.st_mode):
        raise StaleMigrationDecision("Agent source is not a regular file or symlink")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened_before = os.fstat(descriptor)
        if _filesystem_identity(opened_before) != identity:
            raise StaleMigrationDecision("Agent source identity changed before retirement")
        if _open_file_digest(descriptor, opened_before.st_mode) != expected_digest:
            raise StaleMigrationDecision("Agent source changed before retirement")
        opened_after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _filesystem_identity(opened_after) != identity
            or opened_after.st_size != opened_before.st_size
            or opened_after.st_mtime_ns != opened_before.st_mtime_ns
            or _filesystem_identity(current) != identity
        ):
            raise StaleMigrationDecision("Agent source changed while being verified")
        yield identity
        if _filesystem_identity(os.fstat(descriptor)) != identity:
            raise StaleMigrationDecision("Retired agent identity changed during migration")
    finally:
        os.close(descriptor)


class AgentSnapshotMigrator:
    """Preview and recoverably retire legacy project agent snapshots."""

    def __init__(
        self,
        resolver: CatalogResolver,
        *,
        is_tracked: TrackedCheck = _default_is_tracked,
        failure_injector: FailureInjector = _noop_injector,
    ) -> None:
        self.resolver = resolver
        self.is_tracked = is_tracked
        self.failure_injector = failure_injector

    @staticmethod
    def _valid_agent(path: Path, expected_name: str) -> bool:
        try:
            role = path.parent.name
            read_valid_agent_definition(path, f"{role}/{expected_name}")
        except CatalogValidationError:
            return False
        return True

    def _project_agent_candidates(self) -> Iterator[tuple[str, Path, str]]:
        project_roots = [
            item for item in self.resolver.catalog_roots(CatalogKind.AGENT) if item[0] == "project"
        ]
        keys: set[str] = set()
        for _source, root, _layer in project_roots:
            keys.update(self.resolver._keys_at_root(CatalogKind.AGENT, root))
        for key in sorted(keys):
            for _source, root, _layer in reversed(project_roots):
                path = self.resolver.candidate_path(CatalogKind.AGENT, key, root)
                if path.exists() or path.is_symlink():
                    yield key, path, content_digest(path)
                    break

    def _fallback_path(self, key: str) -> Optional[Path]:
        roots = [
            item for item in self.resolver.catalog_roots(CatalogKind.AGENT) if item[0] != "project"
        ]
        for _source, root, _layer in reversed(roots):
            path = self.resolver.candidate_path(CatalogKind.AGENT, key, root)
            if path.exists() or path.is_symlink():
                return path
        return None

    def _fallback_digest(self, key: str) -> str:
        path = self._fallback_path(key)
        return content_digest(path) if path is not None else "missing"

    def _fallback_identity_is_current(
        self,
        key: str,
        path: Optional[Path],
        digest: str,
        identity: Optional[tuple[int, int, int]],
    ) -> bool:
        current = self._fallback_path(key)
        if path is None:
            return current is None and digest == "missing"
        if current != path or identity is None:
            return False
        try:
            return (
                _filesystem_identity(current.lstat()) == identity
                and content_digest(current) == digest
            )
        except (OSError, CatalogValidationError):
            return False

    def _classification_status(
        self,
        key: str,
        content_path: Path,
        digest: str,
        fallback_digest: str,
        *,
        tracked_path: Optional[Path] = None,
    ) -> str:
        expected_name = key.split("/", 1)[1]
        if not self._valid_agent(content_path, expected_name):
            return "invalid"
        if self.is_tracked(tracked_path or content_path):
            return "intentional"
        if fallback_digest != "missing" and digest == fallback_digest:
            return "generated"
        return "ambiguous"

    def _token(
        self,
        items: list[MigrationItem],
        *,
        canonical_root: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ) -> str:
        payload = {
            "canonical_root": str(canonical_root or self.resolver.canonical_root),
            "project_root": str(project_root or self.resolver.project_root),
            "items": [
                {
                    "entry_id": item.entry_id,
                    "path": str(item.path),
                    "digest": item.digest,
                    "fallback_digest": item.fallback_digest,
                    "status": item.status,
                }
                for item in items
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def preview(self) -> MigrationPreview:
        with global_catalog_lock(self.resolver.global_root):
            return self._preview()

    def _preview(self) -> MigrationPreview:
        items: list[MigrationItem] = []
        confirmed = self._confirmed_preserved()
        for key, path, digest in self._project_agent_candidates():
            entry_id = f"agent:{key}"
            fallback_digest = self._fallback_digest(key)
            status = self._classification_status(key, path, digest, fallback_digest)
            if (entry_id, digest) in confirmed:
                continue
            items.append(
                MigrationItem(
                    entry_id=entry_id,
                    path=path,
                    digest=digest,
                    fallback_digest=fallback_digest,
                    status=status,
                )
            )
        items.sort(key=lambda item: item.entry_id)
        return MigrationPreview(token=self._token(items), items=tuple(items))

    def _transaction_root(self, token: str) -> Path:
        return (
            self.resolver.canonical_root / ".cafe" / "migrations" / "agent-snapshots" / token[:16]
        )

    @staticmethod
    def _result_from_manifest(manifest: Path, payload: Mapping[str, object]) -> MigrationResult:
        return MigrationResult(
            retired=tuple(Path(item) for item in payload.get("retired", [])),
            preserved=tuple(Path(item) for item in payload.get("preserved", [])),
            manifest=manifest,
        )

    @staticmethod
    def _validate_directory_ancestry(root: Path, directory: Path, *, allow_missing: bool) -> None:
        try:
            relative = directory.relative_to(root)
        except ValueError as exc:
            raise StaleMigrationDecision(
                "Migration path is outside its authorized project root"
            ) from exc

        try:
            root_metadata = root.lstat()
        except OSError as exc:
            raise StaleMigrationDecision("Migration project root is unavailable") from exc
        if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
            raise StaleMigrationDecision("Migration project root is unsafe")
        resolved_root = root.resolve(strict=True)

        current = root
        missing = False
        for part in relative.parts:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                missing = True
                if allow_missing:
                    continue
                raise StaleMigrationDecision("Migration directory ancestry is unavailable")
            except OSError as exc:
                raise StaleMigrationDecision("Migration directory ancestry is unavailable") from exc
            if missing or not stat.S_ISDIR(metadata.st_mode) or current.is_symlink():
                raise StaleMigrationDecision("Migration directory ancestry is unsafe")
            try:
                if not current.resolve(strict=True).is_relative_to(resolved_root):
                    raise StaleMigrationDecision(
                        "Migration directory ancestry escapes its project root"
                    )
            except OSError as exc:
                raise StaleMigrationDecision("Migration directory ancestry is unavailable") from exc

    @classmethod
    def _ensure_safe_directory(cls, root: Path, directory: Path) -> None:
        cls._validate_directory_ancestry(root, directory, allow_missing=True)
        relative = directory.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            cls._validate_directory_ancestry(root, current, allow_missing=False)

    def _source_root(self, entry_id: str, source: Path) -> tuple[Path, str]:
        if not entry_id.startswith("agent:"):
            raise StaleMigrationDecision("Migration manifest entry identity is invalid")
        key = entry_id.removeprefix("agent:")
        try:
            self.resolver._validate_key(CatalogKind.AGENT, key)
        except CatalogValidationError as exc:
            raise StaleMigrationDecision("Migration manifest entry identity is invalid") from exc
        for project_root in dict.fromkeys(
            (self.resolver.canonical_root, self.resolver.project_root)
        ):
            expected = self.resolver.candidate_path(
                CatalogKind.AGENT,
                key,
                project_root / ".cafe" / "agents",
            )
            if source == expected:
                return project_root, key
        raise StaleMigrationDecision(
            "Migration manifest source does not match the active project view"
        )

    def _validate_record_paths(
        self,
        records: list[dict[str, object]],
        transaction_root: Path,
        *,
        allow_missing_destinations: bool,
    ) -> None:
        for record in records:
            entry_id = str(record["entry_id"])
            source = Path(str(record["path"]))
            source_root, key = self._source_root(entry_id, source)
            self._validate_directory_ancestry(source_root, source.parent, allow_missing=False)
            action = str(record["action"])
            retired_path = record.get("retired_path")
            if action == "preserve":
                if retired_path is not None:
                    raise StaleMigrationDecision(
                        "Preserved migration record has a retirement target"
                    )
                continue
            role, name = key.split("/", 1)
            expected = transaction_root / "retired" / role / f"{name}.md"
            if retired_path is None or Path(str(retired_path)) != expected:
                raise StaleMigrationDecision(
                    "Migration retirement target does not match its operation"
                )
            self._validate_directory_ancestry(
                self.resolver.canonical_root,
                expected.parent,
                allow_missing=allow_missing_destinations,
            )

    def _read_manifest(self, manifest: Path) -> dict[str, object]:
        self._validate_directory_ancestry(
            self.resolver.canonical_root, manifest.parent, allow_missing=False
        )
        try:
            metadata = manifest.lstat()
            if not stat.S_ISREG(metadata.st_mode) or manifest.is_symlink():
                raise StaleMigrationDecision("Migration manifest path is unsafe")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except StaleMigrationDecision:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StaleMigrationDecision("Migration manifest is unreadable") from exc
        if not isinstance(payload, dict):
            raise StaleMigrationDecision("Migration manifest schema is invalid")
        return payload

    def _validate_manifest(
        self,
        manifest: Path,
        payload: dict[str, object],
        token: str,
        decisions: Mapping[str, str],
        *,
        share_canonical_preserves: bool = False,
    ) -> list[dict[str, object]]:
        transaction_root = self._transaction_root(token)
        if manifest != transaction_root / "manifest.json" or set(payload) != _MANIFEST_FIELDS:
            raise StaleMigrationDecision("Migration manifest identity is invalid")
        if (
            type(payload["version"]) is not int
            or payload["version"] != _MANIFEST_VERSION
            or payload["operation"] != _MANIFEST_OPERATION
            or payload["token"] != token
            or payload["canonical_root"] != str(self.resolver.canonical_root)
            or payload["transaction_root"] != str(transaction_root)
            or payload["status"] not in {"in_progress", "completed"}
        ):
            raise StaleMigrationDecision("Migration manifest identity is stale")

        records = payload["items"]
        if not isinstance(records, list) or any(
            not isinstance(record, dict) or set(record) != _RECORD_FIELDS for record in records
        ):
            raise StaleMigrationDecision("Migration manifest records are invalid")
        typed_records = [record for record in records if isinstance(record, dict)]
        recorded_items: list[MigrationItem] = []
        recorded_decisions: dict[str, str] = {}
        seen: set[str] = set()
        for record in typed_records:
            entry_id = record["entry_id"]
            action = record["action"]
            state = record["state"]
            if (
                not isinstance(entry_id, str)
                or entry_id in seen
                or action not in {"preserve", "retire"}
                or state not in {"pending", "retiring", "completed"}
                or record["effect"] != "shadows_fallback"
                or record["status"] not in {"invalid", "intentional", "generated", "ambiguous"}
            ):
                raise StaleMigrationDecision("Migration manifest record is stale")
            digest = record["digest"]
            fallback_digest = record["fallback_digest"]
            if not isinstance(digest, str) or not isinstance(fallback_digest, str):
                raise StaleMigrationDecision("Migration manifest digest is invalid")
            if not (
                len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                and (
                    fallback_digest == "missing"
                    or (
                        len(fallback_digest) == 64
                        and all(character in "0123456789abcdef" for character in fallback_digest)
                    )
                )
            ):
                raise StaleMigrationDecision("Migration manifest digest is invalid")
            seen.add(entry_id)
            recorded_decisions[entry_id] = str(action)
            recorded_items.append(
                MigrationItem(
                    entry_id=entry_id,
                    path=Path(str(record["path"])),
                    digest=digest,
                    fallback_digest=fallback_digest,
                    status=str(record["status"]),
                    effect="shadows_fallback",
                )
            )
        if [item.entry_id for item in recorded_items] != sorted(seen):
            raise StaleMigrationDecision("Migration manifest record order is invalid")
        if recorded_decisions != dict(decisions):
            raise MigrationDecisionError("Migration decisions do not match the migration journal")
        recorded_project_root = Path(str(payload["project_root"]))
        project_view_matches = recorded_project_root == self.resolver.project_root
        canonical_preserve = (
            share_canonical_preserves
            and payload["status"] == "completed"
            and all(record["action"] == "preserve" for record in typed_records)
            and all(
                item.path
                == self.resolver.candidate_path(
                    CatalogKind.AGENT,
                    item.entry_id.removeprefix("agent:"),
                    self.resolver.canonical_root / ".cafe" / "agents",
                )
                for item in recorded_items
            )
        )
        if not project_view_matches and not canonical_preserve:
            raise StaleMigrationDecision("Migration manifest project view is stale")
        if self._token(recorded_items, project_root=recorded_project_root) != token:
            raise StaleMigrationDecision("Migration manifest does not match its preview")
        self._validate_record_paths(
            typed_records,
            transaction_root,
            allow_missing_destinations=False,
        )

        retired = payload["retired"]
        preserved = payload["preserved"]
        if not isinstance(retired, list) or not isinstance(preserved, list):
            raise StaleMigrationDecision("Migration manifest result is invalid")
        expected_retired = [
            str(record["retired_path"]) for record in typed_records if record["action"] == "retire"
        ]
        expected_preserved = [
            str(record["path"]) for record in typed_records if record["action"] == "preserve"
        ]
        if payload["status"] == "completed":
            if any(record["state"] != "completed" for record in typed_records):
                raise StaleMigrationDecision("Migration manifest progress is invalid")
            if retired != expected_retired or preserved != expected_preserved:
                raise StaleMigrationDecision("Migration manifest result is stale")
        elif retired or preserved:
            raise StaleMigrationDecision("In-progress migration contains final results")

        if payload["status"] == "completed":
            return typed_records

        current_items: list[MigrationItem] = []
        for item, record in zip(recorded_items, typed_records):
            source = item.path
            destination = (
                Path(str(record["retired_path"])) if record["action"] == "retire" else None
            )
            if self._record_path_exists(source):
                content_path = source
                status = self._classification_status(
                    item.entry_id.removeprefix("agent:"),
                    source,
                    content_digest(source),
                    self._fallback_digest(item.entry_id.removeprefix("agent:")),
                )
            elif destination is not None and self._record_path_exists(destination):
                content_path = destination
                status = item.status
            else:
                raise StaleMigrationDecision("Migration manifest content is unavailable")
            digest = _retired_content_digest(content_path, source)
            fallback_digest = (
                item.fallback_digest
                if destination is not None
                and not self._record_path_exists(source)
                and self._record_path_exists(destination)
                else self._fallback_digest(item.entry_id.removeprefix("agent:"))
            )
            current_items.append(
                MigrationItem(
                    entry_id=item.entry_id,
                    path=item.path,
                    digest=digest,
                    fallback_digest=fallback_digest,
                    status=status,
                )
            )
        if self._token(current_items, project_root=recorded_project_root) != token:
            raise StaleMigrationDecision("Migration manifest content is stale")
        return typed_records

    def _confirmed_preserved(self) -> set[tuple[str, str]]:
        roots = dict.fromkeys((self.resolver.canonical_root, self.resolver.project_root))
        confirmed: set[tuple[str, str]] = set()
        for project_root in roots:
            root = project_root / ".cafe" / "migrations" / "agent-snapshots"
            if not root.exists() and not root.is_symlink():
                continue
            try:
                self._validate_directory_ancestry(project_root, root, allow_missing=False)
            except StaleMigrationDecision:
                continue
            for manifest in sorted(root.glob("*/manifest.json")):
                try:
                    payload = self._read_manifest(manifest)
                    records = payload.get("items")
                    if not isinstance(records, list):
                        continue
                    decisions = {
                        str(record.get("entry_id")): str(record.get("action"))
                        for record in records
                        if isinstance(record, dict)
                    }
                    self._validate_manifest(
                        manifest,
                        payload,
                        str(payload.get("token")),
                        decisions,
                        share_canonical_preserves=True,
                    )
                except (StaleMigrationDecision, MigrationDecisionError):
                    continue
                if payload.get("status") != "completed":
                    continue
                for record in payload.get("items", []):
                    if isinstance(record, dict):
                        self._validate_completed_record(record)
                    if isinstance(record, dict) and record.get("action") == "preserve":
                        confirmed.add(
                            (
                                str(record.get("entry_id")),
                                str(record.get("digest")),
                            )
                        )
        return confirmed

    def publication_blocked_entry_ids(self) -> set[str]:
        """Return unresolved snapshots that cannot safely be published."""
        return {
            item.entry_id
            for item in self.preview().items
            if item.status in {"generated", "ambiguous"}
        }

    @staticmethod
    def _record_path_exists(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def _validate_completed_record(self, record: dict[str, object]) -> None:
        entry_id = str(record["entry_id"])
        source = Path(str(record["path"]))
        digest = str(record["digest"])
        action = str(record["action"])
        if action == "preserve":
            if not self._record_path_exists(source) or content_digest(source) != digest:
                raise StaleMigrationDecision(
                    f"Preserved agent changed after checkpoint: {entry_id}"
                )
            return
        if action != "retire":
            raise MigrationDecisionError(
                f"Migration journal contains an invalid action: {entry_id}"
            )
        destination = Path(str(record["retired_path"]))
        if (
            self._record_path_exists(source)
            or not self._record_path_exists(destination)
            or _retired_content_digest(destination, source) != digest
        ):
            raise StaleMigrationDecision(
                f"Retired agent recovery evidence changed after checkpoint: {entry_id}"
            )

    def _write_manifest(
        self,
        manifest: Path,
        payload: dict[str, object],
        *,
        entry_id: Optional[str],
    ) -> None:
        self.failure_injector("before_manifest_write", entry_id)
        self._validate_directory_ancestry(
            self.resolver.canonical_root, manifest.parent, allow_missing=False
        )
        write_json_durable(manifest, payload)

    def _retire_identity_bound(
        self,
        source: Path,
        destination: Path,
        digest: str,
        fallback_digest: str,
        entry_id: str,
    ) -> None:
        source_root, key = self._source_root(entry_id, source)
        self._validate_directory_ancestry(source_root, source.parent, allow_missing=False)
        self._validate_directory_ancestry(
            self.resolver.canonical_root,
            destination.parent,
            allow_missing=False,
        )
        with global_catalog_lock(self.resolver.global_root):
            with _bound_agent_source(source, digest) as approved_identity:
                self.failure_injector("before_retire", entry_id)
                if self._fallback_digest(key) != fallback_digest:
                    raise StaleMigrationDecision(
                        f"Agent fallback changed before retirement: {entry_id}"
                    )
                fallback_path = self._fallback_path(key)
                if (fallback_path is None) != (fallback_digest == "missing"):
                    raise StaleMigrationDecision(
                        f"Agent fallback identity changed before retirement: {entry_id}"
                    )
                fallback_context = (
                    nullcontext(None)
                    if fallback_path is None
                    else _bound_agent_source(fallback_path, fallback_digest)
                )
                with fallback_context as approved_fallback_identity:
                    self._validate_directory_ancestry(
                        source_root, source.parent, allow_missing=False
                    )
                    self._validate_directory_ancestry(
                        self.resolver.canonical_root,
                        destination.parent,
                        allow_missing=False,
                    )
                    if self._record_path_exists(destination):
                        raise StaleMigrationDecision(
                            f"Retirement destination already exists: {entry_id}"
                        )
                    directory_flags = (
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    source_directory = os.open(source.parent, directory_flags)
                    destination_directory = os.open(destination.parent, directory_flags)
                    try:
                        os.replace(
                            source.name,
                            destination.name,
                            src_dir_fd=source_directory,
                            dst_dir_fd=destination_directory,
                        )
                        os.fsync(source_directory)
                        os.fsync(destination_directory)
                        destination_matches = (
                            self._record_path_exists(destination)
                            and _filesystem_identity(destination.lstat()) == approved_identity
                            and _retired_content_digest(destination, source) == digest
                        )
                        fallback_matches = self._fallback_identity_is_current(
                            key,
                            fallback_path,
                            fallback_digest,
                            approved_fallback_identity,
                        )
                        if (
                            not destination_matches
                            or self._record_path_exists(source)
                            or not fallback_matches
                        ):
                            if (
                                self._record_path_exists(destination)
                                and not self._record_path_exists(source)
                            ):
                                os.replace(
                                    destination.name,
                                    source.name,
                                    src_dir_fd=destination_directory,
                                    dst_dir_fd=source_directory,
                                )
                                os.fsync(source_directory)
                                os.fsync(destination_directory)
                            raise StaleMigrationDecision(
                                "Retirement identities changed before the decision "
                                f"was consumed: {entry_id}"
                            )
                    finally:
                        os.close(source_directory)
                        os.close(destination_directory)
                self.failure_injector("after_retire", entry_id)

    def _restore_unfinished_retirement(
        self,
        source: Path,
        destination: Path,
        digest: str,
        entry_id: str,
    ) -> None:
        """Restore project precedence before retrying an uncheckpointed retirement."""
        source_root, _key = self._source_root(entry_id, source)
        with global_catalog_lock(self.resolver.global_root):
            source_exists = self._record_path_exists(source)
            destination_exists = self._record_path_exists(destination)
            if source_exists and not destination_exists:
                return
            if source_exists or not destination_exists:
                raise StaleMigrationDecision(
                    f"Unfinished retirement state is unrecoverable: {entry_id}"
                )
            self._validate_directory_ancestry(
                source_root, source.parent, allow_missing=False
            )
            self._validate_directory_ancestry(
                self.resolver.canonical_root,
                destination.parent,
                allow_missing=False,
            )
            if _retired_content_digest(destination, source) != digest:
                raise StaleMigrationDecision(
                    f"Retired agent recovery evidence changed: {entry_id}"
                )
            approved_identity = _filesystem_identity(destination.lstat())
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            source_directory = os.open(source.parent, directory_flags)
            destination_directory = os.open(destination.parent, directory_flags)
            try:
                current = os.stat(
                    destination.name,
                    dir_fd=destination_directory,
                    follow_symlinks=False,
                )
                if _filesystem_identity(current) != approved_identity:
                    raise StaleMigrationDecision(
                        f"Retired agent recovery identity changed: {entry_id}"
                    )
                os.replace(
                    destination.name,
                    source.name,
                    src_dir_fd=destination_directory,
                    dst_dir_fd=source_directory,
                )
                os.fsync(source_directory)
                os.fsync(destination_directory)
            finally:
                os.close(source_directory)
                os.close(destination_directory)
            if (
                not self._record_path_exists(source)
                or self._record_path_exists(destination)
                or _filesystem_identity(source.lstat()) != approved_identity
                or content_digest(source) != digest
            ):
                raise StaleMigrationDecision(
                    f"Project authority could not be restored: {entry_id}"
                )

    def _resume(
        self,
        manifest: Path,
        payload: dict[str, object],
        decisions: Mapping[str, str],
    ) -> MigrationResult:
        records = payload.get("items")
        if not isinstance(records, list):
            raise MigrationDecisionError("Migration journal is missing item records")
        recorded_decisions = {
            str(record.get("entry_id")): str(record.get("action"))
            for record in records
            if isinstance(record, dict)
        }
        if recorded_decisions != dict(decisions):
            raise MigrationDecisionError("Migration decisions do not match the in-progress journal")

        for record in records:
            if not isinstance(record, dict):
                raise MigrationDecisionError("Migration journal contains an invalid item")
            if record.get("state") == "completed":
                self._validate_completed_record(record)
                continue
            entry_id = str(record["entry_id"])
            source = Path(str(record["path"]))
            digest = str(record["digest"])
            fallback_digest = str(record["fallback_digest"])
            action = str(record["action"])
            if action == "preserve":
                if not self._record_path_exists(source) or content_digest(source) != digest:
                    raise StaleMigrationDecision(
                        f"Preserved agent changed during migration: {entry_id}"
                    )
            else:
                destination = Path(str(record["retired_path"]))
                self._restore_unfinished_retirement(
                    source,
                    destination,
                    digest,
                    entry_id,
                )
                if record.get("state") != "retiring":
                    record["state"] = "retiring"
                    self._write_manifest(manifest, payload, entry_id=entry_id)
                self._retire_identity_bound(
                    source,
                    destination,
                    digest,
                    fallback_digest,
                    entry_id,
                )
            record["state"] = "completed"
            self._write_manifest(manifest, payload, entry_id=entry_id)

        retired = [
            str(record["retired_path"])
            for record in records
            if isinstance(record, dict) and record.get("action") == "retire"
        ]
        preserved = [
            str(record["path"])
            for record in records
            if isinstance(record, dict) and record.get("action") == "preserve"
        ]
        payload.update(
            {
                "status": "completed",
                "retired": retired,
                "preserved": preserved,
            }
        )
        self._write_manifest(manifest, payload, entry_id=None)
        return self._result_from_manifest(manifest, payload)

    def apply(self, token: str, decisions: Mapping[str, str]) -> MigrationResult:
        """Apply explicit digest-bound decisions without deleting any agent content."""
        transaction_root = self._transaction_root(token)
        manifest = transaction_root / "manifest.json"
        self._validate_directory_ancestry(
            self.resolver.canonical_root,
            transaction_root,
            allow_missing=True,
        )
        if manifest.exists() or manifest.is_symlink():
            payload = self._read_manifest(manifest)
            records = self._validate_manifest(manifest, payload, token, decisions)
            if payload.get("status") == "completed":
                for record in records:
                    self._validate_completed_record(record)
                return self._result_from_manifest(manifest, payload)
            return self._resume(manifest, payload, decisions)

        try:
            current = self.preview()
        except CatalogValidationError as exc:
            raise StaleMigrationDecision(
                "Agent migration source became unsafe; compare again"
            ) from exc
        if current.token != token:
            raise StaleMigrationDecision("Agent migration preview is stale; compare again")
        expected = {item.entry_id for item in current.items}
        supplied = set(decisions)
        if supplied != expected:
            missing = sorted(expected - supplied)
            unknown = sorted(supplied - expected)
            raise MigrationDecisionError(
                f"Migration decisions must match preview (missing={missing}, unknown={unknown})"
            )
        invalid_actions = sorted(
            entry_id
            for entry_id, action in decisions.items()
            if action not in {"preserve", "retire"}
        )
        if invalid_actions:
            raise MigrationDecisionError(f"Invalid migration action for: {invalid_actions}")
        unsafe_retirements = sorted(
            item.entry_id
            for item in current.items
            if decisions[item.entry_id] == "retire"
            and item.status not in {"generated", "ambiguous"}
        )
        if unsafe_retirements:
            raise MigrationDecisionError(
                "Only generated or explicitly reviewed ambiguous agents can be retired: "
                + ", ".join(unsafe_retirements)
            )

        retirement_root = transaction_root / "retired"
        records = []
        for item in current.items:
            action = decisions[item.entry_id]
            if action == "preserve":
                destination: Optional[Path] = None
            else:
                role, name = item.entry_id.removeprefix("agent:").split("/", 1)
                destination = retirement_root / role / f"{name}.md"
            records.append(
                {
                    **asdict(item),
                    "path": str(item.path),
                    "action": action,
                    "retired_path": str(destination) if destination else None,
                    "state": "pending",
                }
            )

        self._validate_record_paths(
            records,
            transaction_root,
            allow_missing_destinations=True,
        )
        self._ensure_safe_directory(self.resolver.canonical_root, transaction_root)
        self._ensure_safe_directory(self.resolver.canonical_root, transaction_root / "retired")
        for record in records:
            retired_path = record["retired_path"]
            if retired_path is not None:
                self._ensure_safe_directory(
                    self.resolver.canonical_root,
                    Path(str(retired_path)).parent,
                )

        payload: dict[str, object] = {
            "version": _MANIFEST_VERSION,
            "operation": _MANIFEST_OPERATION,
            "token": token,
            "canonical_root": str(self.resolver.canonical_root),
            "project_root": str(self.resolver.project_root),
            "transaction_root": str(transaction_root),
            "status": "in_progress",
            "items": records,
            "retired": [],
            "preserved": [],
        }
        self._write_manifest(manifest, payload, entry_id=None)
        return self._resume(manifest, payload, decisions)
