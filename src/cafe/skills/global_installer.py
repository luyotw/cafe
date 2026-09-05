"""Sync bundled CAFE helper skills into user-level agent CLI directories."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from cafe.skills.loader import read_skill_frontmatter

DEFAULT_GLOBAL_SKILLS = (
    "use-cafe-workflow",
    "write-cafe-agent",
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
GLOBAL_CLI_EXECUTABLES = {
    "claude": ("claude",),
    "codex": ("codex",),
    "copilot": ("copilot",),
    "cursor": ("cursor-agent",),
    "gemini": ("gemini",),
}

GlobalSkillSyncStatus = Literal["installed", "updated", "unchanged", "failed"]
EXPLICIT_SYNC_LOCK_TIMEOUT_SECONDS = 10
AUTO_SYNC_LOCK_TIMEOUT_SECONDS = 0.05


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

    @property
    def installed_skill_count(self) -> int:
        return len(
            {result.skill for result in self.results if result.status == "installed"}
        )

    @property
    def changed_skill_count(self) -> int:
        return len(
            {
                result.skill
                for result in self.results
                if result.status in {"installed", "updated"}
            }
        )

    @property
    def changed_cli_count(self) -> int:
        return len(
            {
                result.cli
                for result in self.results
                if result.status in {"installed", "updated"}
            }
        )

    def _count(self, status: GlobalSkillSyncStatus) -> int:
        return sum(1 for result in self.results if result.status == status)


@dataclass(frozen=True)
class _ResolvedGlobalSkillSync:
    """Validated paths and names shared by explicit and automatic sync."""

    source_root: Path
    home_dir: Path
    skill_names: list[str]
    cli_names: list[str]
    default_cli_selection: bool


@dataclass
class _StagedGlobalSkillReplacement:
    """One destination staged for a batch publish or rollback."""

    cli: str
    skill: str
    source: Path
    destination: Path
    status: GlobalSkillSyncStatus
    workspace: Path
    staged: Path
    backup: Path
    had_destination: bool
    published: bool = False


def _default_source_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "skills"


def _discover_git_roots(checkout_root: Path) -> tuple[Path, Path]:
    """Return active and canonical roots using read-only Git discovery."""

    def run(*args: str) -> Path:
        result = subprocess.run(
            ("git", *args),
            cwd=checkout_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GlobalSkillSyncError(
                result.stderr.strip() or "Git root discovery failed"
            )
        return Path(result.stdout.strip()).resolve()

    active = run("rev-parse", "--show-toplevel")
    common_git = run("rev-parse", "--path-format=absolute", "--git-common-dir")
    canonical = common_git.parent if common_git.name == ".git" else active
    return active, canonical


def _trusted_automatic_source_root(bundled_root: Optional[Path] = None) -> Path:
    """Resolve a released bundle or a checkout's canonical bundled catalog."""
    source_root = (bundled_root or _default_source_root()).expanduser().resolve()
    if len(source_root.parents) < 4 or source_root.parents[2].name != "src":
        return source_root

    checkout_root = source_root.parents[3]
    if not (checkout_root / ".git").exists():
        return source_root

    active, canonical = _discover_git_roots(checkout_root)
    if active != checkout_root:
        raise GlobalSkillSyncError(
            f"Git reported active checkout {active}, expected {checkout_root}"
        )
    canonical_source = (canonical / "src" / "cafe" / "data" / "skills").resolve()
    try:
        canonical_source.relative_to(canonical.resolve())
    except ValueError as exc:
        raise GlobalSkillSyncError(
            f"Trusted automatic source escapes canonical checkout: {canonical_source}"
        ) from exc
    return canonical_source


def _default_home_dir() -> Path:
    return Path.home()


def _normalize_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _validate_cli_names(cli_names: list[str]) -> list[str]:
    names = _normalize_unique(cli_names)
    if not names:
        raise GlobalSkillSyncError("At least one CLI is required")
    for name in names:
        if name not in GLOBAL_CLI_SKILL_DIRS:
            supported = ", ".join(GLOBAL_CLI_SKILL_DIRS)
            raise GlobalSkillSyncError(f"Unsupported CLI '{name}'; choose from: {supported}")
    return names


def _has_existing_agent_state(home_dir: Path, cli: str) -> bool:
    """Detect vendor state while ignoring directories created only by CAFE."""
    skills_root = home_dir / GLOBAL_CLI_SKILL_DIRS[cli]
    agent_root = skills_root.parent
    if agent_root.is_symlink() or (agent_root.exists() and not agent_root.is_dir()):
        return True
    if not agent_root.is_dir():
        return False
    for child in agent_root.iterdir():
        if child != skills_root:
            return True
    if not skills_root.is_dir():
        return False
    return any(child.name not in DEFAULT_GLOBAL_SKILLS for child in skills_root.iterdir())


