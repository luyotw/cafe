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
