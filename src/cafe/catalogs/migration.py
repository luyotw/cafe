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

from cafe.catalogs.resolver import CatalogKind, CatalogResolver, content_digest


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
    ) -> None:
        self.resolver = resolver
        self.is_tracked = is_tracked

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

    def apply(self, token: str, decisions: Mapping[str, str]) -> MigrationResult:
        """Apply explicit digest-bound decisions without deleting any agent content."""
        transaction_root = self._transaction_root(token)
        manifest = transaction_root / "manifest.json"
        if manifest.is_file():
            return self._result_from_manifest(manifest)

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

        retirement_root = transaction_root / "retired"
        retirement_root.mkdir(parents=True, exist_ok=True)
        retired: list[Path] = []
        preserved: list[Path] = []
        records = []
        for item in current.items:
            action = decisions[item.entry_id]
            if action == "preserve":
                preserved.append(item.path)
                destination: Optional[Path] = None
            else:
                role, name = item.entry_id.removeprefix("agent:").split("/", 1)
                destination = retirement_root / role / f"{name}.md"
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item.path, destination)
                retired.append(destination)
            records.append(
                {
                    **asdict(item),
                    "path": str(item.path),
                    "action": action,
                    "retired_path": str(destination) if destination else None,
                }
            )

        payload = {
            "version": 1,
            "token": token,
            "status": "completed",
            "items": records,
            "retired": [str(item) for item in retired],
            "preserved": [str(item) for item in preserved],
        }
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return MigrationResult(tuple(retired), tuple(preserved), manifest)
