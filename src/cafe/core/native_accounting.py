"""Provider-neutral persisted interval evidence and conservative reconciliation.

Counters are known subtotals. A missing category is never an attested zero;
observation copies and cumulative refinements are not additional physical work.
"""

from __future__ import annotations

import copy
import json
import math
from decimal import Decimal
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
ALIASES = {
    "cached_input_tokens": "cache_read_input_tokens",
    "cache_creation_input_tokens": "cache_write_input_tokens",
}


class NativeEvidence(BaseModel):
    """Versioned additive record contract; source-specific proof stays sanitized."""

    model_config = ConfigDict(extra="allow", strict=True)
    version: int = Field(ge=1, le=1)
    segment_id: str = Field(min_length=1, max_length=128)
    kind: str
    session_id: str | None
    root_session_id: str | None
    parent_session_id: str | None = None
    workflow_id: str | None
    caller_id: str = Field(min_length=1, max_length=512)
    attempt_id: str = Field(min_length=1, max_length=512)
    status: str
    inclusion: str
    start: dict
    end: dict
    gaps: list[str]
    source: dict


def normalized_counters(raw):
    values, gaps, rejected = {}, [], set()
    if not isinstance(raw, dict):
        return values, ["counters_unavailable"]
    for key, value in raw.items():
        key = ALIASES.get(key, key)
        if key not in COUNTERS:
            continue
        if type(value) is not int or value < 0:
            gaps.append(f"invalid_{key}")
            rejected.add(key)
            values.pop(key, None)
        elif key in values and values[key] != value:
            gaps.append(f"conflicting_{key}")
            rejected.add(key)
            values.pop(key, None)
        elif key not in rejected:
            values[key] = value
    for subset, whole in (
        ("cache_read_input_tokens", "input_tokens"),
        ("cache_write_input_tokens", "input_tokens"),
        ("reasoning_output_tokens", "output_tokens"),
    ):
        if subset in values and whole in values and values[subset] > values[whole]:
            gaps.append(f"invalid_{subset}")
            values.pop(subset)
    if {"cache_read_input_tokens", "cache_write_input_tokens", "input_tokens"} <= values.keys():
        if (
            values["cache_read_input_tokens"] + values["cache_write_input_tokens"]
            > values["input_tokens"]
        ):
            gaps.append("invalid_cache_subsets")
            values.pop("cache_read_input_tokens")
            values.pop("cache_write_input_tokens")
    if {"input_tokens", "output_tokens", "total_tokens"} <= values.keys():
        if values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
            gaps.append("invalid_total_tokens")
            values.pop("total_tokens")
    return values, gaps


def interval_delta(start, end):
    """Subtract independently valid endpoints; cached/reasoning remain subsets."""
    baseline, gaps = normalized_counters(start)
    current, more = normalized_counters(end)
    gaps += more
    result = {}
    for key in COUNTERS:
        if key not in baseline or key not in current:
            gaps.append(f"unknown_{key}")
        elif current[key] < baseline[key]:
            gaps.append(f"reset_{key}")
        else:
            result[key] = current[key] - baseline[key]
    result, more = normalized_counters(result)
    gaps += more
    if "total_tokens" not in result and {"input_tokens", "output_tokens"} <= result.keys():
        if not any("reset_" in gap or "invalid_total" in gap for gap in gaps):
            result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result, sorted(set(gaps))


def physical_id(session, start):
    """Caller attribution is separate from one verified physical counter range."""
    boundary = (
        start
        if str(session).startswith("scope:")
        else {k: v for k, v in start.items() if k != "at"}
    )
    payload = dict(session=session, start=boundary)
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_native(record):
    native = NativeEvidence.model_validate(record["native_usage"]).model_dump()
    if native["kind"] not in {"scope", "child"} or native["status"] not in {
        "open",
        "progress",
        "partial",
        "final",
    }:
        raise ValueError("unsupported native observation")
    if native["inclusion"] not in {"exclusive", "inclusive", "unknown"}:
        raise ValueError("unsupported native inclusion")
    for boundary in (native["start"], native["end"]):
        offset = boundary.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("invalid native offset")
        _, gaps = normalized_counters(boundary.get("counters", {}))
        if any(gap.startswith(("invalid_", "conflicting_")) for gap in gaps):
            raise ValueError("invalid persisted native counters")
    usage, gaps = normalized_counters(record.get("usage", {}))
    if native["kind"] == "child":
        delta, _ = interval_delta(
            native["start"].get("counters", {}), native["end"].get("counters", {})
        )
        if any(delta.get(k) != v for k, v in usage.items()):
            raise ValueError("persisted usage does not match interval endpoints")
    if gaps:
        raise ValueError("invalid persisted native usage")
    return native


