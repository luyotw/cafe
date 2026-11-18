"""Reconcile continuation of one saved HumanTask result against workflow audit evidence."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

from cafe.core.blackboard import (
    BlackboardState,
    BlackboardStore,
    EventEntry,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.human_task_records import (
    HumanTask,
    HumanTaskCorrelationError,
    HumanTaskRecordStore,
    HumanTaskStatus,
    TaskResult,
)
from cafe.core.human_task_snapshot import durable_task_matches_current_handoff
from cafe.core.human_tasks import AGENT_EXECUTION_INTERRUPTED_TRIGGER
from cafe.core.task_inbox import TaskInboxError
from cafe.core.workflow_models import BatonRejected
from cafe.utils.file_lock import handoff_execution_lock
from cafe.utils.issue_paths import validate_issue_directory, validate_issue_name


@dataclass(frozen=True)
class ContinuationRequest:
    issue: str
    workflow_id: str
    task_id: str
    result_id: str

    def __post_init__(self) -> None:
        validate_issue_name(self.issue)

    @property
    def issue_dir(self) -> Path:
        return Path(".cafe") / "issues" / self.issue

    def matches_workflow(self, store: BlackboardStore) -> bool:
        validate_issue_directory(self.issue_dir)
        raw = json.loads(store.file_path.read_text(encoding="utf-8"))
        return isinstance(raw, dict) and raw.get("workflow_id") == self.workflow_id

    def response(self, status: str) -> dict[str, str]:
        return {
            "issue": self.issue,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "result_id": self.result_id,
            "status": status,
        }


@dataclass(frozen=True)
class ContinuationObservation:
    status: str
    state: BlackboardState | None = None
    unprojected_task: HumanTask | None = None
    result: TaskResult | None = None


class ContinuationSupersededError(RuntimeError):
    """The saved result no longer authorizes the current workflow handoff."""


@dataclass(frozen=True)
class ContinuationBinding:
    request: ContinuationRequest
    handoff: HandoffContract

    def read_current(self, store: BlackboardStore) -> BlackboardState:
        """Read the exact projected position while serializing with handoff writers."""
        try:
            with store.state_transaction():
                state = store.load_read_only()
                if (
                    state.workflow_id == self.request.workflow_id
                    and state.current_step == self.handoff.to_step
                    and state.handoff_contract == self.handoff
                ):
                    return state
        except (BatonRejected, OSError, ValueError) as exc:
            raise ContinuationSupersededError("The saved handoff could not be verified") from exc
        raise ContinuationSupersededError("The saved handoff has changed")

    def claim_current(self, store: BlackboardStore, dispatch: ExitStack) -> BlackboardState:
        """Keep the checked handoff exclusive until the admitted dispatch returns."""
        with store.state_transaction():
            state = self.read_current(store)
            dispatch.enter_context(handoff_execution_lock(store.issue_dir))
            return state


def observe_continuation(
    request: ContinuationRequest, *, reconcile: bool = False,
) -> ContinuationObservation:
    """Read one result and its workflow, repairing audited state only under the caller's lock."""
    store = BlackboardStore(request.issue_dir)
    records = HumanTaskRecordStore(request.issue_dir)
    try:
        if not request.matches_workflow(store):
            return ContinuationObservation("stale_workflow")
        try:
            task = records.get_task(request.task_id)
        except HumanTaskCorrelationError:
            task = None
        result = records.get_result(request.task_id) if task is not None else None
        if (
            task is None or result is None or result.id != request.result_id
            or task.status is not HumanTaskStatus.COMPLETED
            or task.workflow_id != request.workflow_id or result.workflow_id != request.workflow_id
        ):
            raise TaskInboxError(
                "result_mismatch", "No saved result matches this workflow and task.",
                recovery="Read the exact IDs from task completion or inspection.",
                issue=request.issue, task_id=request.task_id, workflow_id=request.workflow_id,
            )
        if task.capability_approval is not None:
            raise TaskInboxError(
                "unsupported_continuation",
                "Capability approvals use their existing continuation path.",
                recovery="Follow the workflow's capability approval protocol.",
                issue=request.issue, task_id=request.task_id, workflow_id=request.workflow_id,
            )
        try:
            state = store.load_read_only()
        except (BatonRejected, OSError, ValueError):
            if not reconcile or not request.matches_workflow(store):
                return ContinuationObservation("unknown")
            state = store.load_or_create(task.step)
    except (BatonRejected, OSError, ValueError):
        return ContinuationObservation("unknown")
    if state.workflow_id != request.workflow_id:
        return ContinuationObservation("stale_workflow")

    for index, event in enumerate(state.events):
        data = event.data
        if (
            event.event_type == "human_task_completed"
            and data.get("human_task_id") == request.task_id
            and data.get("result_id") == request.result_id
            and isinstance(data.get("continuation_handoff_created_at"), str)
        ):
            target = data.get("to_step")
            if not isinstance(target, str):
                return ContinuationObservation("unknown")
            if target == "_done" or _workflow_advanced(state.events[index + 1:], target):
                return ContinuationObservation("advanced")
            contract = state.handoff_contract
            if (
                contract is None or contract.to_owner is not HandoffOwner.AGENT
                or contract.intent is not HandoffIntent.AWAIT_AGENT
                or contract.created_at != data["continuation_handoff_created_at"]
                or contract.from_step != task.step
                or contract.to_step != target or state.current_step != target
            ):
                return ContinuationObservation("unknown")
            break
    else:
        if (
            task.handoff_key.startswith("user-handoff:")
            and durable_task_matches_current_handoff(
                replace(task, status=HumanTaskStatus.PENDING), state,
            )
        ):
            return ContinuationObservation("pending", state, task, result)
        return ContinuationObservation("unknown")

    failed = any(
        event.event_type == "human_task_continuation_failed"
        and event.data.get("human_task_id") == request.task_id
        and event.data.get("result_id") == request.result_id
        for event in state.events[index + 1:]
    )
    return ContinuationObservation("retryable_failure" if failed else "pending", state)


def _workflow_advanced(events: list[EventEntry], target: str) -> bool:
    """Confirm the first subsequent durable outcome came from this continuation."""
    for event in events:
        if event.event_type == "transition":
            return event.data.get("from") == target
        if event.event_type == "workflow_completed" or (
            event.event_type == "human_task_materialized"
            and event.data.get("trigger") != AGENT_EXECUTION_INTERRUPTED_TRIGGER
        ):
            return event.step == target
    return False
