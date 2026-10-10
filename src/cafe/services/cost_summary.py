"""Read-only persisted accounting views, independent of caller identity or policy."""
from __future__ import annotations

import json
import os
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from cafe.core.cost import combine_cost_summaries, summarize_cost
from cafe.core.usage import phase_stats_without_chat

MAX_SOURCE_BYTES = 16 * 1024 * 1024


def read_accounting_file(path: Path):
    """Bounded no-follow read; reject unsafe ancestors and special files."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("accounting source traverses a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise ValueError("accounting source is unsafe or oversized")
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("accounting source is oversized")
    value = yaml.safe_load(data) if path.suffix == ".yaml" else json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("accounting source must be an object")
    return value


def collect_cost_sources(issue_dir: Path) -> list[dict]:
    """Keep source identities stable across active/archive/snapshot copies."""
    sources = []

    def add(identity, stats, records=None, gap=False):
        stats = stats if isinstance(stats, dict) else {}
        sources.append(dict(source_id=identity, records=records if records is not None
                            else stats.get("cost_records", []),
                            legacy_cost=stats.get("total_cost_usd"), gap=gap))

    def chats(identity, groups):
        if not isinstance(groups, list):
            raise ValueError("invalid chat accounting")
        for index, group in enumerate(groups):
            add(f"{identity}/chat/{index}", group.get("stats"), group.get("cost_records", []),
                bool(group.get("incomplete_calls") or
                     "total_cost_usd" in group.get("unknown_fields", [])))

    candidates = sorted(issue_dir.glob("*/iteration_*"))
    for directory in candidates:
        identity = directory.relative_to(issue_dir).as_posix()
        path = directory / "iteration.json"
        if not path.exists() and not path.is_symlink():
            path = directory / "context.json"
        try:
            data = read_accounting_file(path)
            groups = data.get("chat_usage", [])
            stats = phase_stats_without_chat(data.get("stats"), groups)
            # An all-chat aggregate does not create an additional unknown execution.
            if stats.get("cost_records") or not groups or stats.get("total_cost_usd"):
                add(identity, stats)
            chats(identity, groups)
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            add(identity, {}, gap=True)
    path = issue_dir / "issue.yaml"
    if path.exists() or path.is_symlink():
        try:
            chats("issue", read_accounting_file(path).get("chat_usage", []))
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            add("issue", {}, gap=True)
    return sources


def summarize_sources(sources, *, exclude_ids=(), ambiguous_sources=()):
    """Deduplicate globally, retaining each source's non-overlapping legacy remainder."""
    seen_sources, records, parts = set(), {}, []
    excluded, ambiguous = set(exclude_ids), set(ambiguous_sources)
    for source in sources:
        identity = source["source_id"]
        if identity in seen_sources:
            continue
        seen_sources.add(identity)
        raw = source.get("records", [])
        try:
            if not isinstance(raw, list) or any(
                not isinstance(r, dict) or not isinstance(r.get("invocation_id"), str)
                or not r["invocation_id"] for r in raw
            ):
                raise ValueError("invalid invocation identity")
            full = summarize_cost(raw, legacy_cost=source.get("legacy_cost"))
            for record in raw:
                key = record["invocation_id"]
                if key in excluded:
                    continue
                if key in records and records[key] != record:
                    parts.append(summarize_cost([]))
                else:
                    records[key] = record
            residual = full["legacy"]
            if identity in ambiguous:
                residual = Decimal(0)
            if residual:
                parts.append(summarize_cost([], legacy_cost=residual))
            if (source.get("gap") or identity in ambiguous or
                    (not raw and not residual)):
                parts.append(summarize_cost([]))
        except (ValueError, TypeError, KeyError, InvalidOperation):
            parts.append(summarize_cost([]))
    if records:
        # Each source has already been validated before being admitted here.
        parts.append(summarize_cost(list(records.values())))
    return combine_cost_summaries(parts or [summarize_cost([])])
