"""Invocation cost provenance and deterministic API-equivalent estimates."""

from __future__ import annotations

import copy
import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cafe.core.pricing_sources import model_id, pricing_store
from cafe.core.types import TokenUsage


def prepare_cost_accounting(cli, environment):
    """Pin rates before launch; refresh failures must not stop execution."""
    state = None
    rate_cli = "cursor" if cli == "cursor-agent" else cli
    if rate_cli in {"codex", "copilot", "cursor", "gemini"}:
        store = None
        try:
            provider = "openai" if rate_cli == "codex" else rate_cli
            cache = environment.get("CAFE_PRICING_CACHE_DIR")
            if cache and provider != "openai":
                cache = Path(cache) / provider
            store = pricing_store(provider, cache)
            # The invocation uses an already available card. HTTP runs in a
            # daemon worker and cannot delay launching or finishing the agent.
            state = store.status()
            if environment.get("CAFE_PRICING_AUTO_UPDATE", "1") != "0":
                store.refresh_in_background()
        except Exception:
            # This boundary includes constructing/reading the store, not just
            # HTTP. Optional rate updates must never prevent an agent launch.
            try:
                state = store.status() if store is not None else None
                if state is not None:
                    state["stale"] = True
            except Exception:
                state = None
    invocation_id = str(uuid.uuid4())
    valuation_date = datetime.now(timezone.utc).date().isoformat()

    def record(usage, model, *, complete=True):
        return account_cost(
            usage,
            cli=cli,
            model=model,
            state=state,
            invocation_id=invocation_id,
            complete=complete,
            valuation_date=valuation_date,
        )

    record.native_state = state
    record.invocation_id = invocation_id
    return record


def account_cost(
    usage: TokenUsage,
    *,
    cli,
    model,
    state=None,
    invocation_id=None,
    complete=True,
    valuation_date=None,
) -> TokenUsage:
    """Keep reported money separate from estimates and absent telemetry.

    Codex input includes cached tokens; reasoning is a subset of output.
    Unknown counters and model aliases never become zero-priced usage.
    """
    invocation_id = invocation_id or str(uuid.uuid4())
    if any(
        record.get("invocation_id") == invocation_id
        or record.get("parent_invocation_id") == invocation_id
        for record in usage.cost_records
    ):
        return usage
    if (
        cli in {"copilot", "gemini"}
        and usage.turn_usages
        and "total_cost_usd" not in usage.model_fields_set
    ):
        return _account_models(
            usage,
            cli=cli,
            state=state,
            invocation_id=invocation_id,
            complete=complete,
            valuation_date=valuation_date,
        )
    result = usage.model_copy(deep=True)
    known = usage.model_dump(exclude_unset=True)
    record = {
        "invocation_id": invocation_id,
        "cli": cli,
        "model": model,
        "currency": "USD",
        "provenance": "unavailable",
        "amount_usd": None,
        "complete": complete,
        "reason": "provider_cost_absent",
        "usage": {
            key: value
            for key, value in known.items()
            if key not in {"turn_usages", "cost_records", "total_cost_usd"}
        },
    }
    if "total_cost_usd" in known:
        value = known["total_cost_usd"]
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid provider-reported cost")
        record.update(provenance="reported", amount_usd=str(Decimal(str(value))), reason=None)
    elif cli in {"codex", "copilot", "cursor", "cursor-agent", "gemini"}:
        if cli == "gemini" and "reasoning_output_tokens" not in known:
            record["reason"] = "reasoning_usage_unavailable"
        else:
            _estimate_tokens(record, known, state, valuation_date)
        if record["amount_usd"] is not None:
            result.total_cost_usd = float(Decimal(record["amount_usd"]))
    result.cost_records = [*result.cost_records, record]
    return result


