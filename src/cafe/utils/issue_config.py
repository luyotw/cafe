"""Issue YAML config helpers extracted from legacy phase mixins."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


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


def _repository_root_for_config(config_path: Path) -> Path:
    resolved = config_path.resolve()
    for parent in resolved.parents:
        if parent.name == ".cafe":
            return parent.parent
    return Path.cwd().resolve()


def resolve_issue_config_path(config_path: Path) -> Path:
    """Resolve a repo inventory pointer to the active-worktree authority."""
    path = Path(config_path).resolve()
    config = read_issue_config(path)
    if not config:
        return path
    raw_worktree = config.get("worktree_path")
    if not isinstance(raw_worktree, str) or not raw_worktree.strip():
        return path
    worktree = Path(raw_worktree)
    if not worktree.is_absolute():
        worktree = _repository_root_for_config(path) / worktree
    issue_name = config.get("issue_name")
    if not isinstance(issue_name, str) or not issue_name.strip():
        issue_name = path.parent.name
    issue_path = Path(issue_name)
    if (
        issue_path.is_absolute()
        or len(issue_path.parts) != 1
        or issue_name in {"", ".", ".."}
    ):
        raise ValueError("inventory issue name must identify one directory")
    issues_root = (worktree / ".cafe" / "issues").resolve()
    candidate = (issues_root / issue_name / "issue.yaml").resolve()
    if not candidate.is_relative_to(issues_root):
        raise ValueError("inventory issue configuration escapes its worktree issue root")
    return candidate.resolve() if candidate.exists() else path


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
