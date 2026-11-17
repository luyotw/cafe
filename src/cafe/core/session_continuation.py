"""Explicit session-continuation policy for agent executions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

from cafe.core.audit_events import AuditEventStore
from cafe.core.types import AgentCLI

CORRECTION_TODO_PROVENANCE_MISSING = "Correction Todo provenance is missing"
INVALID_CONTEXT_PACKET_DECISION = "Invalid persisted context packet decision"


def _has_approved_preparation_retry(issue_dir, workflow_id, step_name, marker, failed_at):
    """Check that an approved first invocation was still preparing its context."""
    from cafe.core.human_task_records import HumanTaskRecordError, HumanTaskRecordStore, HumanTaskStatus

    iteration = marker.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        return False
    directory = issue_dir / step_name / f"iteration_{iteration:03d}"
    if any(directory.glob("context_*.json")):
        return False
    try:
        records = HumanTaskRecordStore(issue_dir)
        failure_time = datetime.fromisoformat(failed_at)
        completed = [
            task for task in records.tasks()
            if task.workflow_id == workflow_id and task.step == step_name
            and task.iteration == iteration and task.policy_id == "agent-execution-interrupted"
            and task.status is HumanTaskStatus.COMPLETED and task.completed_at
            and datetime.fromisoformat(task.completed_at) <= failure_time
        ]
        if not completed:
            return False
        task = max(completed, key=lambda item: datetime.fromisoformat(item.completed_at))
        result = records.get_result(task.id)
    except (HumanTaskRecordError, OSError, TypeError, ValueError):
        return False
    if result is None or result.payload.get("decision") != "retry_fresh_session":
        return False
    recovery = result.payload.get("session_continuation")
    return (
        isinstance(recovery, dict) and recovery.get("policy") == "new"
        and recovery.get("workflow_id") == workflow_id and recovery.get("human_task_id") == task.id
        and recovery.get("step") == step_name and recovery.get("iteration") == iteration
        and isinstance(recovery.get("preparation"), dict)
        and recovery["preparation"].get("kind") == "correction_preparation_failed"
        and result.payload.get("continuation") == step_name
    )


class SessionContinuationPolicy(str, Enum):
    """How an execution may reuse a persisted CLI session."""

    AUTO = "auto"
    NEW = "new"
    RESUME_EXACT = "resume_exact"


@dataclass(frozen=True)
class SessionContinuation:
    """One invocation-scoped continuation decision."""

    policy: SessionContinuationPolicy
    cli: Optional[AgentCLI] = None
    session_id: Optional[str] = None

    @classmethod
    def auto(cls) -> "SessionContinuation":
        return cls(SessionContinuationPolicy.AUTO)

    @classmethod
    def new(cls) -> "SessionContinuation":
        return cls(SessionContinuationPolicy.NEW)

    @classmethod
    def resume_exact(
        cls,
        cli: AgentCLI,
        session_id: str,
    ) -> "SessionContinuation":
        return cls(
            SessionContinuationPolicy.RESUME_EXACT,
            cli=cli,
            session_id=session_id,
        )

    @property
    def is_exact(self) -> bool:
        return (
            self.policy == SessionContinuationPolicy.RESUME_EXACT
            and self.cli is not None
            and bool(self.session_id)
        )

    def to_dict(self) -> dict[str, Optional[str]]:
        return {
            "policy": self.policy.value,
            "cli": self.cli.value if self.cli is not None else None,
            "session_id": self.session_id,
        }


def exact_continuation_from_context(
    context: Optional[dict[str, Any]],
    *,
    configured_clis: Optional[Sequence[AgentCLI]] = None,
) -> Optional[SessionContinuation]:
    """Return an exact continuation only for a complete, configured CLI/session pair."""
    if not context:
        return None

    raw_cli = context.get("cli")
    session_id = context.get("session_id")
    if not isinstance(raw_cli, str) or not isinstance(session_id, str) or not session_id:
        return None

    try:
        cli = AgentCLI(raw_cli)
    except ValueError:
        return None

    if configured_clis is not None and cli not in configured_clis:
        return None
    return SessionContinuation.resume_exact(cli, session_id)


def pre_invocation_recovery_evidence(
    context: dict[str, Any],
    *,
    issue_dir: Path,
    workflow_id: str,
    task_id: str,
    step_name: str,
) -> Optional[dict[str, Any]]:
    """Identify preparation failures without inventing a prior provider identity."""
    if ("agent_invoked" in context and context["agent_invoked"] is not False) or any(
        key in context
        for key in (
            "cli",
            "session_id",
            "session_continuation",
            "failed_attempts",
            "response",
            "start_time",
            "stats",
            "streaming_log",
        )
    ):
        return None

    # Older runtimes wrote the invocation marker after skill/workspace/checklist preparation.
    # Accept only the specific, workflow/task-bound audit evidence for that gap.
    if set(context) <= {
        "effective_inputs",
        "workflow_completion_trusted",
        "agent_invoked",
        "agent_interruption",
    }:
        audit = AuditEventStore(issue_dir)
        try:
            task = audit.latest_record(
                workflow_id,
                {"agent_execution_task_materialized"},
                step=step_name,
            )
            failure = audit.latest_record(workflow_id, {"step_interrupted"}, step=step_name)
            prepared = (
                audit.latest_record(workflow_id, {"agent_interruption_prepared"}, step=step_name)
                if "agent_interruption" in context
                else None
            )
        except (OSError, ValueError):
            task = failure = prepared = None
        marker = context.get("agent_interruption")
        marker_matches = True
        if "agent_interruption" in context:
            marker_matches = (
                task is not None
                and failure is not None
                and prepared is not None
                and isinstance(marker, dict)
                and marker.get("workflow_id") == workflow_id
                and marker.get("step") == step_name
                and prepared["data"].get("marker") == marker
                and failure["sequence"] < prepared["sequence"] < task["sequence"]
            )
        correction_preparation = (
            "agent_interruption" in context
            and context.get("workflow_completion_trusted") is False
            and failure is not None
            and failure["data"].get("detail") == CORRECTION_TODO_PROVENANCE_MISSING
        )
        packet_preparation = (
            marker_matches and isinstance(marker, dict)
            and context.get("workflow_completion_trusted") is False and failure is not None
            and failure["data"].get("detail") == INVALID_CONTEXT_PACKET_DECISION
            and _has_approved_preparation_retry(issue_dir, workflow_id, step_name, marker, failure["timestamp"])
        )
        if (
            marker_matches
            and task is not None
            and failure is not None
            and task["data"].get("task_id") == task_id
            and task["sequence"] > failure["sequence"]
            and failure["data"].get("reason") == "agent_error"
            and (failure["data"].get("attempt") == 1 or correction_preparation or packet_preparation)
        ):
            marker = context.get("agent_interruption")
            if marker is not None:
                prepared = audit.latest_record(
                    workflow_id, {"agent_interruption_prepared"}, step=step_name,
                )
                if (
                    not isinstance(marker, dict)
                    or marker.get("workflow_id") != workflow_id
                    or marker.get("step") != step_name
                    or marker.get("reason") != "agent_error"
                    or prepared is None
                    or not failure["sequence"] < prepared["sequence"] < task["sequence"]
                    or prepared["data"].get("marker") != marker
                ):
                    return None
            detail = str(failure["data"].get("detail", ""))
            if correction_preparation:
                return {"kind": "correction_preparation_failed", "event_id": failure["event_id"]}
            if packet_preparation:
                return {"kind": "context_packet_preparation_failed", "event_id": failure["event_id"]}
            if (
                marker is not None
                and detail == "Fresh-session recovery has no matching preparation evidence"
            ):
                # Session selection precedes invocation. A rejected preparation
                # must remain recoverable without inventing a provider session.
                return {"kind": "session_preparation_failed", "event_id": failure["event_id"]}
            if marker is not None and re.fullmatch(
                r"Skill not found: Unmanaged or changed native skill conflict: [^\n]+\. "
                r"Back up and relocate the entry, then rerun prepare or launch; "
                r"legacy names or matching contents alone do not prove ownership\.",
                detail,
            ):
                return {"kind": "native_skill_preparation_failed", "event_id": failure["event_id"]}
            if re.fullmatch(
                rf"Step {re.escape(repr(step_name))}, skill '[^']+', "
                r"workflow\.checklist\.[^\n]+: unresolved placeholders \[[^\n]+\]",
                detail,
            ):
                return {"kind": "checklist_preparation_failed", "event_id": failure["event_id"]}
            if re.fullmatch(
                r"Skill not found: Unmanaged or changed native skill conflict: [^\n]+",
                detail,
            ):
                return {"kind": "skill_preparation_failed", "event_id": failure["event_id"]}
            if re.fullmatch(
                r"workspace artifact '[^'\n]+' is stale or contradictory: "
                r"workspace worktree is dirty; refresh it or resolve the conflict before retrying",
                detail,
            ):
                return {"kind": "workspace_preparation_failed", "event_id": failure["event_id"]}

    if context.get("agent_invoked") is False:
        return {"kind": "agent_not_invoked"}
    return None
