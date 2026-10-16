"""Read-only persisted accounting views, independent of caller identity or policy."""

from __future__ import annotations

import json
import os
import re
import stat
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

import yaml

from cafe.core.cost import (
    COUNTERS,
    combine_cost_summaries,
    merge_cost_records,
    source_remainder,
    summarize_cost,
)
from cafe.core.usage import _usage_parent, chat_usage_has_gap, phase_stats_without_chat

MAX_SOURCE_BYTES = 16 * 1024 * 1024


def confirmed_no_model_source(source):
    """Validate retained owner evidence; an empty source alone never proves zero."""
    proof = source.get("no_model_execution")
    if proof is None:
        return False
    if (
        not isinstance(proof, dict)
        or set(proof) != {"version", "owner", "workflow_id", "source_id", "evidence"}
        or type(proof["version"]) is not int
        or proof["version"] != 1
        or not isinstance(proof["owner"], str)
        or not proof["owner"]
        or not isinstance(proof["workflow_id"], str)
        or not proof["workflow_id"]
        or proof["source_id"] != source.get("source_id")
        or not isinstance(proof["evidence"], dict)
        or not 1 <= len(proof["evidence"]) <= 128
    ):
        raise ValueError("invalid no-model accounting proof")
    for name, fingerprint in proof["evidence"].items():
        if (
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        ):
            raise ValueError("invalid no-model accounting evidence")
    return (
        not source.get("records")
        and source.get("legacy_cost") in (None, 0)
        and source.get("legacy_residual", 0) == 0
        and not source.get("gap")
        and not source.get("read_error")
    )


def _execution_context(issue_dir):
    try:
        board = read_accounting_file(issue_dir / "blackboard.json")
        workflow_id = board["workflow_id"]
        binding = read_accounting_file(issue_dir / "audit_events/workflow.json", max_bytes=4096)
        next_sequence = binding.get("next_sequence")
        if (
            binding.get("workflow_id") != workflow_id
            or binding.get("version") != 1
            or type(next_sequence) is not int
            or not 1 <= next_sequence <= 4097
        ):
            return None
        budget, events = 4 * 1024 * 1024, []
        for sequence in range(1, next_sequence):
            event, _, size = read_accounting_file(
                issue_dir / f"audit_events/events/{sequence:010d}.json",
                max_bytes=min(budget, 256 * 1024),
                with_fingerprint=True,
            )
            budget -= size
            if (
                budget <= 0
                or event.get("workflow_id") != workflow_id
                or event.get("sequence") != sequence
                or not isinstance(event.get("data"), dict)
            ):
                return None
            events.append(event)
        return workflow_id, events
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        return None


def _uninvoked_proof(issue_dir, identity, data, stats, context):
    from cafe.core.packet_io import canonical_json

    if context is None or data.get("agent_invoked") is not False:
        return None
    workflow_id, events = context
    step, directory = identity.split("/")
    iteration = int(directory.removeprefix("iteration_"))
    marker = data.get("agent_interruption", {})
    if (
        not isinstance(marker, dict)
        or marker.get("workflow_id") != workflow_id
        or marker.get("step") != step
        or marker.get("iteration") != iteration
        or data.get("workflow_completion_trusted") is not False
        or stats.get("cost_records")
        or any(
            value
            for key, value in stats.items()
            if key in {*COUNTERS, "cache_creation_input_tokens", "total_cost_usd"}
        )
    ):
        return None
    metadata_name = (
        "iteration.json" if (issue_dir / identity / "iteration.json").exists() else "context.json"
    )
    _, fingerprint, _ = read_accounting_file(
        issue_dir / identity / metadata_name, with_fingerprint=True
    )
    matching = [
        event
        for event in events
        if event.get("event_type") == "agent_interruption_prepared"
        and event.get("step") == step
        and event.get("data", {}).get("iteration") == iteration
        and event["data"].get("marker") == marker
        and event["data"].get("context_target_sha256") == fingerprint
    ]
    if not matching:
        return None
    event = matching[-1]
    paths = [
        "blackboard.json",
        f"{identity}/{metadata_name}",
        f"audit_events/events/{event['sequence']:010d}.json",
    ]
    return dict(
        version=1,
        owner="phase",
        workflow_id=workflow_id,
        source_id=identity,
        evidence={
            name: sha256(canonical_json(read_accounting_file(issue_dir / name))).hexdigest()
            for name in paths
        },
    )


def read_accounting_file(path: Path, *, with_fingerprint=False, max_bytes=MAX_SOURCE_BYTES):
    """Bounded no-follow read; reject unsafe ancestors and special files."""
    path = Path(os.path.abspath(path))
    with _usage_parent(path) as (parent_fd, _):
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            limit = min(max_bytes, MAX_SOURCE_BYTES)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("accounting source is unsafe or oversized")
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("accounting source is oversized")
    value = yaml.safe_load(data) if path.suffix == ".yaml" else json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("accounting source must be an object")
    return (value, sha256(data).hexdigest(), len(data)) if with_fingerprint else value


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
    residual = source_remainder(stats, records)
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
                legacy_residual=float(source_remainder(stats, records).get("total_cost_usd", 0)),
            )["counts"]["legacy"]
            and any(unrecorded_usage(stats, records).values())
        )
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        return True


