"""Mode-neutral dispatch port for issue-scoped setting owners."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

SETTING_UPDATE_ENTRY_POINT_GROUP = "cafe.setting_updates"
SETTING_UPDATE_MANIFEST = "data/setting_updates.json"


@dataclass(frozen=True)
class SettingUpdateRequest:
    config_path: Path
    value: Any
    preview: bool = False


class SettingUpdateResult(Protocol):
    status: str
    changes: object


@dataclass(frozen=True)
class _DeclaredSettingOwner:
    name: str
    value: str

    def load(self) -> Any:
        module_name, separator, attribute = self.value.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError(f"settings owner declaration is invalid: {self.name}")
        return getattr(import_module(module_name), attribute)


def _declared_setting_owners() -> list[_DeclaredSettingOwner]:
    resource = files("cafe").joinpath(SETTING_UPDATE_MANIFEST)
    try:
        document = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("settings owner manifest is unreadable") from exc
    if not isinstance(document, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in document.items()
    ):
        raise ValueError("settings owner manifest must map paths to adapters")
    return [_DeclaredSettingOwner(name, value) for name, value in document.items()]


def _setting_entry_points() -> list[Any]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        external = list(discovered.select(group=SETTING_UPDATE_ENTRY_POINT_GROUP))
    else:
        external = list(discovered.get(SETTING_UPDATE_ENTRY_POINT_GROUP, ()))
    return [*_declared_setting_owners(), *external]


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
