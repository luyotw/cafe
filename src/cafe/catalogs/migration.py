"""Conservative migration for project agent files created by legacy preparation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from cafe.catalogs.resolver import (
    CatalogKind,
    CatalogResolver,
    content_digest,
    global_catalog_lock,
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
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        if not text.startswith("---\n"):
            return False
        end = text.find("\n---", 4)
        if end < 0:
            return False
        try:
            metadata = yaml.safe_load(text[4:end])
        except yaml.YAMLError:
            return False
        return isinstance(metadata, dict) and metadata.get("name") == expected_name

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

    @staticmethod
    def _token(items: list[MigrationItem]) -> str:
        payload = [
            {
                "entry_id": item.entry_id,
                "digest": item.digest,
                "fallback_digest": item.fallback_digest,
                "status": item.status,
            }
            for item in items
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def preview(self) -> MigrationPreview:
        with global_catalog_lock(self.resolver.global_root):
            return self._preview()

    def _preview(self) -> MigrationPreview:
        items: list[MigrationItem] = []
        for entry in self.resolver.project_entries([CatalogKind.AGENT]):
            expected_name = entry.key.split("/", 1)[1]
            fallback_digest = self._fallback_digest(entry.key)
            if not self._valid_agent(entry.path, expected_name):
                status = "invalid"
            elif self.is_tracked(entry.path):
                status = "intentional"
            elif fallback_digest != "missing" and entry.digest == fallback_digest:
                status = "generated"
            else:
                status = "ambiguous"
            items.append(
                MigrationItem(
                    entry_id=entry.entry_id,
                    path=entry.path,
                    digest=entry.digest,
                    fallback_digest=fallback_digest,
                    status=status,
                )
            )
        items.sort(key=lambda item: item.entry_id)
        return MigrationPreview(token=self._token(items), items=tuple(items))

    def _transaction_root(self, token: str) -> Path:
        return (
            self.resolver.project_root
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

    def _confirmed_preserved(self) -> set[tuple[str, str, str]]:
        root = (
            self.resolver.project_root
            / ".cafe"
            / "migrations"
            / "agent-snapshots"
        )
        confirmed: set[tuple[str, str, str]] = set()
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
                            str(record.get("path")),
                            str(record.get("digest")),
                        )
                    )
        return confirmed

    def publication_blocked_entry_ids(self) -> set[str]:
        """Return generated snapshots that lack an explicit preserve decision."""
        confirmed = self._confirmed_preserved()
        return {
            item.entry_id
            for item in self.preview().items
            if item.status == "generated"
            and (item.entry_id, str(item.path), item.digest) not in confirmed
        }

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
                continue
            entry_id = str(record["entry_id"])
            source = Path(str(record["path"]))
            digest = str(record["digest"])
            action = str(record["action"])
            if action == "preserve":
                if not source.exists() or content_digest(source) != digest:
                    raise StaleMigrationDecision(
                        f"Preserved agent changed during migration: {entry_id}"
                    )
            else:
                destination = Path(str(record["retired_path"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.exists() or source.is_symlink():
                    if content_digest(source) != digest or destination.exists():
                        raise StaleMigrationDecision(
                            f"Retired agent changed during migration: {entry_id}"
                        )
                    os.replace(source, destination)
                    self.failure_injector("after_retire", entry_id)
                elif not destination.exists() or content_digest(destination) != digest:
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
