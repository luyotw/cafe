"""Conservative migration for project agent files created by legacy preparation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from contextlib import contextmanager
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


@contextmanager
def _bound_agent_source(
    path: Path, expected_digest: str
) -> Iterator[tuple[int, int, int]]:
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
            item
            for item in self.resolver.catalog_roots(CatalogKind.AGENT)
            if item[0] == "project"
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

    def _fallback_digest(self, key: str) -> str:
        roots = [
            item
            for item in self.resolver.catalog_roots(CatalogKind.AGENT)
            if item[0] != "project"
        ]
        for _source, root, _layer in reversed(roots):
            path = self.resolver.candidate_path(CatalogKind.AGENT, key, root)
            if path.is_file() or path.is_symlink():
                return content_digest(path)
        return "missing"

    def _token(self, items: list[MigrationItem]) -> str:
        payload = {
            "canonical_root": str(self.resolver.canonical_root),
            "project_root": str(self.resolver.project_root),
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
            expected_name = key.split("/", 1)[1]
            fallback_digest = self._fallback_digest(key)
            if not self._valid_agent(path, expected_name):
                status = "invalid"
            elif self.is_tracked(path):
                status = "intentional"
            elif fallback_digest != "missing" and digest == fallback_digest:
                status = "generated"
            else:
                status = "ambiguous"
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
            self.resolver.canonical_root
            / ".cafe"
            / "migrations"
            / "agent-snapshots"
            / token[:16]
        )

    @staticmethod
    def _result_from_manifest(manifest: Path) -> MigrationResult:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return MigrationResult(
            retired=tuple(Path(item) for item in payload.get("retired", [])),
            preserved=tuple(Path(item) for item in payload.get("preserved", [])),
            manifest=manifest,
        )

    def _confirmed_preserved(self) -> set[tuple[str, str]]:
        roots = dict.fromkeys(
            (self.resolver.canonical_root, self.resolver.project_root)
        )
        confirmed: set[tuple[str, str]] = set()
        for project_root in roots:
            root = project_root / ".cafe" / "migrations" / "agent-snapshots"
            for manifest in sorted(root.glob("*/manifest.json")):
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if payload.get("status") != "completed":
                    continue
                for record in payload.get("items", []):
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
            if (
                not self._record_path_exists(source)
                or content_digest(source) != digest
            ):
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
            or content_digest(destination) != digest
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
        temporary = manifest.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest)
            directory = os.open(manifest.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _retire_identity_bound(
        self,
        source: Path,
        destination: Path,
        digest: str,
        entry_id: str,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _bound_agent_source(source, digest) as approved_identity:
            self.failure_injector("before_retire", entry_id)
            if self._record_path_exists(destination):
                raise StaleMigrationDecision(
                    f"Retirement destination already exists: {entry_id}"
                )
            source_directory = os.open(source.parent, os.O_RDONLY)
            destination_directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.replace(
                    source.name,
                    destination.name,
                    src_dir_fd=source_directory,
                    dst_dir_fd=destination_directory,
                )
                os.fsync(source_directory)
                os.fsync(destination_directory)
            finally:
                os.close(source_directory)
                os.close(destination_directory)

            destination_matches = (
                self._record_path_exists(destination)
                and _filesystem_identity(destination.lstat()) == approved_identity
                and content_digest(destination) == digest
            )
            if not destination_matches or self._record_path_exists(source):
                if self._record_path_exists(destination) and not self._record_path_exists(
                    source
                ):
                    os.replace(destination, source)
                    source_directory = os.open(source.parent, os.O_RDONLY)
                    destination_directory = os.open(destination.parent, os.O_RDONLY)
                    try:
                        os.fsync(source_directory)
                        os.fsync(destination_directory)
                    finally:
                        os.close(source_directory)
                        os.close(destination_directory)
                raise StaleMigrationDecision(
                    f"Retired agent does not match the approved identity: {entry_id}"
                )
            self.failure_injector("after_retire", entry_id)

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
            raise MigrationDecisionError(
                "Migration decisions do not match the in-progress journal"
            )

        for record in records:
            if not isinstance(record, dict):
                raise MigrationDecisionError("Migration journal contains an invalid item")
            if record.get("state") == "completed":
                self._validate_completed_record(record)
                continue
            entry_id = str(record["entry_id"])
            source = Path(str(record["path"]))
            digest = str(record["digest"])
            action = str(record["action"])
            if action == "preserve":
                if (
                    not self._record_path_exists(source)
                    or content_digest(source) != digest
                ):
                    raise StaleMigrationDecision(
                        f"Preserved agent changed during migration: {entry_id}"
                    )
            else:
                destination = Path(str(record["retired_path"]))
                if source.exists() or source.is_symlink():
                    self._retire_identity_bound(
                        source, destination, digest, entry_id
                    )
                elif (
                    not self._record_path_exists(destination)
                    or content_digest(destination) != digest
                ):
                    raise StaleMigrationDecision(
                        f"Retired agent state is unrecoverable: {entry_id}"
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
        return self._result_from_manifest(manifest)

    def apply(self, token: str, decisions: Mapping[str, str]) -> MigrationResult:
        """Apply explicit digest-bound decisions without deleting any agent content."""
        transaction_root = self._transaction_root(token)
        manifest = transaction_root / "manifest.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if payload.get("status") == "completed":
                records = payload.get("items")
                if not isinstance(records, list):
                    raise MigrationDecisionError(
                        "Migration journal is missing item records"
                    )
                recorded_decisions = {
                    str(record.get("entry_id")): str(record.get("action"))
                    for record in records
                    if isinstance(record, dict)
                }
                if recorded_decisions != dict(decisions):
                    raise MigrationDecisionError(
                        "Migration decisions do not match the completed journal"
                    )
                for record in records:
                    if not isinstance(record, dict):
                        raise MigrationDecisionError(
                            "Migration journal contains an invalid item"
                        )
                    self._validate_completed_record(record)
                return self._result_from_manifest(manifest)
            return self._resume(manifest, payload, decisions)

        current = self.preview()
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
        retirement_root.mkdir(parents=True, exist_ok=True)
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

        payload: dict[str, object] = {
            "version": 1,
            "token": token,
            "status": "in_progress",
            "items": records,
            "retired": [],
            "preserved": [],
        }
        self._write_manifest(manifest, payload, entry_id=None)
        return self._resume(manifest, payload, decisions)
