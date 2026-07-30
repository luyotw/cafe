"""Tests for resume user-input resolution."""

from cafe.core.resume_user_input import (
    CONTINUE_USER_INPUT,
    is_followup_iteration,
    is_resume_iteration,
    load_prior_run_context,
    prior_cli_and_session,
    resolve_resume_user_input,
)


def test_resolve_resume_user_input_first_start_returns_candidate() -> None:
    result = resolve_resume_user_input(
        candidate="Initial requirements",
        prior_cli=None,
        prior_session_id=None,
        current_cli="codex",
        current_session_id="session-1",
    )
    assert result == "Initial requirements"


def test_resolve_resume_user_input_same_session_keeps_real_input() -> None:
    result = resolve_resume_user_input(
        candidate="Please revise the spec",
        prior_cli="codex",
        prior_session_id="abc",
        current_cli="codex",
        current_session_id="abc",
    )
    assert result == "Please revise the spec"


def test_resolve_resume_user_input_same_session_empty_input_returns_continue() -> None:
    result = resolve_resume_user_input(
        candidate="",
        prior_cli="codex",
        prior_session_id="abc",
        current_cli="codex",
        current_session_id="abc",
    )
    assert result == CONTINUE_USER_INPUT


def test_resolve_resume_user_input_different_session_returns_candidate() -> None:
    candidate = "Clarification: use option B"
    result = resolve_resume_user_input(
        candidate=candidate,
        prior_cli="codex",
        prior_session_id="abc",
        current_cli="codex",
        current_session_id="xyz",
    )
    assert result == candidate


def test_resolve_resume_user_input_different_cli_returns_candidate() -> None:
    candidate = "workflow execute"
    result = resolve_resume_user_input(
        candidate=candidate,
        prior_cli="codex",
        prior_session_id="abc",
        current_cli="gemini",
        current_session_id="abc",
    )
    assert result == candidate


def test_resolve_resume_user_input_missing_prior_session_returns_candidate() -> None:
    result = resolve_resume_user_input(
        candidate="workflow execute",
        prior_cli="codex",
        prior_session_id=None,
        current_cli="codex",
        current_session_id="abc",
    )
    assert result == "workflow execute"


def test_completed_correction_is_followup_but_not_resume() -> None:
    args = {
        "iteration": 2,
        "previous_iteration_data": {"cli": "codex", "session_id": "a", "end_time": "done"},
        "current_iteration_data": None,
    }
    assert is_followup_iteration(**args)
    assert not is_resume_iteration(**args)


def test_is_resume_iteration_first_start_is_false() -> None:
    assert not is_resume_iteration(
        iteration=1,
        previous_iteration_data=None,
        current_iteration_data=None,
    )


def test_is_resume_iteration_interrupted_reuse() -> None:
    assert is_resume_iteration(
        iteration=1,
        previous_iteration_data=None,
        current_iteration_data={"cli": "codex", "session_id": "a"},
    )


def test_load_prior_run_context_does_not_treat_completed_correction_as_resume() -> None:
    prev = {"cli": "codex", "session_id": "abc", "end_time": "done"}
    assert (
        load_prior_run_context(
            iteration=2,
            previous_iteration_data=prev,
            current_iteration_data=None,
        )
        is None
    )


def test_load_prior_run_context_from_current_partial() -> None:
    current = {"cli": "codex", "session_id": "abc"}
    assert (
        load_prior_run_context(
            iteration=1,
            previous_iteration_data=None,
            current_iteration_data=current,
        )
        == current
    )


def test_prior_cli_and_session_extracts_strings() -> None:
    assert prior_cli_and_session({"cli": "codex", "session_id": "sid"}) == ("codex", "sid")
    assert prior_cli_and_session(None) == (None, None)
