"""Resume user-input resolution for workflow step iterations."""

from __future__ import annotations

from typing import Any, Dict, Optional

# Injected when a same-session resume has no real user input (e.g. the CLI was
# interrupted by a rate limit and restarted). Phrased so agents read it as a
# system resume marker, never as a user statement or approval.
CONTINUE_USER_INPUT = (
    "[system] The previous run was interrupted (e.g. by a rate limit). "
    "Resume from where you left off. This is not a user message and does not "
    "grant any confirmation or approval."
)

# Synthetic inputs the workflow generates when the user provided nothing; only
# these may be collapsed to the resume marker on a same-session resume.
# "continue" is kept for user_input.md files written by older builds.
PLACEHOLDER_USER_INPUTS = ("", CONTINUE_USER_INPUT, "continue", "workflow execute")


def resolve_resume_user_input(
    *,
    candidate: str,
    prior_cli: Optional[str],
    prior_session_id: Optional[str],
    current_cli: Optional[str],
    current_session_id: Optional[str],
) -> str:
    """Return ``continue`` only when resuming the same CLI session with no real input.

    A candidate outside PLACEHOLDER_USER_INPUTS is the user's actual answer or
    correction and must never be discarded, even on a same-session resume.
    """
    if candidate and candidate.strip() not in PLACEHOLDER_USER_INPUTS:
        return candidate
    if not prior_cli or not prior_session_id:
        return candidate
    if not current_cli or not current_session_id:
        return candidate
    if prior_cli == current_cli and prior_session_id == current_session_id:
        return CONTINUE_USER_INPUT
    return candidate


def is_resume_iteration(
    *,
    iteration: int,
    previous_iteration_data: Optional[Dict[str, Any]],
    current_iteration_data: Optional[Dict[str, Any]],
) -> bool:
    """True when this step iteration continues a prior agent run in the same step."""
    if iteration > 1:
        return True
    if current_iteration_data and current_iteration_data.get("cli"):
        if not current_iteration_data.get("end_time"):
            return True
    return False


def load_prior_run_context(
    *,
    iteration: int,
    previous_iteration_data: Optional[Dict[str, Any]],
    current_iteration_data: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Load iteration metadata from the prior run being resumed."""
    if iteration > 1:
        return previous_iteration_data
    if current_iteration_data and current_iteration_data.get("cli"):
        if not current_iteration_data.get("end_time"):
            return current_iteration_data
    return None


def prior_cli_and_session(
    prior_context: Optional[Dict[str, Any]],
) -> tuple[Optional[str], Optional[str]]:
    if not prior_context:
        return None, None
    cli = prior_context.get("cli")
    session_id = prior_context.get("session_id")
    prior_cli = cli if isinstance(cli, str) else None
    prior_session = session_id if isinstance(session_id, str) else None
    return prior_cli, prior_session
