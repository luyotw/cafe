"""Bounded, provider-neutral state for cold backup agents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from cafe.core.packet_io import file_metadata

_SECRET = re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+")


def sanitize_failure_reason(reason: object, *, limit: int = 400) -> str:
    return _SECRET.sub(r"\1=[redacted]", str(reason).replace("\n", " "))[:limit]


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
    return {
        "schema_version": 1,
        "reason": sanitize_failure_reason(reason),
        "target": {"step": step, "iteration": iteration},
        "resolved_inputs": {key: {"mode": value.get("mode", "full"), "path": value.get("path", "")} for key, value in sorted(resolved_inputs.items())},
        "workspace": dict(workspace or {}),
        "partial": {"output": file_metadata(output_file), "checklist": file_metadata(checklist_file)},
        "operation": {"state": status, **({"id": str(operation.get("id", ""))} if operation and operation.get("id") else {})},
    }
