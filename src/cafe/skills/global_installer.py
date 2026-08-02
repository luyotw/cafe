"""Sync bundled CAFE helper skills into user-level agent CLI directories."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from cafe.skills.loader import read_skill_frontmatter

DEFAULT_GLOBAL_SKILLS = (
    "use-cafe-workflow",
    "write-cafe-playbook",
    "write-cafe-phase",
)

GLOBAL_CLI_SKILL_DIRS = {
    "claude": Path(".claude/skills"),
    "codex": Path(".codex/skills"),
    "copilot": Path(".copilot/skills"),
    "cursor": Path(".cursor/skills"),
    "gemini": Path(".gemini/skills"),
}

GlobalSkillSyncStatus = Literal["installed", "updated", "unchanged", "failed"]
AUTO_SYNC_STATE_VERSION = 1
GLOBAL_SKILL_SYNC_LOCK_TIMEOUT_SECONDS = 10


class GlobalSkillSyncError(ValueError):
    """Raised when a global skill sync request is invalid before copying."""


@dataclass(frozen=True)
class GlobalSkillSyncResult:
    """Result of syncing one bundled skill to one CLI."""

    cli: str
    skill: str
    source: Path
    destination: Path
    status: GlobalSkillSyncStatus
    reason: Optional[str] = None


@dataclass(frozen=True)
class GlobalSkillSyncSummary:
    """Aggregated global skill sync results."""

    source_root: Path
    home_dir: Path
    results: list[GlobalSkillSyncResult]

    @property
    def installed_count(self) -> int:
        return self._count("installed")

    @property
    def updated_count(self) -> int:
        return self._count("updated")

    @property
    def unchanged_count(self) -> int:
        return self._count("unchanged")

    @property
    def failed_count(self) -> int:
        return self._count("failed")

    @property
    def changed_count(self) -> int:
        return self.installed_count + self.updated_count

    def _count(self, status: GlobalSkillSyncStatus) -> int:
        return sum(1 for result in self.results if result.status == status)


@dataclass(frozen=True)
class _ResolvedGlobalSkillSync:
    """Validated paths and names shared by explicit and automatic sync."""

    source_root: Path
    home_dir: Path
    skill_names: list[str]
    cli_names: list[str]


def _default_source_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "skills"


def _default_home_dir() -> Path:
    return Path.home()


def _normalize_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _validate_cli_names(cli_names: Optional[list[str]]) -> list[str]:
    requested = list(GLOBAL_CLI_SKILL_DIRS) if cli_names is None else cli_names
    names = _normalize_unique(requested)
    if not names:
        raise GlobalSkillSyncError("At least one CLI is required")
    for name in names:
        if name not in GLOBAL_CLI_SKILL_DIRS:
            supported = ", ".join(GLOBAL_CLI_SKILL_DIRS)
            raise GlobalSkillSyncError(f"Unsupported CLI '{name}'; choose from: {supported}")
    return names


def _validate_sources(source_root: Path, skill_names: Optional[list[str]]) -> list[str]:
    requested = list(DEFAULT_GLOBAL_SKILLS) if skill_names is None else skill_names
    names = _normalize_unique(requested)
    if not names:
        raise GlobalSkillSyncError("At least one skill is required")

    for name in names:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise GlobalSkillSyncError(f"Invalid bundled skill name '{name}'")
        source = source_root / name
        skill_file = source / "SKILL.md"
        if not source.is_dir() or not skill_file.is_file():
            raise GlobalSkillSyncError(f"Missing bundled skill '{name}' under {source_root}")
        metadata = read_skill_frontmatter(skill_file)
        if metadata.get("name") != name:
            raise GlobalSkillSyncError(
                f"Bundled skill '{name}' has mismatched frontmatter name '{metadata.get('name')}'"
            )
    return names


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> list[tuple[str, str, str]]:
    manifest: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            manifest.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            manifest.append((relative, "directory", ""))
        elif path.is_file():
            manifest.append((relative, "file", _file_digest(path)))
        else:
            manifest.append((relative, "other", ""))
    return manifest


def _trees_equal(source: Path, destination: Path) -> bool:
    if destination.is_symlink() or not destination.is_dir():
        return False
    return _tree_manifest(source) == _tree_manifest(destination)


def _source_fingerprint(source_root: Path, skill_names: list[str]) -> str:
    payload = [(skill, _tree_manifest(source_root / skill)) for skill in skill_names]
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _auto_sync_state_file(home_dir: Path) -> Path:
    return home_dir / ".cafe" / "cache" / "global-skills-sync.json"


@contextmanager
def _global_skill_sync_lock(home_dir: Path) -> Iterator[None]:
    """Serialize one sync batch with SQLite's cross-platform process lock."""
    lock_file = home_dir / ".cafe" / "cache" / "global-skills-sync.lock.sqlite3"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        lock_file,
        isolation_level=None,
        timeout=GLOBAL_SKILL_SYNC_LOCK_TIMEOUT_SECONDS,
    )
    try:
        connection.execute("BEGIN EXCLUSIVE")
        yield
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _global_skill_destinations_exist(
    home_dir: Path,
    skill_names: list[str],
    cli_names: list[str],
) -> bool:
    return all(
        (home_dir / GLOBAL_CLI_SKILL_DIRS[cli] / skill / "SKILL.md").is_file()
        for cli in cli_names
        for skill in skill_names
    )


