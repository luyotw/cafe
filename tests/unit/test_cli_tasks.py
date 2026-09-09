"""CLI contract tests for repository task list and inspection."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_tasks import agent_execution_interrupted_human_task
from cafe.ui.cli import app

runner = CliRunner()


def _task_repo(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-a"
    issue_dir.mkdir(parents=True)
    state = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="standard")
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
        trigger="confirm_output",
        policy_id="output-review",
        prompt="Review this output",
        expected_result={"input_schema": "decision"},
        continuations={"confirm": "plan"},
        assignee_type="user",
        assignee_id="alice",
    )
    return issue_dir, task


def _legacy_interrupted_task_repo(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "legacy-interrupted"
    iteration_dir = issue_dir / "spec" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "cli": "codex",
                "model": "gpt-5-test",
                "session_id": "old-session",
            }
        ),
        encoding="utf-8",
    )
    (iteration_dir / "error.json").write_text(
        json.dumps({"error_type": "rate_limit"}),
        encoding="utf-8",
    )
    (iteration_dir / "user_input.md").write_text(
        "Keep the previously confirmed requirement.",
        encoding="utf-8",
    )
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    blackboards = BlackboardStore(issue_dir)
    state = blackboards.load_or_create("spec", playbook_id="standard")
    blackboards.set_current_step(state, "user")
    blackboards.update_handoff_contract(
        state,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.MANUAL_HANDOFF,
        status_code="INTERRUPTED",
        source="workflow.agent_execution_interrupted",
    )
    assert state.handoff_contract is not None
    policy, _binding = agent_execution_interrupted_human_task(step_name="spec")
    legacy_expected = policy.model_dump(mode="json")
    legacy_expected["decisions"] = [
        item for item in legacy_expected["decisions"] if item["id"] == "retry"
    ]
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
        trigger="agent_execution_interrupted",
        policy_id="agent-execution-interrupted",
        prompt="Agent execution was interrupted. Retry when ready.",
        expected_result=legacy_expected,
        continuations={"retry": "spec"},
        assignee_type="user",
        handoff_key=":".join(
            (
                "user-handoff",
                state.workflow_id,
                "spec",
                HandoffIntent.MANUAL_HANDOFF.value,
                state.handoff_contract.created_at,
            )
        ),
    )
    return issue_dir, iteration_dir, task


def test_task_group_is_discoverable_with_three_operations() -> None:
    """Test List U7: the public CLI exposes one stable task command group."""
    result = runner.invoke(app, ["task", "--help"])

    assert result.exit_code == 0
    assert all(command in result.stdout for command in ("ls", "inspect", "complete"))


def test_list_json_envelope_supports_filters(tmp_path: Path, monkeypatch) -> None:
    """Test List U7/I1/I6: JSON listing uses public projections and AND filters."""
    _issue_dir, task = _task_repo(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "task",
            "ls",
            "--assignee",
            "alice",
            "--workflow",
            task.workflow_id,
            "--step",
            "spec",
            "--due-state",
            "unscheduled",
            "--json",
        ],
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["operation"] == "list"
    assert [item["id"] for item in envelope["data"]["tasks"]] == [task.id]
    assert envelope["error"] is None


def test_inspect_human_and_json_share_task_identity(tmp_path: Path, monkeypatch) -> None:
    """Test List U7/I2: both presentations are derived from one selected detail."""
    _issue_dir, task = _task_repo(tmp_path, monkeypatch)

    human = runner.invoke(app, ["task", "inspect", task.id])
    machine = runner.invoke(app, ["task", "inspect", task.id, "--json"])

    assert human.exit_code == machine.exit_code == 0
    assert task.id in human.stdout
    assert json.loads(machine.stdout)["data"]["task"]["id"] == task.id


def test_inspect_upgrades_legacy_interrupted_task_with_fresh_session_choice(
    tmp_path: Path, monkeypatch
) -> None:
    """A pending task created by an older runtime gains the additive recovery outcome."""
    issue_dir, _iteration_dir, task = _legacy_interrupted_task_repo(tmp_path, monkeypatch)

    first = runner.invoke(app, ["task", "inspect", task.id, "--json"])
    second = runner.invoke(app, ["task", "inspect", task.id, "--json"])

    assert first.exit_code == second.exit_code == 0
    detail = json.loads(first.stdout)["data"]["task"]
    assert [item["id"] for item in detail["expected_result"]["decisions"]] == [
        "retry",
        "retry_fresh_session",
    ]
    assert detail["continuations"] == {
        "retry": "spec",
        "retry_fresh_session": "spec",
    }
    refreshed = [
        event
        for event in HumanTaskRecordStore(issue_dir).lifecycle_events()
        if event.event_type == "contract_refreshed"
    ]
    assert len(refreshed) == 1


def test_fresh_session_completion_preserves_prior_session_and_user_input(
    tmp_path: Path, monkeypatch
) -> None:
    """The user-owned choice creates durable rotation evidence without losing phase input."""
    issue_dir, iteration_dir, task = _legacy_interrupted_task_repo(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"retry_fresh_session"}',
            "--no-resume",
            "--json",
        ],
    )

    assert result.exit_code == 0, (result.stdout, result.exception)
    records = HumanTaskRecordStore(issue_dir)
    completed = records.get_task(task.id)
    receipt = records.get_result(task.id)
    assert completed.status is HumanTaskStatus.COMPLETED
    assert receipt is not None
    assert receipt.payload["decision"] == "retry_fresh_session"
    assert receipt.payload["session_continuation"] == {
        "schema_version": 1,
        "policy": "new",
        "reason": "user_selected_fresh_session",
        "next_action": "resume_same_step_same_iteration",
        "workflow_id": completed.workflow_id,
        "human_task_id": task.id,
        "step": "spec",
        "iteration": 1,
        "previous": {
            "cli": "codex",
            "model": "gpt-5-test",
            "session_id": "old-session",
        },
        "last_error_type": "rate_limit",
    }
    assert (iteration_dir / "user_input.md").read_text(encoding="utf-8") == (
        "Keep the previously confirmed requirement."
    )
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "spec"


def test_json_failure_is_one_document_and_nonzero(tmp_path: Path, monkeypatch) -> None:
    """Test List U7/I6: integrations receive one actionable error envelope."""
    _task_repo(tmp_path, monkeypatch)

    result = runner.invoke(app, ["task", "inspect", "missing", "--json"])

    assert result.exit_code != 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["operation"] == "inspect"
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "task_not_found"
    assert envelope["error"]["recovery"]


def test_read_only_commands_leave_repository_state_unchanged(tmp_path: Path, monkeypatch) -> None:
    """Test List I1/I2: listing and inspection never alter workflow ownership state."""
    issue_dir, task = _task_repo(tmp_path, monkeypatch)
    marker = tmp_path / ".cafe" / "active_issue"
    marker.write_bytes(b"another-issue\n")
    before = {
        path: path.read_bytes()
        for path in (marker, issue_dir / "human_tasks.json", issue_dir / "blackboard.json")
    }

    assert runner.invoke(app, ["task", "ls"]).exit_code == 0
    assert runner.invoke(app, ["task", "inspect", task.id]).exit_code == 0

    assert {path: path.read_bytes() for path in before} == before