def _account_models(usage, *, cli, state, invocation_id, complete, valuation_date):
    result = usage.model_copy(deep=True)
    records = []
    for index, turn in enumerate(usage.turn_usages):
        counters = dict(turn.get("usage", {}))
        if index == 0:
            for key in ("duration_ms", "duration_api_ms"):
                if key in usage.model_fields_set:
                    counters[key] = getattr(usage, key)
        item = account_cost(
            TokenUsage(**counters),
            cli=cli,
            model=turn.get("model"),
            state=state,
            invocation_id=f"{invocation_id}:{index}",
            complete=complete,
            valuation_date=valuation_date,
        )
        record = item.cost_records[0]
        record.update(
            parent_invocation_id=invocation_id,
            model_source=turn.get("model_source", "provider_stats"),
        )
        if turn.get("unavailable_reason"):
            record.update(
                provenance="unavailable", amount_usd=None, reason=turn["unavailable_reason"]
            )
        elif cli == "copilot" and "reported_nano_aiu" in turn:
            native = turn["reported_nano_aiu"]
            if type(native) is not int or native < 0:
                raise ValueError("Invalid Copilot native billing")
            record.update(
                provenance="reported",
                amount_usd=str(Decimal(native) / Decimal(100_000_000_000)),
                reason=None,
                billing={
                    "unit": "nano_aiu",
                    "amount": str(native),
                    "nano_aiu_per_credit": "1000000000",
                    "usd_per_credit": "0.01",
                    "conversion_version": 1,
                    "source_url": "https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing",
                },
            )
            for key in (
                "pricing",
                "rates_usd_per_million_tokens",
                "billed_tokens",
                "pricing_stale",
                "assumptions",
                "calculation_version",
                "service_tier",
                "context_band",
                "short_context_limit",
                "rate_model",
                "valuation_date",
            ):
                record.pop(key, None)
        if "candidate_output_tokens" in turn:
            record["candidate_output_tokens"] = turn["candidate_output_tokens"]
        records.append(record)
    result.cost_records = [*result.cost_records, *records]
    amounts = [
        Decimal(record["amount_usd"]) for record in records if record["amount_usd"] is not None
    ]
    if amounts:
        result.total_cost_usd = float(sum(amounts))
    return result


