"""Mode-neutral dispatch port for issue-scoped setting owners."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

SETTING_UPDATE_ENTRY_POINT_GROUP = "cafe.setting_updates"


@dataclass(frozen=True)
class SettingUpdateRequest:
    config_path: Path
    value: Any
    preview: bool = False


class SettingUpdateResult(Protocol):
    status: str
    changes: object


def _setting_entry_points() -> list[Any]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=SETTING_UPDATE_ENTRY_POINT_GROUP))
    return list(discovered.get(SETTING_UPDATE_ENTRY_POINT_GROUP, ()))


def dispatch_setting_update(path: str, request: SettingUpdateRequest) -> SettingUpdateResult:
    """Load and invoke exactly one package-declared owner adapter."""
    matches = [entry for entry in _setting_entry_points() if entry.name == path]
    if not matches:
        raise ValueError(f"unsupported or protected settings path: {path}")
    if len(matches) != 1:
        raise ValueError(f"settings owner is ambiguous: {path}")
    handler = matches[0].load()
    result = handler(request)
    if not isinstance(getattr(result, "status", None), str) or not hasattr(result, "changes"):
        raise ValueError(f"settings owner returned an invalid result: {path}")
    return result
