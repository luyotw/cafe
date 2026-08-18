"""Explicit session-continuation policy for agent executions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence

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