def _estimate_tokens(record, known, state, valuation_date=None):
    model = record["model"]
    if not model or model.lower() == "auto":
        record["reason"] = "model_unavailable"
        return
    if state is None:
        record["reason"] = "pricing_unavailable"
        return
    snapshot = state["snapshot"]
    matched_model = model if record["cli"] == "codex" else model_id(model)
    rate = snapshot["data"]["rates"].get(matched_model, {}).get("standard")
    if rate is None:
        record["reason"] = "unknown_model_or_rate"
        return
    if rate.get("context_unverified") or rate.get("terms_unverified"):
        record["reason"] = "pricing_terms_unavailable"
        return
    required = {"input_tokens", "output_tokens", "cache_read_input_tokens"}
    if not required.issubset(known):
        record["reason"] = "insufficient_token_usage"
        return
    for field in required | {
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_output_tokens",
    }:
        if field in known and (type(known[field]) is not int or known[field] < 0):
            raise ValueError("Invalid provider token counter")
    limit = rate["short_context_limit"]
    # Invocation totals can contain many API calls. A total below the boundary
    # proves each call is short; a total above it does not prove long context.
    context_input = known["input_tokens"]
    if record["cli"] in {"cursor", "cursor-agent"}:
        context_input += known["cache_read_input_tokens"] + known.get("cache_write_input_tokens", 0)
    if limit is not None and context_input > limit:
        record["reason"] = "context_tier_unavailable"
        return
    prices = copy.deepcopy(rate["usd_per_million_tokens"]["short"])
    valuation_date = valuation_date or datetime.now(timezone.utc).date().isoformat()
    if rate.get("valid_through") and valuation_date > rate["valid_through"]:
        record["reason"] = "expired_rate_unavailable"
        return
    for category, schedule in rate.get("scheduled_prices", {}).items():
        effective = [
            item["price"]
            for item in schedule
            if ("through" in item and valuation_date <= item["through"])
            or ("starting" in item and valuation_date >= item["starting"])
        ]
        if len(effective) != 1:
            record["reason"] = "effective_rate_unavailable"
            return
        prices[category] = effective[0]
    write = known.get("cache_write_input_tokens", known.get("cache_creation_input_tokens", 0))
    creation = known.get("cache_creation_input_tokens", 0)
    if creation and write != creation:
        record["reason"] = "ambiguous_cache_write_counters"
        return
    if prices.get("cache_write") is not None and not (
        {"cache_write_input_tokens", "cache_creation_input_tokens"} & known.keys()
    ):
        record["reason"] = "cache_write_usage_unavailable"
        return
    read = known["cache_read_input_tokens"]
    uncached = (
        known["input_tokens"]
        if record["cli"] in {"cursor", "cursor-agent"}
        else known["input_tokens"] - read - write
    )
    if uncached < 0 or known.get("reasoning_output_tokens", 0) > known["output_tokens"]:
        record["reason"] = "overlapping_token_counters"
        return
    categories = {
        "input": uncached,
        "cached_input": read,
        "cache_write": write,
        "output": known["output_tokens"],
    }
    amount = Decimal(0)
    for category, count in categories.items():
        price = prices.get(category)
        if count and price is None:
            record["reason"] = "required_rate_unavailable"
            return
        amount += Decimal(price or "0") * count / Decimal(1_000_000)
    record.update(
        provenance="estimated",
        amount_usd=str(amount),
        reason=None,
        pricing={
            key: copy.deepcopy(snapshot.get(key))
            for key in ("version", "source_url", "fetched_at", "last_modified", "etag")
        },
        rates_usd_per_million_tokens=copy.deepcopy(prices),
        billed_tokens=categories,
        calculation_version=1,
        pricing_stale=bool(state.get("stale")),
        service_tier="standard",
        context_band="short",
        short_context_limit=limit,
        valuation_date=valuation_date,
        rate_model=matched_model,
        assumptions=[
            "API-equivalent standard global pricing; excludes subscription billing, tools, taxes and discounts",
            (
                "Cursor CLI input excludes cache reads/writes; each category is charged once"
                if record["cli"] in {"cursor", "cursor-agent"}
                else "Input includes cache reads/writes; reasoning is included in output"
            ),
            (
                "Short context verified by invocation token upper bound"
                if limit
                else "Single published context rate"
            ),
        ],
    )


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


def normalized_counters(raw, *, inclusive=True):
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
    if not inclusive:
        return values, gaps
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


def _validate_native(record):
    native = record["native_usage"]
    if (
        not isinstance(native, dict)
        or native.get("version") != 1
        or type(native.get("version")) is not int
    ):
        raise ValueError("unsupported native version")
    if native.get("kind") not in {"scope", "child"} or native.get("status") not in {
        "open",
        "progress",  # Read-only compatibility; legacy child checkpoints are immutable too.
        "partial",
        "final",
    }:
        raise ValueError("unsupported native observation")
    if native.get("inclusion") not in {"exclusive", "inclusive", "unknown"}:
        raise ValueError("unsupported native inclusion")
    for key, limit in (("segment_id", 128), ("caller_id", 512), ("attempt_id", 512)):
        if not isinstance(native.get(key), str) or not 0 < len(native[key]) <= limit:
            raise ValueError("invalid native identity")
    for key in ("session_id", "root_session_id", "parent_session_id", "workflow_id"):
        value = native.get(key)
        if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 512):
            raise ValueError("invalid native attribution")
    if native["kind"] == "child" and not all(
        native.get(k) for k in ("session_id", "root_session_id")
    ):
        raise ValueError("missing native identity")
    if (
        not isinstance(native.get("source"), dict)
        or not isinstance(native.get("gaps"), list)
        or any(not isinstance(g, str) for g in native["gaps"])
    ):
        raise ValueError("invalid native proof")
    boundaries = []
    for key in ("start", "end"):
        boundary = native.get(key)
        if (
            not isinstance(boundary, dict)
            or type(boundary.get("offset", 0)) is not int
            or boundary.get("offset", 0) < 0
        ):
            raise ValueError("invalid native boundary")
        counters, gaps = normalized_counters(boundary.get("counters", {}))
        if gaps:
            raise ValueError("invalid persisted native counters")
        boundaries.append(counters)
    usage, gaps = normalized_counters(record.get("usage", {}))
    if gaps:
        raise ValueError("invalid persisted native usage")
    if native["kind"] == "child":
        start, end = boundaries
        delta = {
            k: end[k] - start[k] for k in COUNTERS if k in end and k in start and end[k] >= start[k]
        }
        if "total_tokens" not in delta and {"input_tokens", "output_tokens"} <= delta.keys():
            delta["total_tokens"] = delta["input_tokens"] + delta["output_tokens"]
        if any(delta.get(k) != v for k, v in usage.items()):
            raise ValueError("persisted usage does not match interval endpoints")
    return native


