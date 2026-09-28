"""Manager-owned adapter for the mode-neutral settings dispatch port."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from cafe.settings import SettingUpdateRequest

from ._store import load_contract, select_authority_directory
from .api import ManagerSettingsUpdateResult, update_manager_settings


def update_manager_setting(request: SettingUpdateRequest) -> ManagerSettingsUpdateResult:
    """Resolve Manager identity and apply one complete Manager settings object."""
    if not isinstance(request.value, Mapping):
        raise ValueError("manager must be a JSON object")
    issue_dir = request.config_path.parent
    authority = select_authority_directory(issue_dir)
    manager_record = issue_dir / "manager" / "contract.json"
    driver_record = issue_dir / "driver" / "contract.json"
    if authority.name == "driver":
        if manager_record.exists() and driver_record.exists():
            raise ValueError(
                "Manager settings cannot update a legacy workflow with a parallel Manager record; "
                f"inspect {driver_record} and {manager_record}, then reconcile the selected authority"
            )
        from cafe.driver._store import load_contract as load_driver_contract
        from cafe.driver.api import update_driver_settings

        contract, _digest = load_driver_contract(issue_dir, allow_legacy_upgrade=True)
        result = update_driver_settings(
            issue_dir=issue_dir,
            issue_name=contract["identity"]["issue_name"],
            workflow_id=contract["identity"]["workflow_id"],
            driver=request.value,
            preview=request.preview,
        )
        return ManagerSettingsUpdateResult(
            result.status,
            MappingProxyType({"manager": result.changes["driver"]}),
            result.revision,
            result.contract_sha256,
        )
    contract, _digest = load_contract(issue_dir, allow_legacy_upgrade=True)
    return update_manager_settings(
        issue_dir=issue_dir,
        issue_name=contract["identity"]["issue_name"],
        workflow_id=contract["identity"]["workflow_id"],
        manager=request.value,
        preview=request.preview,
    )
