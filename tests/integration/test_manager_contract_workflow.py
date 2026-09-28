"""Manager and legacy contract journeys through the packaged entry adapter."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
from tests.fixtures.delivery_contract import delivery_contract

PROJECT_ROOT = Path(__file__).parents[2]
ENTRY_SCRIPT = (
    PROJECT_ROOT
    / "src/cafe/data/skills/use-cafe-workflow/scripts/validate_manager_entry.py"
)


def _entry_adapter():
    spec = importlib.util.spec_from_file_location("manager_entry_adapter", ENTRY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proposal():
    return {
        "delivery_contract": delivery_contract(),
        "locales": {"conversation": {"value": "en", "source": "playbook"}},
        "confirmation_contract": {
            "user_required": [],
            "manager_confirmable": [],
            "mandatory_human_stops": [],
        },
        "reactive_user_handoffs": {
            "need_clarification": "manager_confirmable",
            "need_permission": "user_required",
            "alignment_checkpoint": "manager_resolvable_when_clear",
        },
        "phases": [{"name": "develop", "chain": [{"cli": "codex", "model": "exact"}]}],
        "proactive_review": {"phase_decisions": [{"phase": "develop", "decision": "not_required"}]},
        "manager": {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
        "task_contract": {"user_required": [], "manager_confirmable": []},
    }


def _activate(issue_dir: Path):
    proposal = _proposal()
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue_dir,
            issue_name="journey",
            workflow_id="workflow-journey",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )
    return proposal


def test_manager_contract_survives_packaged_validation_and_detects_policy_change(tmp_path):
    issue_dir = tmp_path / "issue"
    proposal = _activate(issue_dir)
    facts = {"semantic_facts": {"effective_policy": proposal}}
    adapter = _entry_adapter()

    primary = adapter.validate_entry(
        issue_dir=issue_dir, issue_name="journey", workflow_id="workflow-journey", fresh_facts=facts
    )
    retry = adapter.validate_entry(
        issue_dir=issue_dir, issue_name="journey", workflow_id="workflow-journey", fresh_facts=facts
    )

    assert primary == retry
    assert primary["runtime"]["manager"]["mode"] == "unattended"
    assert (issue_dir / "manager" / "contract.json").is_file()
    assert not (issue_dir / "driver" / "contract.json").exists()

    changed = _proposal()
    changed["manager"] = {"mode": "attached", "poll_interval_seconds": 30}
    facts["semantic_facts"] = {"effective_policy": changed}
    with pytest.raises(ValueError):
        adapter.validate_entry(
            issue_dir=issue_dir,
            issue_name="journey",
            workflow_id="workflow-journey",
            fresh_facts=facts,
        )


def test_manager_settings_dispatch_updates_only_manager_contract(tmp_path):
    from cafe.settings import SettingUpdateRequest, dispatch_setting_update

    issue_dir = tmp_path / "issue"
    _activate(issue_dir)
    before = (issue_dir / "manager" / "contract.json").read_bytes()
    preview = dispatch_setting_update(
        "manager",
        SettingUpdateRequest(
            config_path=issue_dir / "issue.yaml",
            value={"mode": "attached", "poll_interval_seconds": 30},
            preview=True,
        ),
    )
    assert preview.status == "proposed"
    assert before == (issue_dir / "manager" / "contract.json").read_bytes()

    saved = dispatch_setting_update(
        "manager",
        SettingUpdateRequest(
            config_path=issue_dir / "issue.yaml",
            value={"mode": "attached", "poll_interval_seconds": 30},
        ),
    )

    assert saved.status == "saved"
    assert (issue_dir / "manager" / "contract.json").is_file()
    assert not (issue_dir / "driver" / "contract.json").exists()


def test_manager_settings_dispatch_updates_the_selected_legacy_authority(tmp_path):
    from copy import deepcopy

    from cafe.core.packet_io import canonical_json
    from cafe.driver._schema import build_initial_contract as build_driver_contract
    from cafe.settings import SettingUpdateRequest, dispatch_setting_update

    proposal = _proposal()
    legacy = deepcopy(proposal)
    legacy["driver"] = legacy.pop("manager")
    for field in ("confirmation_contract", "task_contract"):
        legacy[field]["driver_confirmable"] = legacy[field].pop("manager_confirmable")
    legacy["reactive_user_handoffs"]["need_clarification"] = "driver_confirmable"
    legacy["reactive_user_handoffs"]["alignment_checkpoint"] = "driver_resolvable_when_clear"
    issue_dir = tmp_path / "issue"
    driver_dir = issue_dir / "driver"
    driver_dir.mkdir(parents=True)
    old_contract = build_driver_contract(
        proposal=legacy,
        issue_name="journey",
        workflow_id="workflow-journey",
        confirmed_by="user",
        confirmed_at="2026-09-28T00:00:00+00:00",
    )
    contract_path = driver_dir / "contract.json"
    contract_path.write_bytes(canonical_json(old_contract))
    request = SettingUpdateRequest(
        config_path=issue_dir / "issue.yaml",
        value={"mode": "attached", "poll_interval_seconds": 30},
    )

    preview = dispatch_setting_update("manager", SettingUpdateRequest(
        config_path=request.config_path, value=request.value, preview=True
    ))
    assert preview.status == "proposed"
    assert preview.changes["manager"]["after"] == request.value
    assert json.loads(contract_path.read_text(encoding="utf-8")) == old_contract

    saved = dispatch_setting_update("manager", request)

    assert saved.status == "saved"
    assert saved.changes["manager"]["after"] == request.value
    updated = json.loads(contract_path.read_text(encoding="utf-8"))
    assert updated["driver"] == request.value
    assert not (issue_dir / "manager" / "contract.json").exists()
