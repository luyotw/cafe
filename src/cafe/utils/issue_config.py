"""Issue YAML config helpers extracted from legacy phase mixins."""

from __future__ import annotations

import fcntl
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

import yaml

from cafe.core.packet_io import atomic_write_bytes


def read_issue_config(config_path: Path) -> Optional[Dict[str, Any]]:
    """Read issue configuration from issue.yaml."""
    if not config_path.exists():
        return None
    try:
        with open(config_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
        return config_data if config_data else None
    except (yaml.YAMLError, OSError):
        return None


def read_issue_config_strict(config_path: Path) -> Dict[str, Any]:
    """Read mutable issue authority without hiding malformed or nonmapping data."""
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("issue.yaml is unreadable") from exc
    if not isinstance(loaded, dict):
        raise ValueError("issue.yaml must contain a mapping")
    return loaded


@contextmanager
def issue_config_lock(config_path: Path) -> Iterator[None]:
    """Serialize cooperating settings writers for one issue authority."""
    lock_path = config_path.with_name("issue-settings.lock")
    if lock_path.is_symlink():
        raise ValueError("issue settings lock must not be a symlink")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_issue_config_atomic(config_path: Path, config: Mapping[str, Any]) -> None:
    """Replace a validated issue mapping through the shared atomic-write primitive."""
    content = yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=True).encode("utf-8")
    atomic_write_bytes(config_path, content)


def _repository_root_for_config(config_path: Path) -> Path:
    resolved = config_path.resolve()
    for parent in resolved.parents:
        if parent.name == ".cafe":
            return parent.parent
    return Path.cwd().resolve()


def _registered_worktree_paths(repository_root: Path) -> tuple[Path, ...]:
    """Return the selected repository's registered worktree roots, main first."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("cannot verify inventory worktree against the selected repository")
    registered: list[Path] = []
    for field in result.stdout.split("\0"):
        if field.startswith("worktree "):
            registered.append(Path(field.removeprefix("worktree ")).resolve())
    return tuple(registered)


def _issue_authority_worktree(config_path: Path) -> Optional[Path]:
    """Return the worktree for an exact .cafe issue authority path."""
    if (
        config_path.name == "issue.yaml"
        and config_path.parent.parent.name == "issues"
        and config_path.parent.parent.parent.name == ".cafe"
    ):
        return config_path.parents[3]
    return None


def _require_registered_issue_authority(
    config_path: Path, registered_worktrees: tuple[Path, ...]
) -> Path:
    worktree = _issue_authority_worktree(config_path)
    issue_name = config_path.parent.name
    if (
        worktree not in registered_worktrees
        or not issue_name
        or issue_name in {".", ".."}
        or Path(issue_name).name != issue_name
    ):
        raise ValueError("issue configuration is outside a registered worktree authority")
    return config_path


def _reject_issue_authority_symlinks(config_path: Path) -> None:
    """Reject aliases in the issue authority suffix before canonicalization."""
    lexical = config_path.absolute()
    for candidate in (lexical, lexical.parent, lexical.parent.parent, lexical.parent.parent.parent):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("issue configuration paths must not traverse a symlink")


def resolve_issue_config_path(
    config_path: Path,
    *,
    require_registered_worktree: bool = False,
) -> Path:
    """Resolve a repo inventory pointer to the active-worktree authority."""
    if require_registered_worktree:
        _reject_issue_authority_symlinks(Path(config_path))
    path = Path(config_path).resolve()
    registered_worktrees: tuple[Path, ...] = ()
    repository_root = _repository_root_for_config(path)
    authority_worktree = _issue_authority_worktree(path)
    if require_registered_worktree or authority_worktree is not None:
        try:
            registered_worktrees = _registered_worktree_paths(repository_root)
        except ValueError:
            if require_registered_worktree:
                raise
    config = read_issue_config(path)
    if not config:
        return (
            _require_registered_issue_authority(path, registered_worktrees)
            if require_registered_worktree
            else path
        )
    raw_worktree = config.get("worktree_path")
    if not isinstance(raw_worktree, str) or not raw_worktree.strip():
        return (
            _require_registered_issue_authority(path, registered_worktrees)
            if require_registered_worktree
            else path
        )
    main_worktree = registered_worktrees[0] if registered_worktrees else None
    if authority_worktree in registered_worktrees and authority_worktree != main_worktree:
        return _require_registered_issue_authority(path, registered_worktrees)
    worktree = Path(raw_worktree)
    if not worktree.is_absolute():
        worktree = repository_root / worktree
    worktree = worktree.resolve()
    if require_registered_worktree:
        if worktree not in registered_worktrees:
            raise ValueError("inventory worktree is not registered to the selected repository")
    issue_name = config.get("issue_name")
    if not isinstance(issue_name, str) or not issue_name.strip():
        issue_name = path.parent.name
    issue_path = Path(issue_name)
    if issue_path.is_absolute() or len(issue_path.parts) != 1 or issue_name in {"", ".", ".."}:
        raise ValueError("inventory issue name must identify one directory")
    lexical_issues_root = worktree / ".cafe" / "issues"
    lexical_candidate = lexical_issues_root / issue_name / "issue.yaml"
    if require_registered_worktree:
        _reject_issue_authority_symlinks(lexical_candidate)
    issues_root = lexical_issues_root.resolve()
    candidate = lexical_candidate.resolve()
    if not candidate.is_relative_to(issues_root):
        raise ValueError("inventory issue configuration escapes its worktree issue root")
    if candidate.exists():
        resolved_candidate = candidate.resolve()
        return (
            _require_registered_issue_authority(resolved_candidate, registered_worktrees)
            if require_registered_worktree
            else resolved_candidate
        )
    if require_registered_worktree:
        raise ValueError("registered inventory worktree has no issue policy authority")
    return path


def read_authoritative_issue_config(config_path: Path) -> Optional[Dict[str, Any]]:
    """Read policy and workflow metadata from the active issue authority."""
    return read_issue_config(resolve_issue_config_path(config_path))


def parse_issue_config_value(config_data: Optional[Dict[str, Any]], key: str) -> Optional[Any]:
    """Read a dotted or top-level key from parsed issue config data."""
    if not config_data:
        return None
    if "." in key:
        value: Any = config_data
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return None
            else:
                return None
        return value
    return config_data.get(key)


def read_issue_config_value(config_path: Path, key: str) -> Optional[Any]:
    """Read a value from issue.yaml by key."""
    return parse_issue_config_value(read_issue_config(config_path), key)


def resolve_issue_id(config_path: Path) -> Optional[str]:
    """Resolve issue_id from top-level or spec.issue_id, coerced to str."""
    issue_id = read_issue_config_value(config_path, "issue_id")
    if not issue_id:
        issue_id = read_issue_config_value(config_path, "spec.issue_id")
    if issue_id is None:
        return None
    return str(issue_id)
