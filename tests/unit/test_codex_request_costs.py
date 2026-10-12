"""Native request coverage, context prices and exact resumed invocation ownership."""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cafe.agents.cli.codex_usage import prepare_request_usage
from cafe.core.cost import account_cost, summarize_cost
from cafe.core.types import TokenUsage
from tests.unit.test_cost_accounting import state


SESSION = "01a10a7d-c447-7032-a913-d78a3919d35e"


def counters(n, cached=0):
    return dict(input_tokens=n, output_tokens=10, cached_input_tokens=cached,
                cache_write_input_tokens=0, reasoning_output_tokens=3)


def write(path, row):
    with path.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def metadata(path):
    write(path, dict(type="session_meta", payload=dict(
        id=SESSION, cli_version="0.159.3", source="exec",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )))


def request(path, total, last, *, duplicate=False):
    row = dict(type="event_msg", payload=dict(type="token_count", info=dict(
        total_token_usage=total, last_token_usage=last,
    )))
    write(path, row)
    if duplicate:
        write(path, row)


def project(tmp_path, *, resumed=False, duplicate=False):
    journal = tmp_path / "sessions" / f"rollout-test-{SESSION}.jsonl"
    journal.parent.mkdir()
    prior = counters(1_000_000, 900_000) if resumed else None
    if resumed:
        metadata(journal)
        request(journal, prior, prior)
    command = ["codex", "exec"] + (["resume", SESSION] if resumed else [])
    observe = prepare_request_usage(command, {"CODEX_HOME": str(tmp_path)},
                                    selected_session=SESSION if resumed else None)
    if not resumed:
        metadata(journal)
    write(journal, dict(type="turn_context", payload=dict(model="gpt-test")))
    total = dict(prior or {key: 0 for key in counters(0)})
    for n in (180_000, 180_000):
        last = counters(n, 100_000)
        total = {key: total[key] + last[key] for key in total}
        request(journal, total, last, duplicate=duplicate)
    lines = [json.dumps(dict(type="thread.started", thread_id=SESSION))]
    usage = TokenUsage(**{("cache_read_input_tokens" if key == "cached_input_tokens" else key): val
                          for key, val in total.items()})
    return observe, usage, lines, journal


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("duplicate", [False, True])
def test_short_requests_above_aggregate_threshold_are_priced_once(tmp_path, resumed, duplicate):
    observe, usage, lines, _ = project(tmp_path, resumed=resumed, duplicate=duplicate)
    result = account_cost(observe(usage, lines), cli="codex", model="gpt-test",
                          state=state(), invocation_id="physical")
    assert result.input_tokens == 360_000
    assert result.cache_read_input_tokens == 200_000
    record, = result.cost_records
    assert record["context_band"] == "short"
    assert len(record["request_estimates"]) == 2
    # (160000 * $2 + 200000 * $.2 + 20 * $10) / 1M.
    assert Decimal(record["amount_usd"]) == Decimal("0.3602")
    assert summarize_cost(result.cost_records)["estimated"] == Decimal("0.3602")
    assert account_cost(result, cli="codex", model="gpt-test", state=state(),
                        invocation_id="physical") is result


@pytest.mark.parametrize("damage", ["missing_request", "wrong_last", "model_missing",
                                       "unsupported", "truncated", "oversized", "alias"])
def test_incomplete_native_request_evidence_does_not_invent_a_context_band(
    tmp_path, monkeypatch, damage
):
    observe, usage, lines, journal = project(tmp_path)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    if damage == "missing_request":
        rows.pop(2)
    elif damage == "wrong_last":
        rows[-1]["payload"]["info"]["last_token_usage"]["input_tokens"] -= 1
    elif damage == "model_missing":
        rows.pop(1)
    elif damage == "unsupported":
        rows[0]["payload"]["cli_version"] = "unknown"
    elif damage == "alias":
        rows[-1]["payload"]["info"]["last_token_usage"]["cache_read_input_tokens"] = 1
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if damage == "truncated":
        with journal.open("a") as handle:
            handle.write('{"broken":')
    elif damage == "oversized":
        monkeypatch.setattr("cafe.agents.cli.codex_usage._REQUEST_BYTES", 10)
    observed = observe(usage, lines)
    assert observed is usage
    record, = account_cost(observed, cli="codex", model="gpt-test", state=state()).cost_records
    assert record["reason"] == "context_tier_unavailable"
    assert record["amount_usd"] is None


@pytest.mark.parametrize("damage", ["model", "sum", "negative", "boolean", "missing"])
def test_cost_boundary_revalidates_request_proof(tmp_path, damage):
    observe, usage, lines, _ = project(tmp_path)
    observed = observe(usage, lines)
    requests = copy.deepcopy(observed.turn_usages)
    if damage == "model":
        requests[0]["model"] = "gpt-test-codex"
    elif damage == "sum":
        requests[0]["usage"]["input_tokens"] += 1
    elif damage in {"negative", "boolean"}:
        requests[0]["usage"]["input_tokens"] = -1 if damage == "negative" else True
    else:
        del requests[0]["usage"]["cache_read_input_tokens"]
    record, = account_cost(observed.model_copy(update={"turn_usages": requests}), cli="codex",
                          model="gpt-test", state=state()).cost_records
    assert record["amount_usd"] is None
    assert record["reason"] == "request_usage_unavailable"


def test_short_and_long_requests_use_their_own_rates(tmp_path):
    observe, usage, lines, _ = project(tmp_path)
    observed = observe(usage, lines)
    requests = copy.deepcopy(observed.turn_usages)
    requests[1]["usage"]["input_tokens"] = 300_000
    mixed = observed.model_copy(update={"input_tokens": 480_000, "turn_usages": requests})
    record, = account_cost(mixed, cli="codex", model="gpt-test", state=state()).cost_records
    assert record["context_band"] == "mixed"
    assert [item["context_band"] for item in record["request_estimates"]] == ["short", "long"]
    assert Decimal(record["amount_usd"]) == Decimal("1.02025")


def test_explicit_provider_cost_still_takes_precedence(tmp_path):
    observe, usage, lines, _ = project(tmp_path)
    observed = observe(usage, lines).model_copy(update={"total_cost_usd": 0.0})
    record, = account_cost(observed, cli="codex", model="gpt-test", state=state()).cost_records
    assert record["provenance"] == "reported" and record["amount_usd"] == "0.0"
    assert "request_estimates" not in record
