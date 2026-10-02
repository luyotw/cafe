"""Reuse existing iteration telemetry without resolving caller authority."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from cafe.core.types import TokenUsage
from cafe.core.workspace_lock import workspace_execution_lock


def merge_token_usage_stats(existing: Any, incoming: TokenUsage) -> Dict[str, Any]:
    """Merge one raw attempt into the existing iteration stats shape."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    incoming_data = incoming.model_dump()
    additive_fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
        "total_cost_usd",
    )
    for field in additive_fields:
        prior = merged.get(field, 0)
        value = incoming_data.get(field, 0)
        merged[field] = (prior if isinstance(prior, (int, float)) else 0) + (
            value if isinstance(value, (int, float)) else 0
        )

    for field in ("duration_ms", "duration_api_ms"):
        prior = merged.get(field)
        value = incoming_data.get(field)
        if isinstance(value, int):
            merged[field] = (prior if isinstance(prior, int) else 0) + value
        elif field not in merged:
            merged[field] = None

    prior_turns = merged.get("turn_usages")
    incoming_turns = incoming_data.get("turn_usages")
    merged["turn_usages"] = (list(prior_turns) if isinstance(prior_turns, list) else []) + (
        list(incoming_turns) if isinstance(incoming_turns, list) else []
    )
    return merged


def iteration_usage_sink(repository_root: Path, context_file: Path):
    """Pin an existing caller-admitted metadata target; never create an iteration."""
    root = Path(repository_root).resolve()
    target = Path(context_file).absolute()
    if target.resolve() != target or not target.is_relative_to(root):
        raise ValueError("usage target must remain within its admitted workspace")
    if not target.is_file():
        return None
    original = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(original, dict) or not isinstance(original.get("iteration"), int):
        return None
    identity = (original.get("iteration"), original.get("timestamp"))

    def persist(usage: TokenUsage):
        with workspace_execution_lock(root):
            if target.resolve() != target:
                raise ValueError("usage target changed")
            current = json.loads(target.read_text(encoding="utf-8"))
            if (
                not isinstance(current, dict)
                or (current.get("iteration"), current.get("timestamp")) != identity
            ):
                raise ValueError("admitted iteration identity changed")
            current["stats"] = merge_token_usage_stats(current.get("stats"), usage)
            descriptor, temporary = tempfile.mkstemp(prefix=".usage-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(current, handle, ensure_ascii=False, indent=2)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    return persist
