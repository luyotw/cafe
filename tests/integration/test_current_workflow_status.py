"""Read-only status journeys using durable state and arbitrary phase names."""

import json

import pytest
from typer.testing import CliRunner

from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.services.status_service import StatusService
from cafe.ui.cli import app


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(StatusService, "get_current_issue", lambda self: "demo")
    monkeypatch.setattr("cafe.ui.commands.workflow._load_issue_step_names", lambda _: ["package"])
    issue = tmp_path / ".cafe/issues/demo"
    iteration = issue / "package/iteration_001"
    iteration.mkdir(parents=True)
    (iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "timestamp": "2026-09-26T09:00:00+08:00",
                "end_time": "2026-09-26T09:01:00+08:00",
                "status_code": "confirm_output",
            }
        )
    )
    return issue


def write_state(issue, *, owner="user", step="user", intent="confirm_output", events=()):
    baton = {"version": 1, "to_owner": owner, "to_step": step, "intent": intent}
    state = {
        "schema_version": 4,
        "workflow_id": "workflow-demo",
        "playbook_id": "custom",
        "current_step": step,
        "handoff_contract": {**baton, "from_step": "package"},
        "events": list(events),
    }
    (issue / "blackboard.json").write_text(json.dumps(state))
    (issue / "next_step.txt").write_text(json.dumps(baton))


def materialize(issue, *, workflow_id="workflow-demo", step="package", trigger="confirm_output"):
    task = HumanTaskRecordStore(issue).materialize(
        workflow_id=workflow_id,
        step=step,
        iteration=1,
        trigger=trigger,
        policy_id="artifact-review",
        prompt="Review the prepared package.",
        expected_result={"input_schema": "decision"},
        continuations={"confirm": "_done"},
        assignee_type="user",
    )
    path = issue / "blackboard.json"
    raw = json.loads(path.read_text())
    raw["events"].append(
        {
            "step": step,
            "event_type": (
                "agent_execution_task_materialized"
                if trigger == "agent_execution_interrupted"
                else "human_task_materialized"
            ),
            "data": {"step": step, "trigger": trigger, "task_id": task.id},
        }
    )
    path.write_text(json.dumps(raw))
    return task


def snapshot(issue):
    return {str(p.relative_to(issue)): p.read_bytes() for p in issue.rglob("*") if p.is_file()}


def invoke_unchanged(issue):
    before = snapshot(issue)
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert snapshot(issue) == before
    return result.stdout


def test_completed_iteration_still_waits_for_exact_user_task(workflow):
    write_state(workflow)
    task = materialize(workflow)
    output = invoke_unchanged(workflow)
    assert "State: Waiting for user" in output
    assert "Workflow: workflow-demo" in output
    assert "Step: package" in output
    assert "Owner: user" in output
    assert "Reason: confirm output" in output
    assert f"cafe task inspect {task.id}" in output
    assert "Workflow Status" in output
    assert "cafe task complete" not in output


def test_publication_failure_reports_reason_without_recommending_retry(workflow):
    write_state(
        workflow,
        owner="agent",
        step="package",
        intent="await_agent",
        events=[
            {"step": "package", "event_type": "step_completed", "data": {}},
            {
                "step": "package",
                "event_type": "workflow_paused",
                "data": {"reason": "publish_error", "status_code": "INTERRUPTED"},
            },
            {"step": "package", "event_type": "workflow_event_callback_enqueued", "data": {}},
        ],
    )
    output = invoke_unchanged(workflow)
    assert "State: Paused" in output
    assert "Reason: publish_error" in output
    assert "cafe show package output" in output
    assert "--execute" not in output


def test_resumed_step_does_not_inherit_old_publication_failure(workflow):
    write_state(
        workflow,
        owner="agent",
        step="package",
        intent="await_agent",
        events=[
            {
                "step": "package",
                "event_type": "workflow_paused",
                "data": {"reason": "publish_error", "status_code": "INTERRUPTED"},
            },
            {"step": "package", "event_type": "step_started", "data": {}},
        ],
    )
    output = invoke_unchanged(workflow)
    assert "State: Agent step in progress" in output
    assert "publish_error" not in output


