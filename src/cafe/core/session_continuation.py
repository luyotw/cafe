"""Explicit session-continuation policy for agent executions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

from cafe.core.audit_events import AuditEventStore
from cafe.core.types import AgentCLI


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

    # Older runtimes wrote the invocation marker after checklist preparation.
    # Accept only the specific, workflow/task-bound audit evidence for that gap.
    if set(context) <= {"effective_inputs", "workflow_completion_trusted", "agent_invoked"}:
        audit = AuditEventStore(issue_dir)
        try:
            task = audit.latest_record(
                workflow_id,
                {"agent_execution_task_materialized"},
                step=step_name,
            )
            failure = audit.latest_record(workflow_id, {"step_interrupted"}, step=step_name)
        except (OSError, ValueError):
            task = failure = None
        if (
            task is not None
            and failure is not None
            and task["data"].get("task_id") == task_id
            and task["sequence"] > failure["sequence"]
            and failure["data"].get("reason") == "agent_error"
            and failure["data"].get("attempt") == 1
            and re.fullmatch(
                rf"Step {re.escape(repr(step_name))}, skill '[^']+', "
                r"workflow\.checklist\.[^\n]+: unresolved placeholders \[[^\n]+\]",
                str(failure["data"].get("detail", "")),
            )
        ):
            return {"kind": "checklist_preparation_failed", "event_id": failure["event_id"]}

    if context.get("agent_invoked") is False:
        return {"kind": "agent_not_invoked"}
    return None