def collect_cost_sources(issue_dir: Path) -> list[dict]:
    """Keep source identities stable across active/archive/snapshot copies."""
    sources = []
    execution = _execution_context(issue_dir)

    def add(identity, stats, records=None, gap=False):
        stats = stats if isinstance(stats, dict) else {}
        records = records if records is not None else stats.get("cost_records", [])
        sources.append(
            dict(
                source_id=identity,
                records=records,
                legacy_cost=stats.get("total_cost_usd"),
                legacy_residual=float(source_remainder(stats, records).get("total_cost_usd", 0)),
                gap=gap or _coverage_gap(stats, records),
            )
        )
        return sources[-1]

    def chats(identity, groups):
        if not isinstance(groups, list):
            raise ValueError("invalid chat accounting")
        for index, group in enumerate(groups):
            add(
                f"{identity}/chat/{index}",
                group.get("stats"),
                group.get("cost_records", []),
                chat_usage_has_gap(group),
            )

    candidates = sorted(issue_dir.glob("*/iteration_*"))
    for directory in candidates:
        identity = directory.relative_to(issue_dir).as_posix()
        path = directory / "iteration.json"
        if not path.exists() and not path.is_symlink():
            path = directory / "context.json"
        phase_source = None
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
                phase_source = add(identity, stats)
                proof = _uninvoked_proof(issue_dir, identity, data, stats, execution)
                if proof is not None:
                    phase_source.update(no_model_execution=proof, gap=False)
            chats(identity, groups)
        except (OSError, ValueError, TypeError, AttributeError, InvalidOperation, yaml.YAMLError):
            if phase_source is None:
                phase_source = add(identity, {}, gap=True)
            phase_source.update(gap=True, read_error=path.exists() or path.is_symlink())
            if not phase_source["read_error"] and execution is not None:
                from cafe.delivery.accounting import no_model_delivery_proof

                proof = no_model_delivery_proof(issue_dir, identity, *execution)
                if proof is not None:
                    phase_source.update(no_model_execution=proof, gap=False)
    path = issue_dir / "issue.yaml"
    if path.exists() or path.is_symlink():
        try:
            chats("issue", read_accounting_file(path).get("chat_usage", []))
        except (OSError, ValueError, TypeError, AttributeError, InvalidOperation, yaml.YAMLError):
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
                legacy_residual=(
                    source.get("legacy_residual")
                    if source.get("legacy_residual") is not None
                    else source_remainder(
                        dict(
                            total_cost_usd=source.get("legacy_cost"),
                            scalar_coverage=source.get("scalar_coverage"),
                        ),
                        valid,
                    ).get("total_cost_usd")
                ),
            )
            for record in valid:
                # Source scalar coverage is not part of the physical invocation identity.
                record = {k: v for k, v in record.items() if k != "scalar_coverage"}
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
            no_model = confirmed_no_model_source(source) and identity not in ambiguous
            if no_model:
                parts.append(combine_cost_summaries([]))
            elif source.get("gap") or identity in ambiguous or (not raw and not residual):
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
    paths.extend([issue_dir / "blackboard.json", issue_dir / "human_tasks.json",
                  issue_dir / "audit_events/workflow.json"])
    paths.extend(issue_dir.glob("audit_events/events/*.json"))
    paths.extend(issue_dir.glob("delivery/*/*.json"))
    versions = {}
    for path in paths:
        try:
            info = path.lstat()
            versions[str(path)] = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            versions[str(path)] = None
    return versions


def format_native_usage(view, *, templates=None):
    """Neutral persisted report lines; callers own localized presentation."""
    templates = templates or {
        "child": (
            "Child {session} (parent {parent}, model {model}): {categories}; "
            "{provenance}; {source}; {start}..{end}; inclusion={inclusion}; coverage={coverage}"
        ),
        "child_subtotal": "Child known subtotal: {tokens}",
        "caller_subtotal": "Caller known subtotal: {tokens}; {coverage}",
        "unknown": "unknown",
        "complete": "complete",
        "partial": "partial",
        "combined_unavailable": "combined total unavailable",
    }
    lines = []
    for child in view["children"]:
        native = child["native_usage"]
        usage = child["usage"]
        categories = ", ".join(f"{key}={usage.get(key, templates['unknown'])}" for key in COUNTERS)
        lines.append(
            templates["child"].format(
                session=child["session_id"],
                parent=native["parent_session_id"],
                model=child.get("model") or templates["unknown"],
                categories=categories,
                provenance=child["provenance"],
                source=native["source"]["kind"],
                start=native["start"].get("at", templates["unknown"]),
                end=native.get("ownership_cutoff", native["end"].get("at", templates["unknown"])),
                inclusion=native["inclusion"],
                coverage=templates["complete"] if child["complete"] else templates["partial"],
            )
        )
    if view["children"] or view["gaps"]:
        lines.append(templates["child_subtotal"].format(tokens=view["child_tokens"]))
        lines.append(
            templates["caller_subtotal"].format(
                tokens=view["caller_tokens"],
                coverage=(
                    templates["complete"] if view["complete"] else templates["combined_unavailable"]
                ),
            )
        )
    return lines