def _scope_refines(old, new):
    a, b = old["native_usage"], new["native_usage"]
    if a["kind"] != "scope" or b["kind"] != "scope" or a["status"] != "open":
        return False
    fixed = (
        "segment_id",
        "caller_id",
        "attempt_id",
        "workflow_id",
        "start",
        "inclusion",
        "parent_session_id",
    )
    return (
        all(a.get(k) == b.get(k) for k in fixed)
        and all(a.get(k) is None or a[k] == b.get(k) for k in ("session_id", "root_session_id"))
        and b["end"].get("offset", 0) >= a["end"].get("offset", 0)
        and b["end"].get("at", "") >= a["end"].get("at", "")
    )


def merge_cost_records(existing, incoming):
    """Merge evidence first; scope checkpoints refine, child cutoffs are immutable."""
    records = {}
    for original in [*(existing or []), *(incoming or [])]:
        if not isinstance(original, dict) or not original.get("invocation_id"):
            continue
        row = copy.deepcopy(original)
        native = _validate_native(row) if "native_usage" in row else None
        key = ("native:" + native["segment_id"]) if native else row["invocation_id"]
        old = records.get(key)
        if old is None:
            records[key] = row
        elif native and "native_usage" in old and old != row:
            prior = old["native_usage"]
            if prior.get("conflicts"):
                continue
            evidence = ("usage", "model", "amount_usd", "provenance", "complete")
            proof = (
                "kind",
                "session_id",
                "root_session_id",
                "parent_session_id",
                "workflow_id",
                "caller_id",
                "attempt_id",
                "start",
                "end",
                "inclusion",
                "gaps",
                "status",
            )
            if all(old.get(k) == row.get(k) for k in evidence) and all(
                prior.get(k) == native.get(k) for k in proof
            ):
                continue
            if _scope_refines(old, row):
                records[key] = row
            elif _scope_refines(row, old):
                continue
            else:
                prior["gaps"] = sorted(set(prior["gaps"] + ["conflicting_observations"]))
                prior["conflicts"] = [
                    dict(
                        end=r["native_usage"]["end"],
                        usage=r["usage"],
                        model=r.get("model"),
                        amount_usd=r.get("amount_usd"),
                    )
                    for r in (old, row)
                ]
                old.update(
                    complete=False,
                    amount_usd=None,
                    provenance="unavailable",
                    reason="conflicting_observations",
                    usage={},
                )
    return list(records.values())


def _row_counters(row, *, observations=False):
    proof = row.get("token_total_evidence", {})
    inclusive = not observations and (
        bool(row.get("native_usage"))
        or isinstance(proof, dict)
        and proof.get("kind") == "input_plus_output"
        and bool(proof.get("source"))
    )
    return normalized_counters(row.get("usage", {}), inclusive=inclusive)


