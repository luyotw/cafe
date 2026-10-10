"""Invocation cost provenance and deterministic API-equivalent estimates."""

from __future__ import annotations

import copy
import math
import uuid
from decimal import Decimal
from datetime import datetime, timezone
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


def merge_cost_records(existing, incoming):
    """Keep each invocation once, including records carried by chat aggregates."""
    from cafe.core.native_accounting import merge_native_records

    return merge_native_records(existing, incoming)


def summarize_cost(records, *, legacy_cost=None):
    """Read persisted calculations; never consult a mutable rate source."""
    totals = {"reported": Decimal(0), "estimated": Decimal(0), "legacy": Decimal(0)}
    counts = dict.fromkeys(totals, 0)
    unknown = 0
    incomplete = False
    stale = False
    from cafe.core.native_accounting import native_projection

    records = merge_cost_records([], records)
    native = native_projection(records) if any("native_usage" in r for r in records) else None
    excluded = set(native["excluded_ids"]) if native else set()
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
    if records and legacy_cost is not None:
        # Older iterations/chat groups can be updated in place. Their numeric
        # subtotal includes both old spend and the new records; retain only the
        # unrepresented remainder, without counting new invocations twice.
        aggregate = Decimal(str(legacy_cost))
        if not aggregate.is_finite() or aggregate < 0:
            raise ValueError("Invalid legacy cost")
        remainder = aggregate - sum(totals.values())
        rounding = Decimal(0)
        if isinstance(legacy_cost, float):
            rounding = Decimal(str(math.ulp(legacy_cost))) * max(1, sum(counts.values())) * 2
        if remainder > rounding:
            totals["legacy"] = remainder
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
        **({"native_usage": native, "_native_records": records} if native is not None else {}),
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
        if summary["counts"][kind]:
            parts.append(f"${summary[kind]:.4f} {kind}")
    if not parts:
        return "unknown" if summary["unknown"] else "$0.0000 estimated"
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
    native_records = []
    for summary in summaries:
        native_records = merge_cost_records(native_records, summary.get("_native_records", []))
        for key in ("reported", "estimated", "legacy", "known", "unknown"):
            result[key] += summary[key]
        for key in ("incomplete", "stale"):
            result[key] = result[key] or summary[key]
        for key in result["counts"]:
            result["counts"][key] += summary["counts"][key]
    if native_records:
        from cafe.core.native_accounting import native_projection

        result["native_usage"] = native_projection(native_records)
        result["_native_records"] = native_records
    return result