def _refines(old, new):
    a, b = old["native_usage"], new["native_usage"]
    if a["kind"] != b["kind"]:
        return False
    fixed = (
        "segment_id",
        "session_id",
        "root_session_id",
        "parent_session_id",
        "workflow_id",
        "caller_id",
        "start",
        "inclusion",
    )
    if any(
        a.get(key) != b.get(key)
        for key in fixed
        if not (
            a["kind"] == "scope" and key in {"session_id", "root_session_id"} and a.get(key) is None
        )
    ):
        return False
    if b["end"].get("offset", 0) < a["end"].get("offset", 0):
        return False
    x, y = a["end"].get("counters", {}), b["end"].get("counters", {})
    return all(key in y and y[key] >= value for key, value in x.items())


def merge_native_records(existing, incoming):
    """Legacy records keep first-wins; native open endpoints may refine once."""
    records = {}
    for original in [*(existing or []), *(incoming or [])]:
        if not isinstance(original, dict) or not original.get("invocation_id"):
            continue
        record = copy.deepcopy(original)
        key = record["invocation_id"]
        if "native_usage" in record:
            validate_native(record)
            key = "native:" + record["native_usage"]["segment_id"]
        old = records.get(key)
        if old is None:
            records[key] = record
        elif "native_usage" in old and "native_usage" in record and old != record:
            a, b = old["native_usage"], record["native_usage"]
            if a.get("conflicts"):
                continue
            comparison = ("usage", "model", "amount_usd", "provenance", "complete")
            same_evidence = all(old.get(k) == record.get(k) for k in comparison) and all(
                a.get(k) == b.get(k)
                for k in (
                    "session_id",
                    "root_session_id",
                    "parent_session_id",
                    "workflow_id",
                    "caller_id",
                    "start",
                    "end",
                    "inclusion",
                    "gaps",
                    "status",
                )
            )
            if same_evidence:
                continue
            if a["status"] != "final" and _refines(old, record):
                records[key] = record
            elif b["status"] != "final" and _refines(record, old):
                continue  # A stale checkpoint/storage copy does not undo refinement.
            else:
                conflict = copy.deepcopy(old)
                conflict["native_usage"]["gaps"] = sorted(
                    set(a["gaps"] + ["conflicting_observations"])
                )
                # Keep bounded, sanitized endpoint/calculation proof, never transcripts.
                conflict["native_usage"]["conflicts"] = [
                    dict(
                        end=r["native_usage"]["end"],
                        usage=r["usage"],
                        model=r.get("model"),
                        amount_usd=r.get("amount_usd"),
                    )
                    for r in (old, record)
                ]
                conflict.update(
                    complete=False,
                    amount_usd=None,
                    provenance="unavailable",
                    reason="conflicting_observations",
                    usage={},
                )
                records[key] = conflict
    return list(records.values())


