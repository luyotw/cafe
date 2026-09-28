"""Public invariants for the canonical Manager contract API."""

from datetime import datetime, timezone
import json
from pathlib import Path

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
    from cafe.core.packet_io import canonical_json
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
    legacy_dir = tmp_path / "driver"
    legacy_dir.mkdir()
    (legacy_dir / "contract.json").write_bytes(canonical_json(old_contract))

    assert select_authority_directory(tmp_path) == tmp_path / "driver"


def test_conflicting_dual_contracts_stop_before_selecting_a_directory(tmp_path):
    from cafe.driver._schema import build_initial_contract as build_driver_contract
    from cafe.core.packet_io import canonical_json
    from cafe.manager._store import select_authority_directory

    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="issue574",
            workflow_id="workflow-574",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal={**_manager_proposal(), "manager": {"mode": "attached", "poll_interval_seconds": 30}},
        )
    )
    legacy = _manager_proposal("unattended")
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
    legacy_dir = tmp_path / "driver"
    legacy_dir.mkdir()
    (legacy_dir / "contract.json").write_bytes(canonical_json(old_contract))

    import pytest

    with pytest.raises(ValueError, match="contracts conflict"):
        select_authority_directory(tmp_path)


def test_manager_task_ownership_uses_confirmed_declaration(tmp_path: Path):
    from tests.unit.test_driver_task_authority import _task
    from cafe.manager.task_authority import decide_task_authority

    proposal = _manager_proposal()
    proposal["task_contract"] = {
        "user_required": [],
        "manager_confirmable": [{"phase": "develop", "task_id": "known-answer"}],
    }
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="issue500",
            workflow_id="workflow500",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal=proposal,
        )
    )
    from cafe.manager._store import load_contract

    confirmed, _digest = load_contract(tmp_path)
    result = decide_task_authority(
        task=_task(),
        contract=confirmed,
        current_task_id="durable-1",
        response={"task": "known-answer", "human_task_id": "durable-1", "answers": {"q1": ["A", "B"]}},
        evidence={
            "basis": "confirmed_exact",
            "exhaustive": True,
            "citations": [
                {"field": "answers.q1", "value": value, "source": "contract", "excerpt": "A and B"}
                for value in ("A", "B")
            ],
        },
        confirmed_sources={"contract": "All required outcomes: A and B"},
    )
    assert result["resolution_owner"] == "manager_confirmable"
    assert result["allowed"] is True


def test_legacy_store_cannot_create_a_second_contract_beside_manager_authority(tmp_path):
    import pytest

    from cafe.driver._store import write_contract as write_driver_contract

    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=tmp_path,
            issue_name="issue574",
            workflow_id="workflow-574",
            confirmed_by="user",
            confirmed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            proposal=_manager_proposal(),
        )
    )

    with pytest.raises(ValueError, match="Manager contract is authoritative"):
        write_driver_contract(tmp_path, {}, expected_predecessor_sha256=None)
    assert not (tmp_path / "driver" / "contract.json").exists()
