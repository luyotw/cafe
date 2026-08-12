"""Invariant coverage for durable human-task workflow records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cafe.core.human_task_records import (
    HumanTaskCorrelationError,
    HumanTaskRecordSchemaError,
    HumanTaskRecordStore,
    HumanTaskStatus,
)


def _materialize(store: HumanTaskRecordStore, *, iteration: int = 1):
    return store.materialize(
        workflow_id="workflow-one",
        step="develop",
        iteration=iteration,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Describe the compatibility requirement.",
        expected_result={"input_schema": "feedback", "required": True},
        continuations={"submit": "develop"},
        assignee_type="user",
    )


def test_versioned_records_round_trip_with_assignment_wait_and_result(tmp_path: Path) -> None:
    """UT-001: one task keeps its durable identity and completion context."""
    store = HumanTaskRecordStore(tmp_path / "issue")
    task = _materialize(store)

    result = store.complete(
        workflow_id="workflow-one",
        task_id=task.id,
        payload={"feedback": "Keep old handoffs working."},
        source="command",
    )

    reloaded = HumanTaskRecordStore(tmp_path / "issue")
    loaded_task = reloaded.get_task(task.id)
    wait_state = reloaded.get_wait_state(task.id)
    loaded_result = reloaded.get_result(task.id)

    assert loaded_task.id == task.id
    assert loaded_task.workflow_id == "workflow-one"
    assert loaded_task.step == "develop"
    assert loaded_task.iteration == 1
    assert loaded_task.policy_id == "clarification-feedback"
    assert loaded_task.status is HumanTaskStatus.COMPLETED
    assert reloaded.get_assignment(task.id).assignee_type == "user"
    assert wait_state.task_id == task.id
    assert wait_state.released_at is not None
    assert loaded_result == result
    assert loaded_result.payload["feedback"] == "Keep old handoffs working."


def test_materialization_is_idempotent_per_handoff_key(tmp_path: Path) -> None:
    """UT-002: retries reuse one active task, while a new iteration is distinct."""
    store = HumanTaskRecordStore(tmp_path / "issue")

    first = _materialize(store)
    retry = _materialize(store)
    next_iteration = _materialize(store, iteration=2)

    assert retry.id == first.id
    assert store.active_wait_state("workflow-one").task_id == first.id
    assert next_iteration.id != first.id
    assert len(store.tasks()) == 2


def test_completion_requires_the_matching_workflow_and_pending_task(tmp_path: Path) -> None:
    """UT-003: a cross-workflow result cannot release the active wait state."""
    store = HumanTaskRecordStore(tmp_path / "issue")
    task = _materialize(store)

    with pytest.raises(HumanTaskCorrelationError):
        store.complete(
            workflow_id="workflow-two",
            task_id=task.id,
            payload={"feedback": "unrelated"},
            source="command",
        )

    assert store.get_task(task.id).status is HumanTaskStatus.PENDING
    assert store.active_wait_state("workflow-one").task_id == task.id
    assert store.results() == ()


def test_terminal_lifecycle_transitions_preserve_the_first_result(tmp_path: Path) -> None:
    """UT-004: repeat, stale, and cancelled completion attempts create no progress."""
    store = HumanTaskRecordStore(tmp_path / "issue")
    completed = _materialize(store)
    first = store.complete(
        workflow_id="workflow-one",
        task_id=completed.id,
        payload={"feedback": "first"},
        source="interactive",
    )
    repeated = store.complete(
        workflow_id="workflow-one",
        task_id=completed.id,
        payload={"feedback": "second"},
        source="interactive",
    )
    cancelled = _materialize(store, iteration=2)
    store.cancel(
        workflow_id="workflow-one", task_id=cancelled.id, reason="workflow ended"
    )

    with pytest.raises(HumanTaskCorrelationError):
        store.complete(
            workflow_id="workflow-one",
            task_id=cancelled.id,
            payload={"feedback": "late"},
            source="command",
        )

    assert repeated == first
    assert len(store.results()) == 1
    assert store.get_task(cancelled.id).status is HumanTaskStatus.CANCELLED
    assert store.active_wait_state("workflow-one") is None


def test_lifecycle_and_configuration_errors_are_auditable_without_an_actionable_wait(
    tmp_path: Path,
) -> None:
    """UT-006: rejected and configuration-error evidence does not create progress."""
    store = HumanTaskRecordStore(tmp_path / "issue")
    task = _materialize(store)

    store.record_rejection(
        workflow_id="workflow-one", task_id=task.id, reason="response is invalid"
    )
    store.record_configuration_error(
        workflow_id="workflow-one",
        step="develop",
        iteration=2,
        trigger="need_clarification",
        reason="no matching policy",
    )

    event_types = [event.event_type for event in store.lifecycle_events()]
    assert "created" in event_types
    assert "rejected" in event_types
    assert "configuration_error" in event_types
    assert store.get_task(task.id).status is HumanTaskStatus.PENDING
    assert store.active_wait_state("workflow-one").task_id == task.id
    assert len(store.results()) == 0


def test_future_record_schema_fails_closed_without_rewriting_data(tmp_path: Path) -> None:
    """UT-005: unsupported task records are rejected rather than migrated blindly."""
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    path = issue_dir / "human_tasks.json"
    raw = {"schema_version": 999, "workflow_id": "future", "tasks": []}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(HumanTaskRecordSchemaError):
        HumanTaskRecordStore(issue_dir).tasks()

    assert json.loads(path.read_text(encoding="utf-8")) == raw
