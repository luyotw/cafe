"""Repository inbox journeys across durable task and workflow boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app
from cafe.ui.human_tasks import resolve_step_human_task

runner = CliRunner()


def _pending_issue(cafe_dir: Path, name: str):
    issue_dir = cafe_dir / "issues" / name
    iteration_dir = issue_dir / "spec" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: default\n", encoding="utf-8")
    blackboards = BlackboardStore(issue_dir)
    state = blackboards.load_or_create("spec", playbook_id="default")
    blackboards.set_current_step(state, "user")
    blackboards.update_handoff_contract(
        state,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="test",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=PlaybookLoader().load("default"),
        step_name="spec",
        trigger="confirm_output",
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step="spec",
        iteration=1,
        trigger="confirm_output",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    return issue_dir, task


def test_noninteractive_completion_resumes_only_bound_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I3: stable completion ignores active marker and resumes one owner."""
    monkeypatch.chdir(tmp_path)
    cafe_dir = tmp_path / ".cafe"
    selected_dir, selected = _pending_issue(cafe_dir, "selected")
    other_dir, other = _pending_issue(cafe_dir, "active")
    marker = cafe_dir / "active_issue"
    marker.write_bytes(b"active\n")
    other_before = (other_dir / "blackboard.json").read_bytes()
    resumed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, playbook: resumed.append((issue, playbook)),
    )

    result = runner.invoke(
        app,
        ["task", "complete", selected.id, "--result", '{"decision":"confirm"}'],
    )

    assert result.exit_code == 0
    records = HumanTaskRecordStore(selected_dir)
    assert records.get_task(selected.id).status is HumanTaskStatus.COMPLETED
    assert len(records.results()) == 1
    assert BlackboardStore(selected_dir).load_or_create("spec").current_step == "plan"
    assert resumed == [("selected", "default")]
    assert marker.read_bytes() == b"active\n"
    assert (other_dir / "blackboard.json").read_bytes() == other_before
    assert HumanTaskRecordStore(other_dir).get_task(other.id).status is HumanTaskStatus.PENDING


def test_interactive_completion_uses_same_durable_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I4: prompt collection feeds the same validator and transition."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "interactive")
    monkeypatch.setattr(
        "cafe.ui.commands.tasks.collect_human_task_payload",
        lambda _policy: {"task": "output-review", "decision": "confirm"},
    )
    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)

    result = runner.invoke(app, ["task", "complete", task.id])

    assert result.exit_code == 0
    assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "plan"


@pytest.mark.parametrize("case", ["invalid", "stale"])
def test_unsafe_completion_stops_without_workflow_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Test List I5: invalid and terminal attempts do not resume a workflow."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", case)
    if case == "stale":
        HumanTaskRecordStore(issue_dir).cancel(
            workflow_id=task.workflow_id, task_id=task.id, reason="test"
        )
    resumed: list[str] = []
    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow",
        lambda issue, _playbook: resumed.append(issue),
    )
    payload = '{"decision":"unknown"}' if case == "invalid" else '{"decision":"confirm"}'

    result = runner.invoke(
        app, ["task", "complete", task.id, "--result", payload, "--json"]
    )

    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] in {
        "invalid_response",
        "task_not_pending",
    }
    assert resumed == []
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "user"
    assert HumanTaskRecordStore(issue_dir).results() == ()


def test_completion_json_stdout_is_one_result_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I6: continuation progress cannot contaminate machine output."""
    monkeypatch.chdir(tmp_path)
    _issue_dir, task = _pending_issue(tmp_path / ".cafe", "json")

    def noisy_resume(_issue: str, _playbook: str) -> None:
        print("workflow progress")

    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", noisy_resume)

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--json",
        ],
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["operation"] == "complete"
    assert envelope["data"]["task"]["id"] == task.id
    assert envelope["data"]["workflow"]["issue"] == "json"


def test_resume_failure_reports_committed_completion_and_direct_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List I3/I6: post-commit resume failure is not presented as retryable."""
    monkeypatch.chdir(tmp_path)
    issue_dir, task = _pending_issue(tmp_path / ".cafe", "resume-failure")

    def unavailable_resume(_issue: str, _playbook: str) -> None:
        raise RuntimeError("runner unavailable")

    monkeypatch.setattr(
        "cafe.ui.commands.tasks._resume_issue_workflow", unavailable_resume
    )

    result = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"confirm"}',
            "--json",
        ],
    )

    assert result.exit_code != 0
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "workflow_resume_failed"
    assert envelope["error"]["task_id"] == task.id
    assert envelope["error"]["issue"] == "resume-failure"
    assert envelope["error"]["workflow_id"] == task.workflow_id
    assert "cafe workflow" in envelope["error"]["recovery"]
    records = HumanTaskRecordStore(issue_dir)
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
    assert len(records.results()) == 1
    assert BlackboardStore(issue_dir).load_or_create("spec").current_step == "plan"
