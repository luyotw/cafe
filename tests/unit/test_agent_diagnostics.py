"""Tests for safe, durable agent-attempt diagnostics."""

import json

import pytest

from cafe.agents.diagnostics import (
    ERROR_EXCERPT_LIMIT,
    build_failed_attempt,
    is_transient_same_cli_error,
    sanitize_error_excerpt,
)
from cafe.agents.executor import AgentExecutionError
from cafe.agents.transport_types import TransportResult
from cafe.core.types import AgentCLI


@pytest.mark.parametrize("failure", ["timeout", "conflicting_session_evidence", "invalid_evidence"])
def test_failed_attempt_keeps_only_verified_session_identity(failure):
    error = AgentExecutionError("interrupted", error_type="timeout")
    error.transport_result = TransportResult(observed_session_id="observed-thread", failure_code=failure)
    record = build_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    if failure == "timeout":
        assert record["session_id"] == "observed-thread"
    else:
        assert "session_id" not in record


def test_incomplete_native_stream_retains_verified_thread():
    error = AgentExecutionError("missing completion", error_type="incomplete_stream")
    error.transport_result = TransportResult(observed_session_id="observed-thread", failure_code="incomplete_stream")
    record = build_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    assert record["session_id"] == "observed-thread"


@pytest.mark.parametrize("error_type", [
    "rate_limit", "provider_overloaded", "cli_unavailable", "pipe_read_error", None,
])
def test_other_provider_failures_retain_verified_session(error_type):
    error = AgentExecutionError("interrupted", error_type=error_type)
    error.transport_result = TransportResult(
        observed_session_id="observed-thread", failure_code=error_type or "execution_failed"
    )
    record = build_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    assert record["session_id"] == "observed-thread"


@pytest.mark.parametrize("session_id", [None, "", "  ", {}, [], "x" * 513])
def test_failed_attempt_drops_invalid_session_scalar(session_id):
    error = AgentExecutionError("limited", error_type="rate_limit")
    error.transport_result = TransportResult(
        observed_session_id=session_id, failure_code="rate_limit"
    )
    record = build_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    assert "session_id" not in record


@pytest.mark.parametrize("failure", [
    "conflicting_session_evidence", "invalid_evidence", "model_mismatch", "session_mismatch",
])
def test_failure_with_untrusted_identity_never_exposes_resumable_session(failure):
    error = AgentExecutionError("mismatch", error_type=failure)
    error.transport_result = TransportResult(observed_session_id="untrusted", failure_code=None)
    record = build_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    assert "session_id" not in record


def test_sanitized_excerpt_preserves_reason_without_sensitive_values() -> None:
    """Durable excerpts normalize useful context while redacting credentials."""
    error = AgentExecutionError(
        "connection closed unexpectedly\n"
        "Authorization: Bearer super-secret-token-value\n"
        "api_key=sk-secret-value password=hunter2\n"
        "https://alice:hunter2@example.test/run?token=opaque-secret",
        error_type="cli_unavailable",
    )

    excerpt = sanitize_error_excerpt(error)

    assert "connection closed unexpectedly" in excerpt
    assert "\n" not in excerpt
    assert "super-secret-token-value" not in excerpt
    assert "sk-secret-value" not in excerpt
    assert "hunter2" not in excerpt
    assert "opaque-secret" not in excerpt
    assert len(excerpt) <= ERROR_EXCERPT_LIMIT


def test_display_message_is_preferred_and_failed_attempt_is_serializable() -> None:
    """A classified display message is the durable diagnostic when available."""
    error = AgentExecutionError(
        "raw stderr includes token=secret-token",
        error_type="cli_unavailable",
        display_message="Claude CLI unavailable: connection closed unexpectedly.",
    )

    record = build_failed_attempt(
        cli=AgentCLI.CLAUDE,
        chain_role="primary",
        attempt=1,
        error=error,
    )

    assert record == {
        "cli": "claude",
        "chain_role": "primary",
        "attempt": 1,
        "error_type": "cli_unavailable",
        "error_excerpt": "Claude CLI unavailable: connection closed unexpectedly.",
    }


def test_sanitized_excerpt_handles_empty_and_overlong_error_text() -> None:
    """Fallback diagnostics remain useful and bounded for malformed CLI errors."""
    assert sanitize_error_excerpt(AgentExecutionError(""))

    excerpt = sanitize_error_excerpt(AgentExecutionError("x" * (ERROR_EXCERPT_LIMIT + 50)))

    assert len(excerpt) == ERROR_EXCERPT_LIMIT


def test_failed_attempt_preserves_stderr_reference_without_raw_content():
    error = AgentExecutionError("failed", error_type="timeout")
    error.stderr_diagnostics = {
        "stderr_log": "/private/iteration/stream.stderr-attempt.log",
        "stderr_bytes": 41,
        "stderr_retained_bytes": 41,
        "stderr_truncated": False,
        "stderr_complete": True,
        "stderr_read_failed": False,
        "returncode": -15,
        "timeout_kind": "idle",
        "raw_stderr": "token=private-fixture",
    }
    record = build_failed_attempt(
        cli=AgentCLI.CODEX, chain_role="primary", attempt=2, error=error
    )
    assert record["stderr_diagnostics"]["stderr_log"] == error.stderr_diagnostics["stderr_log"]
    assert record["stderr_diagnostics"]["returncode"] == -15
    assert record["stderr_diagnostics"]["timeout_kind"] == "idle"
    assert "private-fixture" not in json.dumps(record)


def test_classified_auth_error_is_not_retried_for_raw_socket_text() -> None:
    """Retry policy follows the classified display message over noisy raw stderr."""
    error = AgentExecutionError(
        "authentication failed: HTTP 403; socket connection was closed unexpectedly",
        error_type="cli_unavailable",
        display_message="Claude authentication failed. Check your credentials.",
    )

    assert not is_transient_same_cli_error(error)


def test_generic_unavailable_display_keeps_pure_socket_close_retryable() -> None:
    """A generic classified unavailable message must not hide a pure disconnect."""
    error = AgentExecutionError(
        "socket connection was closed unexpectedly",
        error_type="cli_unavailable",
        display_message="Claude CLI unavailable.",
    )

    assert is_transient_same_cli_error(error)


@pytest.mark.parametrize(
    "unavailable_signal",
    [
        "disabled Claude subscription access",
        "use an Anthropic API key instead",
        "failed to authenticate",
        "authentication_failed",
        "API Error: 403",
    ],
)
def test_generic_unavailable_display_never_retries_account_signals(
    unavailable_signal: str,
) -> None:
    """Account and policy failures remain non-transient despite socket noise."""
    error = AgentExecutionError(
        f"{unavailable_signal}; socket connection was closed unexpectedly",
        error_type="cli_unavailable",
        display_message="Claude CLI unavailable.",
    )

    assert not is_transient_same_cli_error(error)
