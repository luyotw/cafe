"""Bounded, provider-neutral state for cold backup agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.core.packet_io import file_metadata


def sanitize_failure_reason(reason: object, *, limit: int = 400) -> str:
    return sanitize_error_excerpt(reason).replace("<redacted>", "[redacted]")[:limit]


def _checklist_progress(path: str | Path) -> dict[str, int]:
    try:
        markers = re.findall(r"(?m)^\s*(?:[-*]\s+)?\[([ xX])\]", Path(path).read_text())
    except (OSError, UnicodeDecodeError):
        return {}
    return {
        "completed": sum(marker.lower() == "x" for marker in markers),
        "pending": sum(marker == " " for marker in markers),
    }


def build_takeover_snapshot(
    *,
    reason: object,
    step: str,
    iteration: int,
    resolved_inputs: Mapping[str, Mapping[str, str]],
    output_file: str | Path,
    checklist_file: str | Path,
    operation: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create durable metadata only; never read session logs or file bodies."""
    status = "absent"
    if operation:
        candidate = operation.get("state")
        status = str(candidate) if candidate in {"running", "terminal", "unknown"} else "unknown"
    checklist = file_metadata(checklist_file)
    if checklist.get("state") == "file":
        checklist.update(_checklist_progress(checklist_file))
    return {
        "schema_version": 1,
        "reason": sanitize_failure_reason(reason),
        "target": {"step": step, "iteration": iteration},
        "resolved_inputs": {key: {"mode": value.get("mode", "full"), "path": value.get("path", "")} for key, value in sorted(resolved_inputs.items())},
        "workspace": dict(workspace or {}),
        "partial": {"output": file_metadata(output_file), "checklist": checklist},
        "operation": {"state": status, **({"id": str(operation.get("id", ""))} if operation and operation.get("id") else {})},
    }
