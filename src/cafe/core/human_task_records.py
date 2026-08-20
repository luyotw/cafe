"""Durable, file-backed records for policy-defined human workflow tasks."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows falls back to the local lock.
    fcntl = None  # type: ignore[assignment]

from cafe.core.packet_io import atomic_write_bytes, canonical_json

HUMAN_TASK_RECORD_FILENAME = "human_tasks.json"
HUMAN_TASK_RECORD_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class HumanTaskRecordError(ValueError):
    """Base error for a durable human-task record operation."""


class HumanTaskRecordSchemaError(HumanTaskRecordError):
    """The on-disk record cannot safely be interpreted by this runtime."""


class HumanTaskCorrelationError(HumanTaskRecordError):
    """A result does not belong to the pending task it tries to affect."""


class HumanTaskStatus(str, Enum):
    """Lifecycle state of the actionable human task."""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True)
class HumanTask:
    """A stable, workflow-owned unit of human work."""

    id: str
    handoff_key: str
    workflow_id: str
    step: str
    iteration: int
    trigger: str
    policy_id: str
    prompt: str
    expected_result: dict[str, Any]
    continuations: dict[str, str]
    status: HumanTaskStatus
    created_at: str
    capability_approval: Optional[dict[str, Any]] = None
    completed_at: Optional[str] = None
    cancelled_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handoff_key": self.handoff_key,
            "workflow_id": self.workflow_id,
            "step": self.step,
            "iteration": self.iteration,
            "trigger": self.trigger,
            "policy_id": self.policy_id,
            "prompt": self.prompt,
            "expected_result": dict(self.expected_result),
            "continuations": dict(self.continuations),
            "status": self.status.value,
            "created_at": self.created_at,
            "capability_approval": (
                dict(self.capability_approval) if self.capability_approval is not None else None
            ),
            "completed_at": self.completed_at,
            "cancelled_at": self.cancelled_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HumanTask":
        try:
            return cls(
                id=_required_text(data, "id"),
                handoff_key=_required_text(data, "handoff_key"),
                workflow_id=_required_text(data, "workflow_id"),
                step=_required_text(data, "step"),
                iteration=_positive_int(data, "iteration"),
                trigger=_required_text(data, "trigger"),
                policy_id=_required_text(data, "policy_id"),
                prompt=_required_text(data, "prompt"),
                expected_result=_mapping(data, "expected_result"),
                continuations=_string_mapping(data, "continuations"),
                status=HumanTaskStatus(_required_text(data, "status")),
                created_at=_required_text(data, "created_at"),
                capability_approval=(
                    _mapping(data, "capability_approval")
                    if data.get("capability_approval") is not None
                    else None
                ),
                completed_at=_optional_text(data.get("completed_at")),
                cancelled_at=_optional_text(data.get("cancelled_at")),
            )
        except (TypeError, ValueError) as exc:
            raise HumanTaskRecordSchemaError(f"invalid human task: {exc}") from exc


@dataclass(frozen=True)
class Assignment:
    """The declared human assignee for one task."""

    task_id: str
    assignee_type: str
    assignee_id: Optional[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "assignee_type": self.assignee_type,
            "assignee_id": self.assignee_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Assignment":
        try:
            return cls(
                task_id=_required_text(data, "task_id"),
                assignee_type=_required_text(data, "assignee_type"),
                assignee_id=_optional_text(data.get("assignee_id")),
                created_at=_required_text(data, "created_at"),
            )
        except (TypeError, ValueError) as exc:
            raise HumanTaskRecordSchemaError(f"invalid assignment: {exc}") from exc


@dataclass(frozen=True)
class WaitState:
    """The correlation fence which authorizes a pending task to resume work."""

    task_id: str
    workflow_id: str
    pause_reason: str
    created_at: str
    released_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "pause_reason": self.pause_reason,
            "created_at": self.created_at,
            "released_at": self.released_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WaitState":
        try:
            return cls(
                task_id=_required_text(data, "task_id"),
                workflow_id=_required_text(data, "workflow_id"),
                pause_reason=_required_text(data, "pause_reason"),
                created_at=_required_text(data, "created_at"),
                released_at=_optional_text(data.get("released_at")),
            )
        except (TypeError, ValueError) as exc:
            raise HumanTaskRecordSchemaError(f"invalid wait state: {exc}") from exc


@dataclass(frozen=True)
class TaskResult:
    """One already-validated completion attached to its matching task."""

    id: str
    task_id: str
    workflow_id: str
    payload: dict[str, Any]
    source: str
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "payload": dict(self.payload),
            "source": self.source,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskResult":
        try:
            return cls(
                id=_required_text(data, "id"),
                task_id=_required_text(data, "task_id"),
                workflow_id=_required_text(data, "workflow_id"),
                payload=_mapping(data, "payload"),
                source=_required_text(data, "source"),
                completed_at=_required_text(data, "completed_at"),
            )
        except (TypeError, ValueError) as exc:
            raise HumanTaskRecordSchemaError(f"invalid task result: {exc}") from exc


@dataclass(frozen=True)
class LifecycleEvent:
    """Append-only evidence for task lifecycle decisions and rejections."""

    event_type: str
    workflow_id: str
    task_id: Optional[str]
    occurred_at: str
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "occurred_at": self.occurred_at,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LifecycleEvent":
        try:
            return cls(
                event_type=_required_text(data, "event_type"),
                workflow_id=_required_text(data, "workflow_id"),
                task_id=_optional_text(data.get("task_id")),
                occurred_at=_required_text(data, "occurred_at"),
                context=_mapping(data, "context"),
            )
        except (TypeError, ValueError) as exc:
            raise HumanTaskRecordSchemaError(f"invalid human-task lifecycle event: {exc}") from exc


@dataclass
class _Envelope:
    workflow_id: str
    tasks: dict[str, HumanTask]
    assignments: dict[str, Assignment]
    wait_states: dict[str, WaitState]
    results: dict[str, TaskResult]
    lifecycle_events: list[LifecycleEvent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HUMAN_TASK_RECORD_SCHEMA_VERSION,
            "workflow_id": self.workflow_id,
            "tasks": [task.to_dict() for task in self.tasks.values()],
            "assignments": [assignment.to_dict() for assignment in self.assignments.values()],
            "wait_states": [wait_state.to_dict() for wait_state in self.wait_states.values()],
            "results": [result.to_dict() for result in self.results.values()],
            "lifecycle_events": [event.to_dict() for event in self.lifecycle_events],
        }

    @classmethod
    def empty(cls) -> "_Envelope":
        return cls("", {}, {}, {}, {}, [])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "_Envelope":
        version = data.get("schema_version")
        if version != HUMAN_TASK_RECORD_SCHEMA_VERSION:
            raise HumanTaskRecordSchemaError(
                f"unsupported human-task schema version {version!r}; "
                f"expected {HUMAN_TASK_RECORD_SCHEMA_VERSION}"
            )
        workflow_id = _required_text(data, "workflow_id")
        tasks = _records_by_task_id(data, "tasks", HumanTask.from_dict)
        assignments = _records_by_task_id(data, "assignments", Assignment.from_dict)
        waits = _records_by_task_id(data, "wait_states", WaitState.from_dict)
        results = _records_by_task_id(data, "results", TaskResult.from_dict)
        raw_events = data.get("lifecycle_events", [])
        if not isinstance(raw_events, list):
            raise HumanTaskRecordSchemaError("lifecycle_events must be a list")
        events = [
            LifecycleEvent.from_dict(_as_mapping(item, "lifecycle event"))
            for item in raw_events
        ]
        envelope = cls(workflow_id, tasks, assignments, waits, results, events)
        envelope.validate()
        return envelope

    def validate(self) -> None:
        for task_id, task in self.tasks.items():
            if task_id != task.id or task.workflow_id != self.workflow_id:
                raise HumanTaskRecordSchemaError(
                    "task identity does not match its workflow envelope"
                )
            assignment = self.assignments.get(task_id)
            wait_state = self.wait_states.get(task_id)
            if assignment is None or assignment.task_id != task_id:
                raise HumanTaskRecordSchemaError("every task requires a matching assignment")
            if (
                wait_state is None
                or wait_state.task_id != task_id
                or wait_state.workflow_id != self.workflow_id
            ):
                raise HumanTaskRecordSchemaError("every task requires a matching wait state")
            result = self.results.get(task_id)
            if task.status is HumanTaskStatus.COMPLETED and result is None:
                raise HumanTaskRecordSchemaError("completed task has no result")
            if result is not None and (
                result.task_id != task_id or result.workflow_id != self.workflow_id
            ):
                raise HumanTaskRecordSchemaError("result does not match its task/workflow")


class HumanTaskRecordStore:
    """Versioned local store for durable task, wait, result, and audit records."""

    _thread_locks: dict[Path, threading.RLock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.file_path = issue_dir / HUMAN_TASK_RECORD_FILENAME
        self.lock_path = issue_dir / f".{HUMAN_TASK_RECORD_FILENAME}.lock"
        self._transaction_depth = 0

    @property
    def exists(self) -> bool:
        return self.file_path.exists()

    def tasks(self) -> tuple[HumanTask, ...]:
        return tuple(self._load().tasks.values())

    def results(self) -> tuple[TaskResult, ...]:
        return tuple(self._load().results.values())

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(self._load().lifecycle_events)

    def get_task(self, task_id: str) -> HumanTask:
        return self._task(self._load(), task_id)

    def get_assignment(self, task_id: str) -> Assignment:
        envelope = self._load()
        self._task(envelope, task_id)
        return envelope.assignments[task_id]

    def get_wait_state(self, task_id: str) -> WaitState:
        envelope = self._load()
        self._task(envelope, task_id)
        return envelope.wait_states[task_id]

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        envelope = self._load()
        self._task(envelope, task_id)
        return envelope.results.get(task_id)

    def active_wait_state(
        self,
        workflow_id: str,
        *,
        step: Optional[str] = None,
        trigger: Optional[str] = None,
        policy_id: Optional[str] = None,
    ) -> Optional[WaitState]:
        envelope = self._load_for_workflow(workflow_id, create=False)
        candidates = []
        for task_id, wait_state in envelope.wait_states.items():
            task = envelope.tasks[task_id]
            if wait_state.released_at is not None or task.status is not HumanTaskStatus.PENDING:
                continue
            if step is not None and task.step != step:
                continue
            if trigger is not None and task.trigger != trigger:
                continue
            if policy_id is not None and task.policy_id != policy_id:
                continue
            candidates.append(wait_state)
        return candidates[0] if candidates else None

    def materialize(
        self,
        *,
        workflow_id: str,
        step: str,
        iteration: int,
        trigger: str,
        policy_id: str,
        prompt: str,
        expected_result: Mapping[str, Any],
        continuations: Mapping[str, str],
        assignee_type: str,
        assignee_id: Optional[str] = None,
        capability_approval: Optional[Mapping[str, Any]] = None,
        handoff_key: Optional[str] = None,
    ) -> HumanTask:
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=True)
            resolved_handoff_key = handoff_key or _handoff_key(
                workflow_id, step, iteration, trigger, policy_id
            )
            existing = next(
                (
                    task
                    for task in envelope.tasks.values()
                    if task.handoff_key == resolved_handoff_key
                    and task.status is HumanTaskStatus.PENDING
                ),
                None,
            )
            if existing is not None:
                return existing
            now = _now_iso()
            task_id = str(uuid4())
            capability_metadata = (
                {**dict(capability_approval), "task_id": task_id}
                if capability_approval is not None
                else None
            )
            task = HumanTask(
                id=task_id,
                handoff_key=resolved_handoff_key,
                workflow_id=workflow_id,
                step=_text(step, "step"),
                iteration=_positive(iteration, "iteration"),
                trigger=_text(trigger, "trigger"),
                policy_id=_text(policy_id, "policy_id"),
                prompt=_text(prompt, "prompt"),
                expected_result=dict(expected_result),
                continuations=_string_mapping_value(continuations, "continuations"),
                status=HumanTaskStatus.PENDING,
                created_at=now,
                capability_approval=capability_metadata,
            )
            envelope.tasks[task.id] = task
            envelope.assignments[task.id] = Assignment(
                task_id=task.id,
                assignee_type=_text(assignee_type, "assignee_type"),
                assignee_id=_optional_text(assignee_id),
                created_at=now,
            )
            envelope.wait_states[task.id] = WaitState(
                task_id=task.id,
                workflow_id=workflow_id,
                pause_reason=task.trigger,
                created_at=now,
            )
            self._append_event(
                envelope,
                "created",
                task_id=task.id,
                context={"handoff_key": resolved_handoff_key},
            )
            self._save(envelope)
            return task

    def update_capability_approval(
        self,
        *,
        workflow_id: str,
        task_id: str,
        metadata: Mapping[str, Any],
        event_type: str,
    ) -> HumanTask:
        """Atomically replace capability-specific state and append audit evidence."""
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=False)
            task = self._task(envelope, task_id)
            if task.capability_approval is None:
                raise HumanTaskCorrelationError(f"task {task.id} is not a capability approval")
            updated = replace(task, capability_approval=dict(metadata))
            envelope.tasks[task.id] = updated
            self._append_event(
                envelope,
                _text(event_type, "event_type"),
                task_id=task.id,
                context={
                    "request_fingerprint": metadata.get("fingerprint"),
                    "state": metadata.get("state"),
                },
            )
            self._save(envelope)
            return updated

    def transition_capability_approval(
        self,
        *,
        workflow_id: str,
        task_id: str,
        metadata: Mapping[str, Any],
        event_type: str,
        terminal_status: Optional[HumanTaskStatus] = None,
        result_payload: Optional[Mapping[str, Any]] = None,
        result_source: str = "capability_approval",
    ) -> HumanTask:
        """Persist capability state and its wait/result boundary in one transaction."""
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=False)
            task = self._task(envelope, task_id)
            if task.capability_approval is None:
                raise HumanTaskCorrelationError(f"task {task.id} is not a capability approval")
            now = _now_iso()
            updates: dict[str, Any] = {"capability_approval": dict(metadata)}
            if terminal_status is HumanTaskStatus.COMPLETED:
                updates["status"] = HumanTaskStatus.COMPLETED
                updates["completed_at"] = task.completed_at or now
            elif terminal_status is HumanTaskStatus.CANCELLED:
                updates["status"] = HumanTaskStatus.CANCELLED
                updates["cancelled_at"] = task.cancelled_at or now
            updated = replace(task, **updates)
            envelope.tasks[task.id] = updated
            if terminal_status is not None:
                wait = envelope.wait_states[task.id]
                if wait.released_at is None:
                    envelope.wait_states[task.id] = replace(wait, released_at=now)
            if result_payload is not None and task.id not in envelope.results:
                envelope.results[task.id] = TaskResult(
                    id=str(uuid4()),
                    task_id=task.id,
                    workflow_id=workflow_id,
                    payload=dict(result_payload),
                    source=_text(result_source, "result_source"),
                    completed_at=now,
                )
            self._append_event(
                envelope,
                _text(event_type, "event_type"),
                task_id=task.id,
                context={
                    "request_fingerprint": metadata.get("fingerprint"),
                    "state": metadata.get("state"),
                },
            )
            self._save(envelope)
            return updated

    def complete(
        self,
        *,
        workflow_id: str,
        task_id: str,
        payload: Mapping[str, Any],
        source: str,
    ) -> TaskResult:
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=False)
            task = self._task(envelope, task_id)
            existing = envelope.results.get(task.id)
            if existing is not None:
                return existing
            if task.status is not HumanTaskStatus.PENDING:
                raise HumanTaskCorrelationError(f"task {task.id} is not pending")
            wait_state = envelope.wait_states[task.id]
            if wait_state.released_at is not None:
                raise HumanTaskCorrelationError(f"task {task.id} has no active wait state")
            now = _now_iso()
            result = TaskResult(
                id=str(uuid4()),
                task_id=task.id,
                workflow_id=workflow_id,
                payload=dict(payload),
                source=_text(source, "source"),
                completed_at=now,
            )
            envelope.results[task.id] = result
            envelope.tasks[task.id] = replace(
                task, status=HumanTaskStatus.COMPLETED, completed_at=now
            )
            envelope.wait_states[task.id] = replace(wait_state, released_at=now)
            self._append_event(
                envelope, "completed", task_id=task.id, context={"result_id": result.id}
            )
            self._save(envelope)
            return result

    def record_rejection(self, *, workflow_id: str, task_id: str, reason: str) -> None:
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=False)
            self._task(envelope, task_id)
            self._append_event(
                envelope, "rejected", task_id=task_id, context={"reason": _text(reason, "reason")}
            )
            self._save(envelope)

    def cancel(self, *, workflow_id: str, task_id: str, reason: str) -> HumanTask:
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=False)
            task = self._task(envelope, task_id)
            if task.status is not HumanTaskStatus.PENDING:
                return task
            now = _now_iso()
            cancelled = replace(task, status=HumanTaskStatus.CANCELLED, cancelled_at=now)
            envelope.tasks[task.id] = cancelled
            envelope.wait_states[task.id] = replace(envelope.wait_states[task.id], released_at=now)
            self._append_event(
                envelope, "cancelled", task_id=task.id, context={"reason": _text(reason, "reason")}
            )
            self._save(envelope)
            return cancelled

    def record_configuration_error(
        self,
        *,
        workflow_id: str,
        step: str,
        iteration: int,
        trigger: str,
        reason: str,
    ) -> None:
        with self.transaction():
            envelope = self._load_for_workflow(workflow_id, create=True)
            self._append_event(
                envelope,
                "configuration_error",
                task_id=None,
                context={
                    "step": _text(step, "step"),
                    "iteration": _positive(iteration, "iteration"),
                    "trigger": _text(trigger, "trigger"),
                    "reason": _text(reason, "reason"),
                },
            )
            self._save(envelope)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize a durable record transition across threads and POSIX processes."""
        if self._transaction_depth:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        with self._thread_lock_for(self.file_path):
            self.issue_dir.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                self._transaction_depth = 1
                try:
                    yield
                finally:
                    self._transaction_depth = 0
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _thread_lock_for(cls, file_path: Path) -> threading.RLock:
        resolved = file_path.resolve()
        with cls._thread_locks_guard:
            return cls._thread_locks.setdefault(resolved, threading.RLock())

    def _load(self) -> _Envelope:
        if not self.file_path.exists():
            return _Envelope.empty()
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HumanTaskRecordSchemaError(f"cannot read human-task records: {exc}") from exc
        return _Envelope.from_dict(_as_mapping(raw, "human-task record envelope"))

    def _load_for_workflow(self, workflow_id: str, *, create: bool) -> _Envelope:
        requested = _text(workflow_id, "workflow_id")
        envelope = self._load()
        if not envelope.workflow_id:
            if not create:
                return envelope
            envelope.workflow_id = requested
            return envelope
        if envelope.workflow_id != requested:
            raise HumanTaskCorrelationError("human-task record belongs to a different workflow")
        return envelope

    @staticmethod
    def _task(envelope: _Envelope, task_id: str) -> HumanTask:
        task = envelope.tasks.get(task_id)
        if task is None:
            raise HumanTaskCorrelationError(f"unknown human task {task_id!r}")
        return task

    @staticmethod
    def _append_event(
        envelope: _Envelope, event_type: str, *, task_id: Optional[str], context: Mapping[str, Any]
    ) -> None:
        envelope.lifecycle_events.append(
            LifecycleEvent(
                event_type=event_type,
                workflow_id=envelope.workflow_id,
                task_id=task_id,
                occurred_at=_now_iso(),
                context=dict(context),
            )
        )

    def _save(self, envelope: _Envelope) -> None:
        envelope.validate()
        atomic_write_bytes(self.file_path, canonical_json(envelope.to_dict()))


