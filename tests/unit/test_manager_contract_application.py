"""Public invariants for the canonical Manager contract API."""

from datetime import datetime, timezone
import json

from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
from tests.fixtures.delivery_contract import delivery_contract


def test_new_contract_uses_manager_names_and_manager_authority(tmp_path):
    proposal = {
        "delivery_contract": delivery_contract(),
        "locales": {"conversation": {"value": "en", "source": "playbook:standard"}},
        "confirmation_contract": {
            "user_required": ["spec", "plan"],
            "manager_confirmable": [],
            "mandatory_human_stops": ["spec", "plan"],
        },
        "reactive_user_handoffs": {
            "need_clarification": "user_required",
            "need_permission": "user_required",
            "alignment_checkpoint": "manager_resolvable_when_clear",
        },
        "phases": [{"name": "develop", "chain": [{"cli": "codex", "model": "gpt-5.6-sol"}]}],
        "proactive_review": {"phase_decisions": [{"phase": "develop", "decision": "not_required"}]},
        "manager": {"mode": "unattended"},
        "checkout": {"kind": "current_checkout"},
        "task_contract": {"user_required": [], "manager_confirmable": []},
    }
    result = activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="issue574",
            workflow_id="workflow-574",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )

    contract = json.loads((tmp_path / "manager" / "contract.json").read_text(encoding="utf-8"))
    assert result.created
    assert contract["schema_version"] == 8
    assert contract["manager"]["mode"] == "unattended"
    assert "driver" not in contract
    assert "manager_confirmable" in contract["confirmation_contract"]


def _manager_proposal(mode="unattended"):
    return {
        "delivery_contract": delivery_contract(),
        "locales": {"conversation": {"value": "en", "source": "playbook:standard"}},
        "confirmation_contract": {
            "user_required": ["spec", "plan"],
            "manager_confirmable": [],
            "mandatory_human_stops": ["spec", "plan"],
        },
        "reactive_user_handoffs": {
            "need_clarification": "manager_confirmable",
            "need_permission": "user_required",
            "alignment_checkpoint": "manager_resolvable_when_clear",
        },
        "phases": [{"name": "develop", "chain": [{"cli": "codex", "model": "gpt-5.6-sol"}]}],
        "proactive_review": {"phase_decisions": [{"phase": "develop", "decision": "not_required"}]},
        "manager": {"mode": mode},
        "checkout": {"kind": "current_checkout"},
        "task_contract": {"user_required": [], "manager_confirmable": []},
    }


def test_equivalent_dual_contracts_keep_the_legacy_directory_authoritative(tmp_path):
    from copy import deepcopy

    from cafe.driver._schema import build_initial_contract as build_driver_contract
    from cafe.driver._store import write_contract as write_driver_contract
    from cafe.manager._store import select_authority_directory

    proposal = _manager_proposal()
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="issue574",
            workflow_id="workflow-574",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )
    legacy = deepcopy(proposal)
    legacy["driver"] = legacy.pop("manager")
    for field in ("confirmation_contract", "task_contract"):
        owner = legacy[field]
        owner["driver_confirmable"] = owner.pop("manager_confirmable")
    legacy["reactive_user_handoffs"]["alignment_checkpoint"] = "driver_resolvable_when_clear"
    legacy["reactive_user_handoffs"]["need_clarification"] = "driver_confirmable"
    old_contract = build_driver_contract(
        proposal=legacy,
        issue_name="issue574",
        workflow_id="workflow-574",
        confirmed_by="user",
        confirmed_at="2026-09-28T00:00:00+00:00",
    )
    write_driver_contract(tmp_path, old_contract, expected_predecessor_sha256=None)

    assert select_authority_directory(tmp_path) == tmp_path / "driver"
