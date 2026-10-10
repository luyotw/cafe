"""Read-only persisted accounting views, independent of caller identity or policy."""

from __future__ import annotations

import json
import os
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from cafe.core.cost import combine_cost_summaries, merge_cost_records, summarize_cost
from cafe.core.usage import _usage_parent, phase_stats_without_chat

MAX_SOURCE_BYTES = 16 * 1024 * 1024


def read_accounting_file(path: Path):
    """Bounded no-follow read; reject unsafe ancestors and special files."""
    path = Path(os.path.abspath(path))
    with _usage_parent(path) as (parent_fd, _):
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
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


def unrecorded_usage(stats, records):
    """Retain aggregate token coverage absent from deduplicated invocation usage."""
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_write_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
    )
    remaining = {}
    from cafe.core.native_accounting import accounting_admission, unrepresented_totals

    residual = unrepresented_totals(stats, accounting_admission(records))
    for field in fields:
        remaining[field] = residual.get(field)
        if remaining[field] is None and field == "cache_write_input_tokens":
            remaining[field] = residual.get("cache_creation_input_tokens")
    return remaining


def _coverage_gap(stats, records):
    try:
        return bool(
            records
            and not summarize_cost(
                records,
                legacy_cost=stats.get("total_cost_usd"),
                legacy_residual=stats.get("accounting_residual", {}).get("total_cost_usd"),
            )["counts"]["legacy"]
            and any(unrecorded_usage(stats, records).values())
        )
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        return True


def collect_cost_sources(issue_dir: Path) -> list[dict]:
    """Keep source identities stable across active/archive/snapshot copies."""
    sources = []

    def add(identity, stats, records=None, gap=False):
        stats = stats if isinstance(stats, dict) else {}
        records = records if records is not None else stats.get("cost_records", [])
        sources.append(
            dict(
                source_id=identity,
                records=records,
                legacy_cost=stats.get("total_cost_usd"),
                legacy_residual=stats.get("accounting_residual", {}).get("total_cost_usd"),
                gap=gap or _coverage_gap(stats, records),
            )
        )

    def chats(identity, groups):
        if not isinstance(groups, list):
            raise ValueError("invalid chat accounting")
        for index, group in enumerate(groups):
            add(
                f"{identity}/chat/{index}",
                group.get("stats"),
                group.get("cost_records", []),
                bool(
                    group.get("incomplete_calls")
                    or "total_cost_usd" in group.get("unknown_fields", [])
                ),
            )

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
            if (
                stats.get("cost_records")
                or not groups
                or stats.get("total_cost_usd")
                or any(unrecorded_usage(stats, []).values())
            ):
                add(identity, stats)
            chats(identity, groups)
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            add(identity, {}, gap=True)
            sources[-1]["read_error"] = path.exists() or path.is_symlink()
    path = issue_dir / "issue.yaml"
    if path.exists() or path.is_symlink():
        try:
            chats("issue", read_accounting_file(path).get("chat_usage", []))
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            add("issue", {}, gap=True)
            sources[-1]["read_error"] = path.exists() or path.is_symlink()
    return sources


def summarize_sources(sources, *, exclude_ids=(), ambiguous_sources=()):
    """Deduplicate globally, retaining each source's non-overlapping legacy remainder."""
    seen_sources, records, parts, conflicting = set(), {}, [], set()
    excluded, ambiguous = set(exclude_ids), set(ambiguous_sources)
    for source in sources:
        identity = source["source_id"]
        if identity in seen_sources:
            continue
        seen_sources.add(identity)
        raw = source.get("records", [])
        try:
            if not isinstance(raw, list):
                raise ValueError("invalid accounting records")
            valid = []
            for record in raw:
                try:
                    if (
                        not isinstance(record, dict)
                        or not isinstance(record.get("invocation_id"), str)
                        or not record["invocation_id"]
                    ):
                        raise ValueError("invalid invocation identity")
                    summarize_cost([record])
                    valid.append(record)
                except (ValueError, TypeError, KeyError, InvalidOperation):
                    parts.append(summarize_cost([]))
            full = summarize_cost(
                valid,
                legacy_cost=source.get("legacy_cost"),
                legacy_residual=source.get("legacy_residual"),
            )
            for record in valid:
                key = record["invocation_id"]
                if key in excluded or key in conflicting:
                    continue
                if key in records and "native_usage" in record and "native_usage" in records[key]:
                    records[key] = merge_cost_records([records[key]], [record])[0]
                elif key in records and records[key] != record:
                    parts.append(summarize_cost([]))
                    conflicting.add(key)
                    records.pop(key)
                else:
                    records[key] = record
            residual = full["legacy"]
            if identity in ambiguous:
                residual = Decimal(0)
            if residual:
                parts.append(summarize_cost([], legacy_cost=residual))
            if source.get("gap") or identity in ambiguous or (not raw and not residual):
                parts.append(summarize_cost([]))
        except (ValueError, TypeError, KeyError, InvalidOperation):
            parts.append(summarize_cost([]))
    if records:
        # Each source has already been validated before being admitted here.
        parts.append(summarize_cost(list(records.values())))
    return combine_cost_summaries(parts or [summarize_cost([])])


def accounting_source_versions(issue_dir, *, extra_paths=()):
    """Observe source identities/versions around a read without creating locks."""
    paths = [issue_dir / "issue.yaml", *extra_paths]
    paths.extend(issue_dir.glob("*/iteration_*/iteration.json"))
    paths.extend(issue_dir.glob("*/iteration_*/context.json"))
    versions = {}
    for path in paths:
        try:
            info = path.lstat()
            versions[str(path)] = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            versions[str(path)] = None
    return versions