def _token_totals(rows):
    values = [_row_counters(row)[0] for row in rows]
    return {
        key: sum(v[key] for v in values if key in v)
        for key in COUNTERS
        if any(key in v for v in values)
    }


def accounting_admission(records):
    """One joint physical admission after source-local legacy reduction."""
    records = merge_cost_records([], records)
    children = [r for r in records if r.get("native_usage", {}).get("kind") == "child"]
    excluded, overlap, included, gaps = set(), set(), set(), []
    for index, child in enumerate(children):
        native = child["native_usage"]
        gaps.extend(native["gaps"] or ([] if child.get("complete", True) else ["child_partial"]))
        if native.get("conflicts"):
            excluded.add(child["invocation_id"])
        if native["inclusion"] != "exclusive":
            excluded.add(child["invocation_id"])
            exact = False
            for parent in records:
                proof = parent.get("native_inclusion", {})
                expected = {k: native[k] for k in ("segment_id", "session_id", "start", "end")}
                values, invalid = _row_counters(parent)
                if (
                    native["inclusion"] == "inclusive"
                    and isinstance(proof, dict)
                    and proof.get("kind") == "exact_inclusive"
                    and proof.get("source")
                    and parent.get("session_id") == native["root_session_id"]
                    and expected in proof.get("segments", [])
                    and not invalid
                    and all(values.get(k, -1) >= v for k, v in child["usage"].items())
                ):
                    exact = True
            if exact:
                included.add(child["invocation_id"])
            else:
                gaps.append("parent_overlap_unreconciled")
        for other in children[:index]:
            b = other["native_usage"]
            if native["session_id"] == b["session_id"] and max(
                native["start"].get("offset", 0), b["start"].get("offset", 0)
            ) < min(native["end"].get("offset", 0), b["end"].get("offset", 0)):
                overlap.update((child["invocation_id"], other["invocation_id"]))
                excluded.update(overlap)
                gaps.append("overlapping_physical_ranges")
    additive = [
        r
        for r in records
        if r["invocation_id"] not in excluded and r.get("native_usage", {}).get("kind") != "scope"
    ]
    for row in records:
        if row.get("native_usage", {}).get("kind") == "scope":
            gaps.extend(row["native_usage"]["gaps"])
    for row in additive:
        values, invalid = _row_counters(row)
        gaps.extend(invalid)
        if "total_tokens" not in values:
            gaps.append("total_tokens_unavailable")
        if "native_usage" not in row and (
            not {"input_tokens", "output_tokens"} <= values.keys() or not row.get("complete", True)
        ):
            gaps.append("caller_token_coverage_incomplete")
    tokens = _token_totals(additive)
    return dict(
        records=records,
        admitted=additive,
        included_ids=included,
        native_usage=dict(
            children=children,
            child_tokens=_token_totals([r for r in children if r["invocation_id"] not in overlap]),
            caller_tokens=_token_totals([r for r in additive if "native_usage" not in r]),
            tokens=tokens,
            combined_tokens=None if gaps else tokens,
            complete=not gaps,
            gaps=sorted(set(gaps)),
            excluded_ids=sorted(excluded),
        ),
    )


