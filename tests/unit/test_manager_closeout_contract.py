"""Closeout choice is a user-confirmed Manager policy independent of delivery facts."""

import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from cafe.manager._schema import build_initial_contract, validate_contract, freshness_semantic_facts
from cafe.manager.delivery import delivery_result_steps
from tests.unit.test_manager_contract_application import _manager_proposal


def proposal():
    value = _manager_proposal()
    value["delivery_contract"].update(schema_version=6)
    value["delivery_contract"].pop("closeout_plan")
    value["closeout_contract"] = {
        "schema_version": 1,
        "choice": "cleanup",
        "plan": {"cleanup": [{"argv": ["cafe", "close", "--archive-only"]}]},
        "delivery_result": {
            "step": "deliver",
            "task_id": "delivery-outcome",
            "artifact": "delivery_result",
        },
    }
    return value


def build(value, confirmed_by="user"):
    return build_initial_contract(
        proposal=value,
        issue_name="topic",
        workflow_id="wf",
        confirmed_by=confirmed_by,
        confirmed_at=datetime.now(timezone.utc).isoformat(),
    )


def test_separate_closeout_is_required_hashed_and_retained_in_freshness():
    value = proposal()
    contract = build(value)
    assert "closeout_plan" not in contract["delivery_contract"]
    assert (
        freshness_semantic_facts(contract)["effective_policy"]["closeout_contract"]
        == value["closeout_contract"]
    )
    changed = deepcopy(contract)
    changed["closeout_contract"]["choice"] = "leave"
    with pytest.raises(ValueError, match="digest"):
        validate_contract(changed)
    value.pop("closeout_contract")
    with pytest.raises(ValueError, match="requires.*closeout"):
        build(value)


@pytest.mark.parametrize("bad", ["version", "empty_cleanup", "unexpected", "legacy", "confirmer"])
def test_invalid_or_unconfirmed_closeout_never_acquires_authority(bad):
    value = proposal()
    if bad == "version":
        value["closeout_contract"]["schema_version"] = 2
    elif bad == "empty_cleanup":
        value["closeout_contract"]["plan"]["cleanup"] = []
    elif bad == "unexpected":
        value["closeout_contract"]["plan"]["deliver"] = []
    elif bad == "legacy":
        value["delivery_contract"] = _manager_proposal()["delivery_contract"]
    with pytest.raises(ValueError):
        build(value, "manager" if bad == "confirmer" else "user")


@pytest.mark.parametrize("approval_step", ["ship", "review_action"])
def test_delivery_result_owner_survives_same_phase_permission_and_renaming(approval_step):
    graph = {
        "steps": {
            "ship": {
                "output_artifact": "receipt",
                "delivery": {
                    "approval_step": approval_step,
                    "result_task": "accept-results",
                    "result_artifact": "receipt",
                },
            }
        }
    }
    assert delivery_result_steps(graph) == {"ship"}
    graph["steps"]["ship"]["output_artifact"] = "draft"
    assert not delivery_result_steps(graph)


def test_defaulted_playbooks_remain_eligible_in_kickoff_catalog(tmp_path):
    from pathlib import Path
    from tests.unit._kickoff_test_support import load_kickoff_module

    module = load_kickoff_module("kickoff_catalog")
    root = Path(__file__).resolve().parents[2]
    args = dict(
        project_root=root,
        global_root=tmp_path / "global",
        builtin_root=root / "src/cafe/data",
        cache_file=tmp_path / "catalog.json",
        selected_id="standard",
    )
    for light in [True, False]:
        result = module.discover_index(**args, lightweight=light)
        assert not result["diagnostics"], result["diagnostics"]
        assert len(result["candidates"]) == 1 and result["candidates"][0]["eligible"]