def _build_auto_sync_state(
    request: _ResolvedGlobalSkillSync,
    fingerprint: str,
) -> dict[str, object]:
    return {
        "version": AUTO_SYNC_STATE_VERSION,
        "source_root": str(request.source_root),
        "fingerprint": fingerprint,
        "skills": request.skill_names,
        "clis": request.cli_names,
    }


def _auto_sync_state_matches(
    state_file: Path,
    expected_state: dict[str, object],
) -> bool:
    try:
        state: object = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state == expected_state


def _write_auto_sync_state(
    state_file: Path,
    state: dict[str, object],
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{state_file.name}.",
        dir=state_file.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, state_file)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_directory(source: Path, destination: Path) -> None:
    """Replace one destination from a fully copied sibling staging directory."""
    skills_root = destination.parent
    if skills_root.is_symlink() and not skills_root.exists():
        raise OSError(f"Skills directory is a dangling symlink: {skills_root}")
    if skills_root.exists() and not skills_root.is_dir():
        raise NotADirectoryError(f"Skills path is not a directory: {skills_root}")
    skills_root.mkdir(parents=True, exist_ok=True)

    workspace = Path(tempfile.mkdtemp(prefix=f".cafe-{destination.name}-", dir=skills_root))
    staged = workspace / "staged"
    backup = workspace / "previous"
    had_destination = destination.exists() or destination.is_symlink()

    try:
        shutil.copytree(source, staged, symlinks=True)
        if had_destination:
            destination.rename(backup)
        try:
            staged.rename(destination)
        except Exception:
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            if had_destination and (backup.exists() or backup.is_symlink()):
                backup.rename(destination)
            raise
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _resolve_global_skill_sync(
    *,
    source_root: Optional[Path] = None,
    home_dir: Optional[Path] = None,
    skill_names: Optional[list[str]] = None,
    cli_names: Optional[list[str]] = None,
) -> _ResolvedGlobalSkillSync:
    resolved_source_root = (source_root or _default_source_root()).expanduser().resolve()
    resolved_home_dir = (home_dir or _default_home_dir()).expanduser().resolve()
    return _ResolvedGlobalSkillSync(
        source_root=resolved_source_root,
        home_dir=resolved_home_dir,
        skill_names=_validate_sources(resolved_source_root, skill_names),
        cli_names=_validate_cli_names(cli_names),
    )


def _sync_resolved_global_skills(
    request: _ResolvedGlobalSkillSync,
) -> GlobalSkillSyncSummary:
    """Synchronize one validated request while the caller holds the batch lock."""

    results: list[GlobalSkillSyncResult] = []
    for cli in request.cli_names:
        skills_root = request.home_dir / GLOBAL_CLI_SKILL_DIRS[cli]
        for skill in request.skill_names:
            source = request.source_root / skill
            destination = skills_root / skill
            existed = destination.exists() or destination.is_symlink()
            try:
                if existed and _trees_equal(source, destination):
                    status: GlobalSkillSyncStatus = "unchanged"
                else:
                    _replace_directory(source, destination)
                    status = "updated" if existed else "installed"
                results.append(
                    GlobalSkillSyncResult(
                        cli=cli,
                        skill=skill,
                        source=source,
                        destination=destination,
                        status=status,
                    )
                )
            except Exception as exc:
                results.append(
                    GlobalSkillSyncResult(
                        cli=cli,
                        skill=skill,
                        source=source,
                        destination=destination,
                        status="failed",
                        reason=str(exc),
                    )
                )

    return GlobalSkillSyncSummary(
        source_root=request.source_root,
        home_dir=request.home_dir,
        results=results,
    )


def sync_global_skills(
    *,
    source_root: Optional[Path] = None,
    home_dir: Optional[Path] = None,
    skill_names: Optional[list[str]] = None,
    cli_names: Optional[list[str]] = None,
) -> GlobalSkillSyncSummary:
    """Install or update bundled skills in selected user-level CLI directories.

    Sources always come from CAFE's bundled skill catalog, not project or global
    overrides. Validation completes before acquiring one per-machine batch lock,
    and each destination is staged before replacement so failures do not remove
    the previous copy.
    """
    request = _resolve_global_skill_sync(
        source_root=source_root,
        home_dir=home_dir,
        skill_names=skill_names,
        cli_names=cli_names,
    )
    with _global_skill_sync_lock(request.home_dir):
        return _sync_resolved_global_skills(request)


def auto_sync_global_skills(
    *,
    source_root: Optional[Path] = None,
    home_dir: Optional[Path] = None,
) -> Optional[GlobalSkillSyncSummary]:
    """Synchronize defaults only when sources changed or an install is missing.

    The per-machine fingerprint makes normal CAFE startup cheap while ensuring a
    fresh checkout, package upgrade, or another machine installs the bundled
    helper skills on its first invocation. Explicit ``sync-global`` remains the
    recovery path for destination content that exists but was manually edited.
    """
    request = _resolve_global_skill_sync(
        source_root=source_root,
        home_dir=home_dir,
    )
    with _global_skill_sync_lock(request.home_dir):
        fingerprint = _source_fingerprint(request.source_root, request.skill_names)
        state_file = _auto_sync_state_file(request.home_dir)
        expected_state = _build_auto_sync_state(request, fingerprint)

        if _auto_sync_state_matches(
            state_file,
            expected_state,
        ) and _global_skill_destinations_exist(
            request.home_dir,
            request.skill_names,
            request.cli_names,
        ):
            return None

        summary = _sync_resolved_global_skills(request)
        if summary.failed_count == 0:
            _write_auto_sync_state(state_file, expected_state)
        return summary