def _records_by_task_id(data: Mapping[str, Any], field_name: str, parser: Any) -> dict[str, Any]:
    raw = data.get(field_name, [])
    if not isinstance(raw, list):
        raise HumanTaskRecordSchemaError(f"{field_name} must be a list")
    records: dict[str, Any] = {}
    for item in raw:
        record = parser(_as_mapping(item, field_name))
        task_id = getattr(record, "task_id", getattr(record, "id", None))
        if not isinstance(task_id, str) or not task_id or task_id in records:
            raise HumanTaskRecordSchemaError(f"{field_name} has an invalid or duplicate task id")
        records[task_id] = record
    return records


def _handoff_key(workflow_id: str, step: str, iteration: int, trigger: str, policy_id: str) -> str:
    return "\u001f".join((workflow_id, step, str(iteration), trigger, policy_id))


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HumanTaskRecordSchemaError(f"{field_name} must be an object")
    return value


def _mapping(data: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    return dict(_as_mapping(data.get(field_name), field_name))


def _string_mapping(data: Mapping[str, Any], field_name: str) -> dict[str, str]:
    return _string_mapping_value(_as_mapping(data.get(field_name), field_name), field_name)


def _string_mapping_value(value: Mapping[str, Any], field_name: str) -> dict[str, str]:
    return {
        _text(str(key), field_name): _text(str(item), field_name)
        for key, item in value.items()
    }


def _required_text(data: Mapping[str, Any], field_name: str) -> str:
    return _text(data.get(field_name), field_name)


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return _text(value, "optional value")


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _positive_int(data: Mapping[str, Any], field_name: str) -> int:
    return _positive(data.get(field_name), field_name)


def _positive(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value
