"""CLI contract tests for repository task list and inspection."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.ui.cli import app

runner = CliRunner()


def _task_repo(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-a"
    issue_dir.mkdir(parents=True)
    state = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    (issue_dir / "issue.yaml").write_text("playbook: default\n", encoding="utf-8")
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
