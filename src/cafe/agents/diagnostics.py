"""Safe diagnostics and retry policy for agent execution attempts."""

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

from cafe.agents.transport_types import TransportResult, _validated_evidence_scalar
from cafe.core.types import AgentCLI

ERROR_EXCERPT_LIMIT = 400
_TRANSIENT_CLI_UNAVAILABLE = re.compile(
    r"(?:socket\s+)?connection\s+was\s+closed\s+unexpectedly",
    re.IGNORECASE,
)
_GENERIC_CLI_UNAVAILABLE_DISPLAY = re.compile(r"^\S+ CLI unavailable\.$", re.IGNORECASE)
_NON_TRANSIENT_CLI_UNAVAILABLE = re.compile(
    r"(?:failed\s+to\s+authenticate|authentication[_\s-]*failed|"
    r"use\s+an\s+anthropic\s+api\s+key\s+instead|\b403\b|"
    r"subscription|organization|org[\s-]*policy|access\s+is\s+disabled)",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL = re.compile(r"\bbearer\s+[^\s,;]+", re.IGNORECASE)
_KEY_VALUE_CREDENTIAL = re.compile(
    r"\b(api[_-]?key|token|password|secret|credential|authorization)\s*[:=]\s*[^\s,;]+",
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


def save_stderr_diagnostics(
    streaming_output_file: str,
    stderr: str,
    *,
    diagnostics: Dict[str, Any],
    returncode: int | None,
    timeout_kind: str | None,
) -> Dict[str, Any]:
    """Keep the bounded pipe snapshot in a private, unique attempt file."""
    stream = Path(streaming_output_file)
    descriptor, filename = tempfile.mkstemp(
        prefix=f"{stream.stem}.stderr-", suffix=".log", dir=stream.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(stderr)
    except BaseException:
        Path(filename).unlink(missing_ok=True)
        raise
    retained_bytes = len(stderr.encode("utf-8"))
    return {
        "stderr_log": str(Path(filename).resolve()),
        "stderr_bytes": diagnostics["stderr_bytes"],
        "stderr_retained_bytes": retained_bytes,
        "stderr_truncated": diagnostics["stderr_bytes"] > retained_bytes,
        "stderr_complete": diagnostics["stderr_complete"],
        "stderr_read_failed": diagnostics["stderr_read_failed"],
        "returncode": returncode,
        "timeout_kind": timeout_kind,
    }


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
    record = {
        "cli": cli_name,
        "chain_role": chain_role,
        "attempt": attempt,
        "error_type": error_type,
        "error_excerpt": sanitize_error_excerpt(error),
    }
    stderr_diagnostics = getattr(error, "stderr_diagnostics", None)
    if isinstance(stderr_diagnostics, dict):
        record["stderr_diagnostics"] = {
            key: value for key, value in stderr_diagnostics.items()
            if key in {
                "stderr_log", "stderr_bytes", "stderr_retained_bytes", "stderr_truncated",
                "stderr_complete", "stderr_read_failed", "returncode", "timeout_kind",
            }
        }
    evidence = getattr(error, "transport_result", None)
    if (
        isinstance(evidence, TransportResult)
        and error_type not in {
            "conflicting_session_evidence", "invalid_evidence", "model_mismatch", "session_mismatch"
        }
        and evidence.failure_code in {None, error_type, "execution_failed"}
        and evidence.failure_code not in {
            "conflicting_session_evidence", "invalid_evidence", "model_mismatch", "session_mismatch"
        }
    ):
        try:
            record["session_id"] = _validated_evidence_scalar(
                evidence.observed_session_id, strip=True
            )
        except ValueError:
            pass
    return record
