"""Actual user replies, host effects and terminal authority share two normal stops."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.status_codes import PhaseStatusCode
from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.integration.test_development_delivery_operations import git
from tests.unit._kickoff_test_support import load_kickoff_module
from tests.unit.test_manager_contract_application import _manager_proposal


@pytest.fixture
def local_action(tmp_path):
    return journey.local_action.__wrapped__(tmp_path)


@pytest.fixture
def fake_github(local_action, tmp_path, monkeypatch):
    return journey.fake_github.__wrapped__(local_action, tmp_path, monkeypatch)


def activate_closeout(context, command):
    root, _, issue, state = context[:4]
    proposal = _manager_proposal()
    proposal["delivery_contract"]["schema_version"] = 5
    proposal["delivery_contract"]["terminal_selection"] = "delivery_outcome"
    proposal["delivery_contract"]["closeout_plan"] = {"cleanup": [{"argv": command}]}
    proposal["confirmation_contract"] = {
        "user_required": [],
        "manager_confirmable": [],
        "mandatory_human_stops": [context[7]["step_name"], context[-1]],
    }
    activate_confirmed_contract(
        ActivateConfirmedContract(
            issue_dir=issue,
            issue_name="journey",
            workflow_id=state.workflow_id,
            confirmed_by="user",
            confirmed_at=datetime.now(timezone.utc),
            proposal=proposal,
        )
    )


def terminal_reply(context, decision):
    _, _, issue, state, phase, data, engine, kwargs, _, delivery = context
    journey.hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    task = HumanTaskRecordStore(issue).tasks()[-1]
    assert "Closeout plan SHA256:" in task.prompt
    journey.pause(issue, state, delivery)
    reply = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=delivery,
        trigger="confirm_output",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": decision},
        source="test",
    )
    return reply


@pytest.mark.parametrize("renamed", [False, True])
@pytest.mark.parametrize("missing_projection", [False, True])
@pytest.mark.parametrize(
    "decision,choice",
    [
        ("confirm_cleanup", "cleanup"),
        ("confirm_archive", "archive"),
        ("confirm", "leave"),
    ],
)
def test_two_actual_replies_authorize_delivery_and_terminal_selection(
    local_action, tmp_path, renamed, decision, choice, missing_projection
):
    context = journey.setup_action(local_action, tmp_path, renamed=renamed)
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    marker = tmp_path / "cleanup-effect"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('done')",
    ]
    activate_closeout(context, command)
    assert "Host capability review SHA256:" in task.prompt
    journey.approve_action(context)  # First user reply includes the exact host boundary.
    result = journey.hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    assert result.override_status_code is None
    assert result.context_updates["delivery_complete"] == "true"
    assert git(dest, "rev-parse", "HEAD") == git(root, "rev-parse", "HEAD")
    records = HumanTaskRecordStore(issue)
    host_tasks = [task for task in records.tasks() if task.capability_approval]
    assert len(host_tasks) == 1
    assert all(task.status == HumanTaskStatus.COMPLETED for task in host_tasks)
    assert records.get_result(host_tasks[0].id).payload["authority"]["task_id"] == task.id
    assert not marker.exists()

    reply = terminal_reply(context, decision)  # Second user reply also selects terminal work.
    assert reply.target == "done", reply.rejection
    store = BlackboardStore(issue)
    store.set_current_step(state, reply.target)  # Apply the declared terminal runtime transition.
    inspect = load_kickoff_module("inspect_delivery_closeout").inspect
    selection = inspect(issue, state.workflow_id)
    assert selection["status"] == "accepted" and selection["selection"]["choice"] == choice
    # Derived host results are distinct durable receipts, not extra human replies.
    assert len([result for result in records.results() if result.source == "test"]) == 2

    script = (
        Path(__file__).resolve().parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/execute_closeout.py"
    )
    argv = [
        sys.executable,
        str(script),
        "--project-root",
        str(root),
        "--issue-dir",
        str(issue),
        "--issue-name",
        "journey",
        "--workflow-id",
        state.workflow_id,
    ]
    initialized = subprocess.run([*argv, "--initialize"], capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stderr
    if missing_projection:
        (issue / "delivery" / "closeout.json").unlink()
    executed = subprocess.run(
        [*argv, "--execute", "--stage", "cleanup", "--index", "0"], capture_output=True, text=True
    )
    if choice == "cleanup" and not missing_projection:
        assert executed.returncode == 0, executed.stderr
        assert marker.read_text() == "done"
        replay = subprocess.run(
            [*argv, "--execute", "--stage", "cleanup", "--index", "0"],
            capture_output=True,
            text=True,
        )
        assert replay.returncode != 0
    else:
        assert executed.returncode != 0
        assert not marker.exists()
    assert len([result for result in records.results() if result.source == "test"]) == 2


def test_selected_followups_share_the_same_user_authorization(local_action, tmp_path, fake_github):
    context = journey.setup_action(
        local_action,
        tmp_path,
        github=True,
        proposals=[
            {"id": "FUP-001", "title": "One", "body": "Selected draft", "evidence": "file:1"},
            {"id": "FUP-002", "title": "Two", "body": "Unselected", "evidence": "file:2"},
        ],
    )
    journey.approve_action(context, selected="FUP-001")
    result = journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    assert (
        result.override_status_code is None
        and result.context_updates["delivery_complete"] == "true"
    )
    records = HumanTaskRecordStore(context[2])
    assert len([row for row in records.results() if row.source == "test"]) == 1
    host = [row for row in records.tasks() if row.capability_approval]
    assert len(host) == 2 and all(row.status == HumanTaskStatus.COMPLETED for row in host)
    assert json.loads(fake_github.read_text())["effects"] == ["merge", "issue"]
    rerun = journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    assert rerun.context_updates["delivery_complete"] == "true"
    assert json.loads(fake_github.read_text())["effects"] == ["merge", "issue"]


@pytest.mark.parametrize("approval", ["required", "not_required"])
def test_changed_manifest_needs_fresh_action_review_before_any_effect(
    local_action, tmp_path, monkeypatch, approval
):
    from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry

    context = journey.setup_action(local_action, tmp_path)
    journey.approve_action(context)
    root, dest, issue = context[:3]
    registry = dict(load_capability_registry(default_capability_definition_dirs(root)))
    registry["cafe.branch.integrate"] = registry["cafe.branch.integrate"].model_copy(
        update={"approval": approval, "version": 2}
    )
    monkeypatch.setattr("cafe.core.capabilities.load_capability_registry", lambda *args: registry)
    result = journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    assert result.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert "fresh action review" in context[7]["output_file"].read_text()
    assert git(dest, "rev-parse", "HEAD") == local_action[3].proposal.target_oid
    assert not any(row.capability_approval for row in HumanTaskRecordStore(issue).tasks())


def test_changed_terminal_plan_rejects_acceptance_without_cleanup_effect(local_action, tmp_path):
    context = journey.setup_action(local_action, tmp_path)
    activate_closeout(context, ["cafe", "close", "--archive-only"])
    journey.approve_action(context)
    journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    journey.hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    issue, state = context[2:4]
    task = HumanTaskRecordStore(issue).tasks()[-1]
    path = issue / "delivery" / "closeout.json"
    plan = json.loads(path.read_text())
    plan["cleanup"] = [["gh", "issue", "close", "999", "--repo", "owner/repo"]]
    path.write_text(json.dumps(plan))
    journey.pause(issue, state, context[-1])
    rejected = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=context[5],
        blackboard=state,
        from_step=context[-1],
        trigger="confirm_output",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "confirm_cleanup",
        },
        source="test",
    )
    assert rejected.rejection is not None and rejected.target is None
    assert HumanTaskRecordStore(issue).get_result(task.id) is None


def test_explicit_host_denial_is_not_overwritten_by_bundled_authority(local_action, tmp_path):
    from cafe.core.capabilities import (
        default_capability_definition_dirs,
        load_capability_registry,
        evaluate_capability_request,
    )
    from cafe.core.capability_approvals import CapabilityApprovalService
    from cafe.delivery.contracts import DeliveryBinding
    from cafe.delivery.selection import approved_snapshot
    from cafe.delivery.service import action_request

    context = journey.setup_action(local_action, tmp_path)
    journey.approve_action(context)
    root, dest, issue, state = context[:4]
    snapshot = approved_snapshot(
        issue,
        workflow_id=state.workflow_id,
        binding=DeliveryBinding.model_validate(context[7]["step_def"]["delivery"]),
    )
    registry = load_capability_registry(default_capability_definition_dirs(root))
    evaluated = evaluate_capability_request(
        registry, action_request(registry, snapshot, issue, "integration")
    )
    service = CapabilityApprovalService(
        issue_dir=issue, workflow_id=state.workflow_id, step=context[-1], iteration=1
    )
    task = service.request_approval(request=evaluated.request, manifest=evaluated.manifest)
    approval = service.inspect(task.id)
    service.record_decision(
        task.id,
        {
            "decision": "deny",
            "workflow_id": state.workflow_id,
            "task_id": task.id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
        },
    )
    result = journey.hook(context[6], "prepare_input", "DevelopmentDeliveryExecutor", context[7])
    assert result.context_updates["delivery_complete"] == "false"
    assert service.inspect(task.id)["state"] == "denied"
    assert git(dest, "rev-parse", "HEAD") == local_action[3].proposal.target_oid
