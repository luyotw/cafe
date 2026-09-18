"""Driver-owned adapter for the mode-neutral settings dispatch port."""

from __future__ import annotations

from collections.abc import Mapping

from cafe.settings import SettingUpdateRequest

from ._store import load_contract
from .api import DriverSettingsUpdateResult, update_driver_settings


def update_driver_setting(request: SettingUpdateRequest) -> DriverSettingsUpdateResult:
    """Resolve Driver identity and apply one complete Driver settings object."""
    if not isinstance(request.value, Mapping):
        raise ValueError("driver must be a JSON object")
    issue_dir = request.config_path.parent
    contract, _digest = load_contract(issue_dir, allow_legacy_upgrade=True)
    return update_driver_settings(
        issue_dir=issue_dir,
        issue_name=contract["identity"]["issue_name"],
        workflow_id=contract["identity"]["workflow_id"],
        driver=request.value,
        preview=request.preview,
    )