def source_remainder(stats, records):
    """Interpret bounded old source proof read-only; new scalars cover callers only."""
    if stats.get("scalar_coverage") not in (None, "caller"):
        raise ValueError("unsupported scalar coverage")
    admission = accounting_admission(records)
    caller_only = stats.get("scalar_coverage") == "caller" or any(
        r.get("scalar_coverage") == "caller"
        or isinstance(r.get("scalar_coverage"), dict)
        and r["scalar_coverage"].get("kind") == "caller"
        for r in records
    )
    rows = []
    for row in admission["records"]:
        if (
            row["invocation_id"] in admission["included_ids"]
            or row.get("native_usage", {}).get("kind") == "scope"
            or caller_only
            and "native_usage" in row
        ):
            continue
        rows.extend(row.get("native_usage", {}).get("conflicts") or [row])
    represented, ambiguous = {}, set()
    for row in rows:
        values, invalid = _row_counters(row, observations=True)
        coverage = row.get("scalar_coverage", {})
        if isinstance(coverage, dict) and coverage and (
            coverage.get("kind") != "caller"
            or not coverage.keys() <= {"kind", "excluded_fields"}
        ):
            raise ValueError("invalid scalar coverage")
        excluded = coverage.get("excluded_fields", []) if isinstance(coverage, dict) else []
        if (
            not isinstance(excluded, list)
            or len(excluded) > len(COUNTERS)
            or any(k not in COUNTERS for k in excluded)
        ):
            raise ValueError("invalid scalar coverage fields")
        values = {k: v for k, v in values.items() if k not in excluded}
        invalid = [
            g
            for g in invalid
            if not any(g in ("invalid_" + k, "conflicting_" + k) for k in excluded)
        ]
        for key, value in values.items():
            represented[key] = represented.get(key, 0) + value
        for gap in invalid:
            ambiguous.update(k for k in COUNTERS if gap in ("invalid_" + k, "conflicting_" + k))
        if row.get("amount_usd") is not None:
            amount = Decimal(str(row["amount_usd"]))
            if not amount.is_finite() or amount < 0:
                raise ValueError("invalid represented cost")
            represented["total_cost_usd"] = represented.get("total_cost_usd", Decimal(0)) + amount
    historical = stats.get("accounting_residual", {})
    fields = (*COUNTERS, "cache_creation_input_tokens", "total_cost_usd")
    if not isinstance(historical, dict) or not historical.keys() <= set(fields):
        raise ValueError("invalid historical remainder proof")
    remaining = {}
    for key in fields:
        canonical = "cache_write_input_tokens" if key == "cache_creation_input_tokens" else key
        for proof, value in ((True, historical.get(key)), (False, stats.get(key))):
            if value is None:
                continue
            if key == "total_cost_usd":
                if type(value) not in (int, float, Decimal, str):
                    raise ValueError("invalid source cost")
                amount = Decimal(str(value))
                if not amount.is_finite() or amount < 0:
                    raise ValueError("invalid source cost")
            elif type(value) is not int or value < 0:
                raise ValueError("invalid source counter")
            else:
                amount = value
            if not proof:
                amount = 0 if canonical in ambiguous else amount - represented.get(canonical, 0)
            tolerance = (
                Decimal(str(math.ulp(value))) * max(1, len(rows)) * 2
                if key == "total_cost_usd" and type(value) is float
                else 0
            )
            # Historical proof is an independent lower bound, not a frozen
            # replacement for subsequent scalar-only legacy observations.
            remaining[key] = max(remaining.get(key, 0), amount if amount > tolerance else 0)
    return remaining


