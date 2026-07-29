"""Tests for safe, durable agent-attempt diagnostics."""

from cafe.agents.diagnostics import (
    ERROR_EXCERPT_LIMIT,
    build_failed_attempt,
    is_transient_same_cli_error,
    sanitize_error_excerpt,
)
from cafe.agents.executor import AgentExecutionError
from cafe.core.types import AgentCLI


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