@pytest.mark.parametrize("completed", [False, True])
def test_pre_upgrade_missing_verification_preserves_only_completed_acceptance(
    tmp_path, monkeypatch, completed
):
    import json
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.delivery.contracts import approve_selection, digest, DeliveryBinding
    from cafe.delivery.records import ActionStore
    from cafe.delivery.selection import save_shown_proposal, validate_response
    from cafe.manager.closeout import accepted_delivery_result
    from tests.unit.test_development_delivery import proposal as action_proposal, authority

    p = action_proposal(proposals=[], verification=None)
    records = HumanTaskRecordStore(tmp_path)
    approval = records.materialize(
        workflow_id=p.workflow_id,
        step=p.approval_step,
        iteration=1,
        trigger="need_permission",
        policy_id="delivery-review",
        prompt=f"Action proposal SHA256: {p.digest}",
        expected_result={"input_schema": "decision"},
        continuations={"integrate_only": "deliver"},
        assignee_type="user",
    )
    save_shown_proposal(tmp_path, approval, p)
    result = records.complete(
        workflow_id=p.workflow_id,
        task_id=approval.id,
        payload={"decision": "integrate_only"},
        source="legacy",
    )
    auth = authority(p, "integrate_only", "")
    auth.update(task_id=approval.id, result_id=result.id)
    snapshot = approve_selection(p, auth)
    store = ActionStore(tmp_path, snapshot)
    with store.locked():
        store.finish("integration", {"state": "succeeded", "commit": "c" * 40})
    report = {
        "snapshot": snapshot.digest,
        "complete": True,
        "remaining": [],
        "actions": {"integration": store.read("integration")},
        "previous_results": {},
    }
    (store.directory / "result.json").write_text(json.dumps(report))
    task = records.materialize(
        workflow_id=p.workflow_id,
        step="ship",
        iteration=1,
        trigger="confirm_output",
        policy_id="accept-results",
        prompt=f"Action snapshot SHA256: {snapshot.digest}\nDelivery result SHA256: {digest(report)}",
        expected_result={"input_schema": "decision"},
        continuations={"confirm": "finalize"},
        assignee_type="user",
    )
    monkeypatch.setattr("cafe.delivery.selection.validate_source_identity", lambda *a: None)
    if completed:
        records.complete(
            workflow_id=p.workflow_id,
            task_id=task.id,
            payload={"decision": "confirm"},
            source="legacy",
        )
        assert (
            accepted_delivery_result(
                tmp_path, p.workflow_id, binding={"step": "ship", "task_id": "accept-results"}
            )["task_id"]
            == task.id
        )
    else:
        binding = DeliveryBinding(
            actions_artifact="actions",
            result_artifact="result",
            approval_step=p.approval_step,
            result_task="accept-results",
            correction_step="develop",
        )
        with pytest.raises(ValueError, match="verification"):
            validate_response(tmp_path, binding, task, {"decision": "confirm"})
        assert records.get_result(task.id) is None
        assert accepted_delivery_result(tmp_path, p.workflow_id) is None


@pytest.mark.parametrize("damage", ["workflow", "version", "digest", "binding", "symlink"])
def test_delivery_result_projection_rejects_changed_identity_and_unsafe_files(tmp_path, damage):
    from cafe.delivery.closeout import read_result_contract
    issue = tmp_path / "issue"
    directory = issue / "delivery"
    directory.mkdir(parents=True)
    value = {"version": 1, "workflow_id": "workflow", "contract_sha256": "a" * 64,
             "delivery_result": {"step": "ship", "task_id": "accept", "artifact": "result"}}
    path = directory / "result-contract.json"
    path.write_text(json.dumps(value))
    assert read_result_contract(issue, "workflow") == value["delivery_result"]
    if damage == "workflow":
        value["workflow_id"] = "other"
    elif damage == "version":
        value["version"] = True
    elif damage == "digest":
        value["contract_sha256"] = "stale"
    elif damage == "binding":
        value["delivery_result"]["task_id"] = ""
    elif damage == "symlink":
        target = tmp_path / "outside.json"
        target.write_text(json.dumps(value))
        path.unlink()
        path.symlink_to(target)
    if damage != "symlink":
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_result_contract(issue, "workflow")