def accounting_admission(records):
    """Merge once and distinguish represented observations from admitted work."""
    records = merge_native_records([], records)
    children = [r for r in records if r.get("native_usage", {}).get("kind") == "child"]
    excluded, overlap, included, gaps = set(), set(), set(), []

    def exactly_included(child):
        native = child["native_usage"]
        for parent in records:
            proof = parent.get("native_inclusion", {})
            if (
                not isinstance(proof, dict)
                or proof.get("kind") != "exact_inclusive"
                or not proof.get("source")
                or parent.get("session_id") != native["root_session_id"]
            ):
                continue
            segments = proof.get("segments", [])
            expected = {key: native[key] for key in ("segment_id", "session_id", "start", "end")}
            if expected not in segments:
                continue
            parent_usage, invalid = normalized_counters(parent.get("usage", {}))
            if not invalid and all(parent_usage.get(k, -1) >= v for k, v in child["usage"].items()):
                return True
        return False

    for index, child in enumerate(children):
        native = child["native_usage"]
        if native["gaps"] or not child.get("complete", True):
            gaps.extend(native["gaps"] or ["child_partial"])
        if native.get("conflicts"):
            excluded.add(child["invocation_id"])
        if native["inclusion"] != "exclusive":
            excluded.add(child["invocation_id"])
            # Preserve the parent's amount when exact child boundaries are attested.
            if native["inclusion"] == "inclusive" and exactly_included(child):
                included.add(child["invocation_id"])
            else:
                gaps.append("parent_overlap_unreconciled")
        for other in children[:index]:
            a, b = native, other["native_usage"]
            if a["session_id"] == b["session_id"] and (
                max(a["start"].get("offset", 0), b["start"].get("offset", 0))
                < min(a["end"].get("offset", 0), b["end"].get("offset", 0))
            ):
                excluded.update((child["invocation_id"], other["invocation_id"]))
                overlap.update((child["invocation_id"], other["invocation_id"]))
                gaps.append("overlapping_physical_ranges")

    def total(rows):
        result = {}
        for key in COUNTERS:
            known = []
            for r in rows:
                usage, _ = normalized_counters(r.get("usage", {}))
                value = usage.get(key)
                known.append(value)
            values = [v for v in known if type(v) is int]
            if values:
                result[key] = sum(values)
        return result

    additive = [
        r
        for r in records
        if r["invocation_id"] not in excluded and r.get("native_usage", {}).get("kind") != "scope"
    ]
    scopes = [r for r in records if r.get("native_usage", {}).get("kind") == "scope"]
    gaps.extend(gap for r in scopes for gap in r["native_usage"]["gaps"])
    for r in additive:
        if type(r.get("usage", {}).get("total_tokens")) is not int:
            gaps.append("total_tokens_unavailable")
        if r.get("native_usage", {}).get("kind") != "child" and (
            not {"input_tokens", "output_tokens"} <= r.get("usage", {}).keys()
            or not r.get("complete", True)
        ):
            gaps.append("caller_token_coverage_incomplete")
    tokens = total(additive)
    # A scope checkpoint is evidence of missing coverage, not an extra provider call.
    view = dict(
        children=children,
        child_tokens=total([r for r in children if r["invocation_id"] not in overlap]),
        tokens=tokens,
        combined_tokens=None if gaps else tokens,
        complete=not bool(gaps),
        gaps=sorted(set(gaps)),
        excluded_ids=sorted(excluded),
    )

    represented_rows = []
    for row in records:
        if row.get("native_usage", {}).get("kind") == "scope" or row["invocation_id"] in included:
            continue
        # Conflicting observations remain represented, even when none is admissible.
        observations = row.get("native_usage", {}).get("conflicts") or [row]
        represented_rows.extend(observations)
    represented = total(represented_rows)
    amounts = [
        Decimal(str(r["amount_usd"])) for r in represented_rows if r.get("amount_usd") is not None
    ]
    if amounts:
        if any(not value.is_finite() or value < 0 for value in amounts):
            raise ValueError("Invalid recorded cost")
        represented["total_cost_usd"] = sum(amounts, Decimal(0))
    return dict(records=records, admitted=additive, represented=represented, native_usage=view)


def validate_accounting_residual(value):
    """Validate the bounded, independent remainder of admitted compatibility stats."""
    if not isinstance(value, dict) or not value.keys() <= set(COUNTERS) | {
        "cache_creation_input_tokens",
        "total_cost_usd",
    }:
        raise ValueError("Invalid accounting residual")
    for key, amount in value.items():
        if (
            type(amount) not in (int, float)
            or amount < 0
            or (key != "total_cost_usd" and type(amount) is not int)
        ):
            raise ValueError("Invalid accounting residual")
        try:
            finite = math.isfinite(amount)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("Invalid accounting residual")
    return value


def unrepresented_totals(stats, admission):
    """Retain genuine old components independently of excluded observations."""
    residual = dict(validate_accounting_residual(stats.get("accounting_residual", {})))
    for key in (*COUNTERS, "cache_creation_input_tokens", "total_cost_usd"):
        if key in residual or stats.get(key) is None:
            continue
        canonical = "cache_write_input_tokens" if key == "cache_creation_input_tokens" else key
        represented = admission["represented"].get(canonical, 0)
        if key == "total_cost_usd":
            amount = Decimal(str(stats[key])) - represented
            rounding = Decimal(0)
            if type(stats[key]) is float:
                rounding = (
                    Decimal(str(math.ulp(stats[key]))) * max(1, len(admission["records"])) * 2
                )
            residual[key] = float(amount) if amount > rounding else 0.0
        else:
            residual[key] = max(0, stats[key] - represented)
    return validate_accounting_residual(residual)


def native_projection(records):
    """Read-only child detail and disjoint known subtotals; never reprice."""
    return accounting_admission(records)["native_usage"]


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
                tokens=view["tokens"],
                coverage=(
                    templates["complete"] if view["complete"] else templates["combined_unavailable"]
                ),
            )
        )
    return lines
