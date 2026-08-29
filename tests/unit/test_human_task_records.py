"""Invariant coverage for durable human-task workflow records."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from cafe.core.human_task_records import (
    HumanTaskCorrelationError,
    HumanTaskMaterialization,
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


def _materialize_in_process(issue_dir: str, barrier, results) -> None:
    """Exercise the store through a separate process sharing one handoff."""
    try:
        barrier.wait(timeout=5)
        results.put(("ok", _materialize(HumanTaskRecordStore(Path(issue_dir))).id))
    except BaseException as exc:
        results.put(("error", repr(exc)))


def _materialize_with_status_in_process(issue_dir: str, barrier, results) -> None:
    """Exercise the atomic creation signal across separate processes."""
    try:
        barrier.wait(timeout=5)
        result = HumanTaskRecordStore(Path(issue_dir)).materialize_with_status(
            workflow_id="workflow-one",
            step="develop",
            iteration=1,
            trigger="need_clarification",
            policy_id="clarification-feedback",
            prompt="Describe the compatibility requirement.",
            expected_result={"input_schema": "feedback", "required": True},
            continuations={"submit": "develop"},
            assignee_type="user",
        )
        results.put(("ok", result.task.id, result.created))
    except BaseException as exc:
        results.put(("error", repr(exc), False))


def _complete_in_process(issue_dir: str, task_id: str, barrier, results) -> None:
    """Race two external completion attempts against the same durable task."""
    try:
        barrier.wait(timeout=5)
        result = HumanTaskRecordStore(Path(issue_dir)).complete(
            workflow_id="workflow-one",
            task_id=task_id,
            payload={"feedback": "concurrent completion"},
            source="command",
        )
        results.put(("ok", result.id))
    except BaseException as exc:
        results.put(("error", repr(exc)))


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


def test_materialization_reports_creation_atomically_across_restart(tmp_path: Path) -> None:
    """The durable store distinguishes a new task from recovery without a second read."""
    issue_dir = tmp_path / "issue"

    first = HumanTaskRecordStore(issue_dir).materialize_with_status(
        workflow_id="workflow-one",
        step="develop",
        iteration=1,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Describe the compatibility requirement.",
        expected_result={"input_schema": "feedback", "required": True},
        continuations={"submit": "develop"},
        assignee_type="user",
    )
    recovered = HumanTaskRecordStore(issue_dir).materialize_with_status(
        workflow_id="workflow-one",
        step="develop",
        iteration=1,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Describe the compatibility requirement.",
        expected_result={"input_schema": "feedback", "required": True},
        continuations={"submit": "develop"},
        assignee_type="user",
    )

    assert isinstance(first, HumanTaskMaterialization)
    assert first.created is True
    assert recovered.created is False
    assert recovered.task == first.task


def test_replacement_materialization_cancels_only_explicit_obsolete_pending_tasks(
    tmp_path: Path,
) -> None:
    """Test List 4: replacement atomically deactivates only its named predecessor."""
    store = HumanTaskRecordStore(tmp_path / "issue")
    original = store.materialize(
        workflow_id="workflow-one",
        step="develop",
        iteration=1,
        trigger="need_permission",
        policy_id="permission-answers",
        prompt="Grant permission",
        expected_result={"input_schema": "feedback"},
        continuations={"submit": "develop"},
        assignee_type="user",
    )
    unrelated = store.materialize(
        workflow_id="workflow-one",
        step="review",
        iteration=1,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Clarify review",
        expected_result={"input_schema": "feedback"},
        continuations={"submit": "review"},
        assignee_type="user",
    )

    replacement = store.materialize_with_status(
        workflow_id="workflow-one",
        step="develop",
        iteration=2,
        trigger="need_permission",
        policy_id="permission-answers",
        prompt="Grant renewed permission",
        expected_result={"input_schema": "feedback"},
        continuations={"submit": "develop"},
        assignee_type="user",
        superseded_task_ids=(original.id,),
    ).task

    assert store.get_task(original.id).status is HumanTaskStatus.CANCELLED
    assert store.get_wait_state(original.id).released_at is not None
    assert store.get_task(unrelated.id).status is HumanTaskStatus.PENDING
    assert store.get_wait_state(unrelated.id).released_at is None
    superseded = [event for event in store.lifecycle_events() if event.event_type == "superseded"]
    assert superseded[0].task_id == original.id
    assert superseded[0].context["replacement_task_id"] == replacement.id


@pytest.mark.skipif(os.name == "nt", reason="the durable record lock is POSIX file based")
def test_only_one_concurrent_materialization_reports_creation(tmp_path: Path) -> None:
    """Only the process that durably creates the shared task reports creation."""
    context = multiprocessing.get_context("fork")
    issue_dir = tmp_path / "issue"
    worker_count = 8
    barrier = context.Barrier(worker_count)
    results = context.Queue()
    workers = [
        context.Process(
            target=_materialize_with_status_in_process,
            args=(str(issue_dir), barrier, results),
        )
        for _ in range(worker_count)
    ]

    for process in workers:
        process.start()
    materializations = [results.get(timeout=10) for _ in workers]
    for process in workers:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in workers)
    assert all(status == "ok" for status, _task_id, _created in materializations)
    assert len({task_id for _status, task_id, _created in materializations}) == 1
    assert sum(created for _status, _task_id, created in materializations) == 1


@pytest.mark.skipif(os.name == "nt", reason="the durable record lock is POSIX file based")
def test_record_transitions_are_idempotent_across_concurrent_processes(tmp_path: Path) -> None:
    """UT-002/UT-004: concurrent workers create and complete exactly one record."""
    context = multiprocessing.get_context("fork")
    issue_dir = tmp_path / "issue"
    worker_count = 8

    create_barrier = context.Barrier(worker_count)
    create_results = context.Queue()
    creators = [
        context.Process(
            target=_materialize_in_process,
            args=(str(issue_dir), create_barrier, create_results),
        )
        for _ in range(worker_count)
    ]
    for process in creators:
        process.start()
    created = [create_results.get(timeout=10) for _ in creators]
    for process in creators:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in creators)
    assert all(status == "ok" for status, _value in created)
    assert len({value for _status, value in created}) == 1
    task = HumanTaskRecordStore(issue_dir).tasks()[0]

    complete_barrier = context.Barrier(worker_count)
    complete_results = context.Queue()
    completers = [
        context.Process(
            target=_complete_in_process,
            args=(str(issue_dir), task.id, complete_barrier, complete_results),
        )
        for _ in range(worker_count)
    ]
    for process in completers:
        process.start()
    completed = [complete_results.get(timeout=10) for _ in completers]
    for process in completers:
        process.join(timeout=10)

    records = HumanTaskRecordStore(issue_dir)
    assert all(process.exitcode == 0 for process in completers)
    assert all(status == "ok" for status, _value in completed)
    assert len({value for _status, value in completed}) == 1
    assert len(records.tasks()) == 1
    assert len(records.results()) == 1


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
