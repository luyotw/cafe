"""Bounded, provider-neutral state for cold backup agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.core.packet_io import compact_json, sha256_bytes


TAKEOVER_SNAPSHOT_MAX_BYTES = 16 * 1024
TAKEOVER_FILE_INSPECTION_MAX_BYTES = 1024 * 1024


def sanitize_failure_reason(reason: object, *, limit: int = 400) -> str:
    return sanitize_error_excerpt(reason).replace("<redacted>", "[redacted]")[:limit]


def _checklist_progress(content: bytes) -> dict[str, int]:
    try:
        markers = re.findall(
            r"(?m)^\s*(?:[-*]\s+)?\[([ xX])\]",
            content.decode("utf-8"),
        )
    except UnicodeDecodeError:
        return {}
    return {
        "completed": sum(marker.lower() == "x" for marker in markers),
        "pending": sum(marker == " " for marker in markers),
    }


def _takeover_file_metadata(path: str | Path) -> tuple[dict[str, Any], bytes | None]:
    """Read at most the inspection limit while keeping large files addressable."""
    source = Path(path)
    shown_path = source.as_posix()
    try:
        if not source.exists():
            return {"path": shown_path, "state": "missing"}, None
        if not source.is_file():
            return {"path": shown_path, "state": "not_file"}, None
        with source.open("rb") as handle:
            content = handle.read(TAKEOVER_FILE_INSPECTION_MAX_BYTES + 1)
            size = source.stat().st_size
    except OSError:
        return {"path": shown_path, "state": "unreadable"}, None
    if len(content) > TAKEOVER_FILE_INSPECTION_MAX_BYTES:
        return (
            {
                "path": shown_path,
                "state": "file",
                "bytes": max(size, len(content)),
                "content_inspection": "omitted_size_limit",
            },
            None,
        )
    return (
        {
            "path": shown_path,
            "state": "file",
            "bytes": len(content),
            "sha256": sha256_bytes(content),
        },
        content,
    )


def build_takeover_snapshot(
    *,
    reason: object,
    step: str,
    iteration: int,
    resolved_inputs: Mapping[str, Mapping[str, str]],
    output_file: str | Path,
    checklist_file: str | Path,
    workspace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create durable metadata only; never read session logs or file bodies."""
    checklist, checklist_content = _takeover_file_metadata(checklist_file)
    if checklist_content is not None:
        checklist.update(_checklist_progress(checklist_content))
    output, _ = _takeover_file_metadata(output_file)
    snapshot = {
        "schema_version": 1,
        "reason": sanitize_failure_reason(reason),
        "target": {"step": step, "iteration": iteration},
        "resolved_inputs": {
            key: {"mode": value.get("mode", "full"), "path": value.get("path", "")}
            for key, value in sorted(resolved_inputs.items())
        },
        "workspace": dict(workspace or {}),
        "partial": {"output": output, "checklist": checklist},
    }
    size = len(compact_json(snapshot).encode("utf-8"))
    if size > TAKEOVER_SNAPSHOT_MAX_BYTES:
        raise ValueError(
            f"Takeover snapshot exceeds {TAKEOVER_SNAPSHOT_MAX_BYTES}-byte limit: {size} bytes"
        )
    return snapshot
