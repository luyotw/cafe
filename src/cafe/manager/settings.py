"""Manager-owned adapter for the mode-neutral settings dispatch port."""

from __future__ import annotations

from collections.abc import Mapping

from cafe.settings import SettingUpdateRequest

from ._store import load_contract
from .api import ManagerSettingsUpdateResult, update_manager_settings


def update_manager_setting(request: SettingUpdateRequest) -> ManagerSettingsUpdateResult:
    """Resolve Manager identity and apply one complete Manager settings object."""
    if not isinstance(request.value, Mapping):
        raise ValueError("manager must be a JSON object")
    issue_dir = request.config_path.parent
    contract, _digest = load_contract(issue_dir, allow_legacy_upgrade=True)
    return update_manager_settings(
        issue_dir=issue_dir,
        issue_name=contract["identity"]["issue_name"],
        workflow_id=contract["identity"]["workflow_id"],
        manager=request.value,
        preview=request.preview,
    )