def summarize_cost(records, *, legacy_cost=None, legacy_residual=None):
    """Read persisted calculations; never consult a mutable rate source."""
    totals = {"reported": Decimal(0), "estimated": Decimal(0), "legacy": Decimal(0)}
    counts = dict.fromkeys(totals, 0)
    unknown = 0
    incomplete = False
    stale = False
    admission = accounting_admission(records)
    records = admission["records"]
    native = admission["native_usage"] if any("native_usage" in r for r in records) else None
    excluded = {r["invocation_id"] for r in records} - {
        r["invocation_id"] for r in admission["admitted"]
    }
    incomplete = bool(native and not native["complete"])
    seen = set()
    for record in records or []:
        if record.get("native_usage", {}).get("kind") == "scope":
            if record["native_usage"]["gaps"] or not record.get("complete", True):
                unknown += 1
            # Coverage checkpoints are not additional calls or unavailable prices.
            continue
        if record.get("invocation_id") in excluded:
            continue  # Projection distinguishes exact inclusion from unresolved overlap.
        identity = record.get("invocation_id")
        if identity in seen:
            continue
        seen.add(identity)
        kind, amount = record.get("provenance"), record.get("amount_usd")
        if kind not in {"reported", "estimated"} or amount is None:
            unknown += 1
        else:
            value = Decimal(str(amount))
            if not value.is_finite() or value < 0:
                raise ValueError("Invalid recorded cost")
            totals[kind] += value
            counts[kind] += 1
        incomplete = incomplete or not record.get("complete", True)
        stale = stale or bool(record.get("pricing_stale"))
    if legacy_residual is not None:
        remainder = Decimal(str(legacy_residual))
        if not remainder.is_finite() or remainder < 0:
            raise ValueError("Invalid legacy residual")
        if remainder:
            totals["legacy"] = remainder
            counts["legacy"] += 1
    elif records and legacy_cost is not None:
        # Older iterations/chat groups can be updated in place. Their numeric
        # subtotal includes both old spend and the new records; retain only the
        # unrepresented remainder, without counting new invocations twice.
        remainder = source_remainder({"total_cost_usd": legacy_cost}, records).get(
            "total_cost_usd", 0
        )
        if remainder:
            totals["legacy"] = Decimal(str(remainder))
            counts["legacy"] += 1
    elif not records:
        # Old serializers wrote default zero even when no cost was reported.
        # A nonzero historical amount is retained with unknown provenance.
        if legacy_cost is not None and legacy_cost > 0:
            totals["legacy"] = Decimal(str(legacy_cost))
            counts["legacy"] += 1
        else:
            unknown += 1
    return {
        "_cost_records": records,
        **({"native_usage": native} if native is not None else {}),
        **totals,
        "known": sum(totals.values()),
        "unknown": unknown,
        "incomplete": incomplete or bool(unknown),
        "stale": stale,
        "counts": counts,
    }


def format_cost(summary):
    parts = []
    for kind in ("reported", "estimated", "legacy"):
        if summary["counts"][kind] and (summary["known"] or not summary["incomplete"]):
            parts.append(f"${summary[kind]:.4f} {kind}")
    if not parts:
        return (
            "unknown (partial)" if summary["incomplete"] and not summary["unknown"] else "unknown"
        )
    text = " + ".join(parts)
    if summary["incomplete"]:
        text += " (partial)"
    if summary["stale"]:
        text += " (stale rates)"
    return text


def combine_cost_summaries(summaries):
    result = {key: Decimal(0) for key in ("reported", "estimated", "legacy", "known")}
    result.update(
        unknown=0,
        incomplete=False,
        stale=False,
        counts=dict.fromkeys(("reported", "estimated", "legacy"), 0),
    )
    records = []
    for summary in summaries:
        rows = summary.get("_cost_records", summary.get("_native_records", []))
        # Strip each local represented contribution, then admit the merged evidence.
        local = summarize_cost(rows) if rows else None
        records.extend(rows)
        for key in ("reported", "estimated", "legacy", "known", "unknown"):
            result[key] += summary[key] - (local[key] if local else 0)
        for key in result["counts"]:
            result["counts"][key] += summary["counts"][key] - (local["counts"][key] if local else 0)
        result["incomplete"] |= bool(summary["incomplete"] and not (local and local["incomplete"]))
        result["stale"] |= bool(summary["stale"] and not (local and local["stale"]))
    if records:
        joint = summarize_cost(records)
        for key in ("reported", "estimated", "legacy", "known", "unknown"):
            result[key] += joint[key]
        for key in result["counts"]:
            result["counts"][key] += joint["counts"][key]
        result["incomplete"] |= joint["incomplete"]
        result["stale"] |= joint["stale"]
        result["_cost_records"] = joint["_cost_records"]
        if "native_usage" in joint:
            result["native_usage"] = joint["native_usage"]
    result["incomplete"] |= bool(result["unknown"])
    return result
