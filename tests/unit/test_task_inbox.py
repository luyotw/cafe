"""Invariant tests for the repository-wide durable task inbox."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.task_inbox import TaskInboxError, TaskInboxService


def _issue(cafe_dir: Path, name: str, workflow_id: str) -> Path:
    issue_dir = cafe_dir / "issues" / name
    issue_dir.mkdir(parents=True)
    blackboard = BlackboardStore(issue_dir).load_or_create("spec", playbook_id="default")
    blackboard.workflow_id = workflow_id
    BlackboardStore(issue_dir).save(blackboard)
    (issue_dir / "issue.yaml").write_text("playbook: default\n", encoding="utf-8")
    return issue_dir


def _task(
    issue_dir: Path,
    workflow_id: str,
    *,
    step: str = "spec",
    iteration: int = 1,
    assignee: str | None = None,
):
    return HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=workflow_id,
        step=step,
        iteration=iteration,
        trigger="confirm_output",
        policy_id="output-review",
        prompt="Review the output",
        expected_result={"input_schema": "decision", "decisions": [{"id": "confirm"}]},
        continuations={"confirm": "plan"},
        assignee_type="user",
        assignee_id=assignee,
    )


def test_repository_discovery_is_deterministic_and_hides_terminal_tasks(tmp_path: Path) -> None:
    """Test List U1: default discovery is complete, stable, and pending-only."""
    cafe_dir = tmp_path / ".cafe"
    issue_b = _issue(cafe_dir, "issue-b", "workflow-b")
    issue_a = _issue(cafe_dir, "issue-a", "workflow-a")
    pending = _task(issue_b, "workflow-b")
    completed = _task(issue_a, "workflow-a")
    HumanTaskRecordStore(issue_a).complete(
        workflow_id="workflow-a",
        task_id=completed.id,
        payload={"decision": "confirm"},
        source="test",
    )

    service = TaskInboxService(cafe_dir)
    first = service.list_tasks()
    second = service.list_tasks()

    assert [item.id for item in first] == [pending.id]
    assert first == second
    assert [item.status for item in service.list_tasks(include_historical=True)] == [
        "completed",
        "pending",
    ]


def test_filters_are_conjunctive_and_due_state_is_read_only(tmp_path: Path) -> None:
    """Test List U2: all filters compose and canonical no-due tasks stay unscheduled."""
    cafe_dir = tmp_path / ".cafe"
    issue = _issue(cafe_dir, "issue-a", "workflow-a")
    selected = _task(issue, "workflow-a", step="review", assignee="alice")
    _task(issue, "workflow-a", step="spec", iteration=2, assignee="bob")

    matches = TaskInboxService(cafe_dir).list_tasks(
        statuses={"pending"},
        assignee="alice",
        workflow="workflow-a",
        step="review",
        due_state="unscheduled",
    )

    assert [item.id for item in matches] == [selected.id]
    assert matches[0].due_state == "unscheduled"


def test_stable_identifier_lookup_distinguishes_missing_and_duplicate(tmp_path: Path) -> None:
    """Test List U3: selection never guesses when identity is absent or ambiguous."""
    cafe_dir = tmp_path / ".cafe"
    issue_a = _issue(cafe_dir, "issue-a", "workflow-a")
    issue_b = _issue(cafe_dir, "issue-b", "workflow-b")
    task = _task(issue_a, "workflow-a")

    with pytest.raises(TaskInboxError) as missing:
        TaskInboxService(cafe_dir).inspect("missing")
    assert missing.value.code == "task_not_found"

    raw = json.loads((issue_a / "human_tasks.json").read_text(encoding="utf-8"))
    raw["workflow_id"] = "workflow-b"
    for collection in ("tasks", "wait_states", "results", "lifecycle_events"):
        for record in raw[collection]:
            record["workflow_id"] = "workflow-b"
    (issue_b / "human_tasks.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TaskInboxError) as duplicate:
        TaskInboxService(cafe_dir).inspect(task.id)
    assert duplicate.value.code == "duplicate_task_id"


def test_inspection_projects_full_public_contract(tmp_path: Path) -> None:
    """Test List U4: detail exposes declared context without mutable store internals."""
    cafe_dir = tmp_path / ".cafe"
    issue = _issue(cafe_dir, "issue-a", "workflow-a")
    task = _task(issue, "workflow-a", assignee="alice")

    detail = TaskInboxService(cafe_dir).inspect(task.id)

    assert detail.issue == "issue-a"
    assert detail.prompt == "Review the output"
    assert detail.expected_result["input_schema"] == "decision"
    assert detail.continuations == {"confirm": "plan"}
    assert detail.assignment == {"type": "user", "id": "alice"}
    assert detail.wait["active"] is True
    assert detail.timestamps["created_at"]


@pytest.mark.parametrize("contents", ["{broken", '{"schema_version": 999}'])
def test_repository_scan_fails_closed_on_corrupt_store(
    tmp_path: Path, contents: str
) -> None:
    """Test List U5: repository corruption cannot produce partial success."""
    cafe_dir = tmp_path / ".cafe"
    valid = _issue(cafe_dir, "valid", "workflow-valid")
    corrupt = _issue(cafe_dir, "corrupt", "workflow-corrupt")
    _task(valid, "workflow-valid")
    (corrupt / "human_tasks.json").write_text(contents, encoding="utf-8")

    with pytest.raises(TaskInboxError) as failure:
        TaskInboxService(cafe_dir).list_tasks()

    assert failure.value.code == "corrupt_store"
    assert failure.value.issue == "corrupt"
    assert failure.value.recovery


def test_completion_preflight_rejects_stale_or_mismatched_ownership(tmp_path: Path) -> None:
    """Test List U6: only one live matching wait may enter the mutation path."""
    cafe_dir = tmp_path / ".cafe"
    issue = _issue(cafe_dir, "issue-a", "workflow-a")
    task = _task(issue, "workflow-a")
    service = TaskInboxService(cafe_dir)

    assert service.preflight_completion(task.id).workflow_id == "workflow-a"

    state = BlackboardStore(issue).load_or_create("spec")
    state.workflow_id = "workflow-other"
    BlackboardStore(issue).save(state)
    with pytest.raises(TaskInboxError) as mismatch:
        service.preflight_completion(task.id)
    assert mismatch.value.code == "workflow_mismatch"
    assert HumanTaskRecordStore(issue).results() == ()


def test_archived_task_requires_explicit_restore(tmp_path: Path) -> None:
    """Test List U6/I5: archived ownership is distinct from a missing identifier."""
    cafe_dir = tmp_path / ".cafe"
    archive_root = tmp_path / "archives"
    issue = _issue(cafe_dir, "archived", "workflow-a")
    task = _task(issue, "workflow-a")
    archive_root.mkdir()
    issue.rename(archive_root / "archived")

    with pytest.raises(TaskInboxError) as archived:
        TaskInboxService(cafe_dir, archive_root=archive_root).preflight_completion(task.id)

    assert archived.value.code == "archived_workflow"
    assert "restore" in archived.value.recovery.lower()