def test_terminal_workflow_is_explicit(workflow):
    write_state(workflow, owner="done", step="done", intent="workflow_complete")
    output = invoke_unchanged(workflow)
    assert "State: Completed" in output
    assert "Reason: Workflow completed" in output
    assert "Next:" not in output


@pytest.mark.parametrize("file", ["blackboard.json", "next_step.txt", "human_tasks.json"])
@pytest.mark.parametrize("content", ["{broken", "[]"])
def test_malformed_records_are_unknown_without_edits(workflow, file, content):
    write_state(workflow)
    (workflow / file).write_text(content)
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert "cafe task inspect" not in output
    assert "State: Completed" not in output


@pytest.mark.parametrize("file", ["blackboard.json", "next_step.txt"])
def test_missing_state_does_not_infer_completion_from_iteration(workflow, file):
    write_state(workflow)
    (workflow / file).unlink()
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert "State: Completed" not in output


@pytest.mark.parametrize(
    "workflow_id,step",
    [
        ("other-workflow", "package"),
        ("workflow-demo", "other-step"),
    ],
)
def test_stale_task_is_not_offered_for_completion(workflow, workflow_id, step):
    write_state(workflow)
    task = materialize(workflow, workflow_id=workflow_id, step=step)
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert f"cafe task inspect {task.id}" not in output


def test_conflicting_terminal_pointer_does_not_hide_pending_task(workflow):
    write_state(workflow, owner="done", step="done", intent="workflow_complete")
    materialize(workflow)
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert "State: Completed" not in output


def test_disagreeing_handoffs_fail_closed(workflow):
    write_state(workflow)
    (workflow / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "to_owner": "done",
                "to_step": "done",
                "intent": "workflow_complete",
            }
        )
    )
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output


@pytest.mark.parametrize(
    "changes",
    [
        {"to_owner": "done", "to_step": "package", "intent": "workflow_complete"},
        {"to_owner": "agent", "to_step": "not-declared", "intent": "await_agent"},
        {"version": 99},
    ],
)
def test_invalid_handoff_cannot_claim_a_valid_state(workflow, changes):
    write_state(workflow)
    raw = json.loads((workflow / "blackboard.json").read_text())
    raw["handoff_contract"].update(changes)
    baton = json.loads((workflow / "next_step.txt").read_text())
    baton.update(changes)
    (workflow / "blackboard.json").write_text(json.dumps(raw))
    (workflow / "next_step.txt").write_text(json.dumps(baton))
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert "State: Completed" not in output


def test_old_confirmation_is_not_a_current_clarification(workflow):
    write_state(workflow, intent="need_clarification")
    task = materialize(workflow)
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert f"cafe task inspect {task.id}" not in output


def test_older_iteration_task_is_not_offered(workflow):
    write_state(workflow)
    task = materialize(workflow)
    (workflow / "package/iteration_002").mkdir()
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert f"cafe task inspect {task.id}" not in output


def test_task_must_match_latest_materialization(workflow):
    write_state(workflow)
    task = materialize(workflow)
    path = workflow / "blackboard.json"
    raw = json.loads(path.read_text())
    raw["events"].append(
        {
            "step": "package",
            "event_type": "human_task_materialized",
            "data": {"task_id": "other-task"},
        }
    )
    path.write_text(json.dumps(raw))
    output = invoke_unchanged(workflow)
    assert "State: Unknown" in output
    assert f"cafe task inspect {task.id}" not in output


def test_manual_handoff_can_reuse_a_recovery_task(workflow):
    write_state(
        workflow,
        intent="manual_handoff",
        events=[
            {
                "step": "package",
                "event_type": "human_task_materialized",
                "data": {"task_id": "previously-completed-task"},
            },
        ],
    )
    task = materialize(workflow, trigger="agent_execution_interrupted")
    output = invoke_unchanged(workflow)
    assert "State: Waiting for user" in output
    assert f"cafe task inspect {task.id}" in output
    assert "Reason: agent execution interrupted" in output