def detect_global_skill_clis(*, home_dir: Optional[Path] = None) -> list[str]:
    """Return supported agent CLIs evidenced by PATH or existing vendor state."""
    resolved_home = (home_dir or _default_home_dir()).expanduser().resolve()
    detected: list[str] = []
    for cli in GLOBAL_CLI_SKILL_DIRS:
        executable_found = any(
            shutil.which(name) is not None for name in GLOBAL_CLI_EXECUTABLES[cli]
        )
        if executable_found or _has_existing_agent_state(resolved_home, cli):
            detected.append(cli)
    return detected


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
        try:
            source.resolve().relative_to(source_root)
            skill_file.resolve().relative_to(source_root)
        except ValueError as exc:
            raise GlobalSkillSyncError(
                f"Bundled skill '{name}' escapes source root {source_root}"
            ) from exc
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
        relative_path = path.relative_to(root)
        if "__pycache__" in relative_path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = relative_path.as_posix()
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


@contextmanager
def _global_skill_sync_lock(
    home_dir: Path,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    """Serialize one sync batch with SQLite's cross-platform process lock."""
    lock_file = home_dir / ".cafe" / "cache" / "global-skills-sync.lock.sqlite3"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        lock_file,
        isolation_level=None,
        timeout=timeout_seconds,
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
        (
            (destination := home_dir / GLOBAL_CLI_SKILL_DIRS[cli] / skill).exists()
            or destination.is_symlink()
        )
        for cli in cli_names
        for skill in skill_names
    )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _stage_directory_replacement(
    *,
    cli: str,
    skill: str,
    source: Path,
    destination: Path,
    status: GlobalSkillSyncStatus,
) -> _StagedGlobalSkillReplacement:
    """Copy one source beside its destination without publishing it."""
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
        shutil.copytree(
            source,
            staged,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    return _StagedGlobalSkillReplacement(
        cli=cli,
        skill=skill,
        source=source,
        destination=destination,
        status=status,
        workspace=workspace,
        staged=staged,
        backup=backup,
        had_destination=had_destination,
    )


def _publish_staged_replacement(operation: _StagedGlobalSkillReplacement) -> None:
    """Publish one staged destination while retaining its rollback backup."""
    if operation.had_destination:
        operation.destination.rename(operation.backup)
    try:
        operation.staged.rename(operation.destination)
    except Exception:
        if operation.had_destination and (
            operation.backup.exists() or operation.backup.is_symlink()
        ):
            operation.backup.rename(operation.destination)
        raise
    operation.published = True


def _rollback_staged_replacement(operation: _StagedGlobalSkillReplacement) -> None:
    """Restore one previously published destination."""
    if not operation.published:
        return
    _remove_path(operation.destination)
    if operation.had_destination:
        operation.backup.rename(operation.destination)
    operation.published = False


def _cleanup_staged_replacement(
    operation: _StagedGlobalSkillReplacement,
    *,
    preserve_backup: bool = False,
) -> None:
    if preserve_backup and (operation.backup.exists() or operation.backup.is_symlink()):
        return
    shutil.rmtree(operation.workspace, ignore_errors=True)


def _replacement_result(
    operation: _StagedGlobalSkillReplacement,
    *,
    status: Optional[GlobalSkillSyncStatus] = None,
    reason: Optional[str] = None,
) -> GlobalSkillSyncResult:
    return GlobalSkillSyncResult(
        cli=operation.cli,
        skill=operation.skill,
        source=operation.source,
        destination=operation.destination,
        status=status or operation.status,
        reason=reason,
    )


def _resolve_global_skill_sync(
    *,
    source_root: Optional[Path] = None,
    home_dir: Optional[Path] = None,
    skill_names: Optional[list[str]] = None,
    cli_names: Optional[list[str]] = None,
    automatic: bool = False,
) -> _ResolvedGlobalSkillSync:
    if automatic and source_root is None:
        resolved_source_root = _trusted_automatic_source_root()
    else:
        resolved_source_root = (source_root or _default_source_root()).expanduser().resolve()
    resolved_home_dir = (home_dir or _default_home_dir()).expanduser().resolve()
    return _ResolvedGlobalSkillSync(
        source_root=resolved_source_root,
        home_dir=resolved_home_dir,
        skill_names=_validate_sources(resolved_source_root, skill_names),
        cli_names=(
            detect_global_skill_clis(home_dir=resolved_home_dir)
            if cli_names is None
            else _validate_cli_names(cli_names)
        ),
        default_cli_selection=cli_names is None,
    )


def _sync_resolved_global_skills(
    request: _ResolvedGlobalSkillSync,
    *,
    replace_existing: bool = True,
) -> GlobalSkillSyncSummary:
    """Stage every change, then publish or roll back the complete batch."""

    entries: list[GlobalSkillSyncResult | _StagedGlobalSkillReplacement] = []
    staging_failure: Optional[str] = None
    for cli in request.cli_names:
        skills_root = request.home_dir / GLOBAL_CLI_SKILL_DIRS[cli]
        for skill in request.skill_names:
            source = request.source_root / skill
            destination = skills_root / skill
            existed = destination.exists() or destination.is_symlink()
            try:
                if existed and (
                    not replace_existing or _trees_equal(source, destination)
                ):
                    entries.append(
                        GlobalSkillSyncResult(
                            cli=cli,
                            skill=skill,
                            source=source,
                            destination=destination,
                            status="unchanged",
                        )
                    )
                elif staging_failure is not None:
                    entries.append(
                        GlobalSkillSyncResult(
                            cli=cli,
                            skill=skill,
                            source=source,
                            destination=destination,
                            status="failed",
                            reason=f"Batch staging aborted: {staging_failure}",
                        )
                    )
                else:
                    entries.append(
                        _stage_directory_replacement(
                            cli=cli,
                            skill=skill,
                            source=source,
                            destination=destination,
                            status="updated" if existed else "installed",
                        )
                    )
            except Exception as exc:
                staging_failure = f"{cli}/{skill}: {exc}"
                entries.append(
                    GlobalSkillSyncResult(
                        cli=cli,
                        skill=skill,
                        source=source,
                        destination=destination,
                        status="failed",
                        reason=str(exc),
                    )
                )

    operations = [entry for entry in entries if isinstance(entry, _StagedGlobalSkillReplacement)]
    if staging_failure is not None:
        for operation in operations:
            _cleanup_staged_replacement(operation)
        results = [
            _replacement_result(
                entry,
                status="failed",
                reason=f"Batch staging aborted: {staging_failure}",
            )
            if isinstance(entry, _StagedGlobalSkillReplacement)
            else entry
            for entry in entries
        ]
        return GlobalSkillSyncSummary(
            source_root=request.source_root,
            home_dir=request.home_dir,
            results=results,
        )

    published: list[_StagedGlobalSkillReplacement] = []
    publish_failure: Optional[str] = None
    rollback_failures: list[str] = []
    for operation in operations:
        try:
            _publish_staged_replacement(operation)
            published.append(operation)
        except Exception as exc:
            publish_failure = f"{operation.cli}/{operation.skill}: {exc}"
            for published_operation in reversed(published):
                try:
                    _rollback_staged_replacement(published_operation)
                except Exception as rollback_exc:
                    rollback_failures.append(
                        f"{published_operation.cli}/{published_operation.skill}: {rollback_exc}"
                    )
            break

    if publish_failure is not None:
        reason = f"Batch publish rolled back: {publish_failure}"
        if rollback_failures:
            reason += f"; rollback failures: {', '.join(rollback_failures)}"
        for operation in operations:
            _cleanup_staged_replacement(operation, preserve_backup=True)
        results = [
            _replacement_result(entry, status="failed", reason=reason)
            if isinstance(entry, _StagedGlobalSkillReplacement)
            else entry
            for entry in entries
        ]
        return GlobalSkillSyncSummary(
            source_root=request.source_root,
            home_dir=request.home_dir,
            results=results,
        )

    for operation in operations:
        _cleanup_staged_replacement(operation)

    results = [
        _replacement_result(entry) if isinstance(entry, _StagedGlobalSkillReplacement) else entry
        for entry in entries
    ]
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
    """Install or update bundled skills in detected or selected user CLI directories.

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
    if not request.cli_names:
        return GlobalSkillSyncSummary(
            source_root=request.source_root,
            home_dir=request.home_dir,
            results=[],
        )
    with _global_skill_sync_lock(
        request.home_dir,
        timeout_seconds=EXPLICIT_SYNC_LOCK_TIMEOUT_SECONDS,
    ):
        return _sync_resolved_global_skills(request)


def auto_sync_global_skills(
    *,
    source_root: Optional[Path] = None,
    home_dir: Optional[Path] = None,
) -> Optional[GlobalSkillSyncSummary]:
    """Install missing defaults without changing any existing destination."""
    request = _resolve_global_skill_sync(
        source_root=source_root,
        home_dir=home_dir,
        automatic=True,
    )
    if not request.cli_names:
        return None
    if _global_skill_destinations_exist(
        request.home_dir,
        request.skill_names,
        request.cli_names,
    ):
        return None
    try:
        with _global_skill_sync_lock(
            request.home_dir,
            timeout_seconds=AUTO_SYNC_LOCK_TIMEOUT_SECONDS,
        ):
            if _global_skill_destinations_exist(
                request.home_dir,
                request.skill_names,
                request.cli_names,
            ):
                return None
            return _sync_resolved_global_skills(request, replace_existing=False)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            return None
        raise
