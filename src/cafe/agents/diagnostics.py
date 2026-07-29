"""Safe diagnostics and retry policy for agent execution attempts."""

import re
from typing import Any, Dict, Union

from cafe.core.types import AgentCLI

ERROR_EXCERPT_LIMIT = 400
_TRANSIENT_CLI_UNAVAILABLE = re.compile(
    r"(?:socket\s+)?connection\s+was\s+closed\s+unexpectedly",
    re.IGNORECASE,
)
_GENERIC_CLI_UNAVAILABLE_DISPLAY = re.compile(r"^\S+ CLI unavailable\.$", re.IGNORECASE)
_NON_TRANSIENT_CLI_UNAVAILABLE = re.compile(
    r"(?:failed\s+to\s+authenticate|authentication[_\s-]*failed|\b403\b|"
    r"subscription|organization|org[\s-]*policy|access\s+is\s+disabled)",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL = re.compile(r"\bbearer\s+[^\s,;]+", re.IGNORECASE)
_KEY_VALUE_CREDENTIAL = re.compile(
    r"\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_URL_CREDENTIAL = re.compile(r"(https?://)[^\s:/@]+:[^\s@/]+@", re.IGNORECASE)


def sanitize_error_excerpt(error: BaseException) -> str:
    """Return a bounded, single-line error summary safe for durable records."""
    display_message = getattr(error, "display_message", None)
    text = display_message if isinstance(display_message, str) and display_message else str(error)
    text = " ".join(text.split())
    text = _URL_CREDENTIAL.sub(r"\1<redacted>@", text)
    text = _BEARER_CREDENTIAL.sub("Bearer <redacted>", text)
    text = _KEY_VALUE_CREDENTIAL.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    if not text:
        text = "Agent execution failed"
    return text[:ERROR_EXCERPT_LIMIT]


def is_transient_same_cli_error(error: BaseException) -> bool:
    """Return whether an error merits the one permitted same-CLI retry."""
    if getattr(error, "error_type", None) != "cli_unavailable":
        return False
    display_message = getattr(error, "display_message", None)
    if isinstance(display_message, str) and display_message:
        if _TRANSIENT_CLI_UNAVAILABLE.search(display_message):
            return True
        if not _GENERIC_CLI_UNAVAILABLE_DISPLAY.fullmatch(display_message.strip()):
            return False

    raw_text = str(error)
    return bool(
        _TRANSIENT_CLI_UNAVAILABLE.search(raw_text)
        and not _NON_TRANSIENT_CLI_UNAVAILABLE.search(raw_text)
    )


def build_failed_attempt(
    *,
    cli: Union[AgentCLI, str],
    chain_role: str,
    attempt: int,
    error: BaseException,
) -> Dict[str, Any]:
    """Build the additive JSON-safe record for one unsuccessful CLI call."""
    cli_name = cli.value if isinstance(cli, AgentCLI) else str(cli)
    error_type = getattr(error, "error_type", None) or type(error).__name__
    return {
        "cli": cli_name,
        "chain_role": chain_role,
        "attempt": attempt,
        "error_type": error_type,
        "error_excerpt": sanitize_error_excerpt(error),
    }
