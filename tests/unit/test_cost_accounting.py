"""Reproducible money, overlap semantics, unknowns and consistent summaries."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cafe.core.cost import account_cost, format_cost, summarize_cost
from cafe.core.pricing import make_snapshot
from cafe.core.types import TokenUsage
from cafe.core.usage import (
    merge_token_usage_stats, chat_usage_sink, phase_stats_without_chat, iteration_usage_sink,
)
from cafe.agents.transport_types import TransportResult
from cafe.services.status_display import StatusDisplay
from cafe.services.timeline_builder import TimelineEntry
from tests.unit.test_pricing import DOCUMENT


@pytest.mark.parametrize("succeed", [True, False])
def test_session_recovery_retains_each_physical_attempt_cost(succeed):
    from unittest.mock import Mock

    from cafe.agents.executor import AgentExecutionError, AgentExecutor
    from cafe.core.types import AgentCLI, AgentConfig, AgentResponse

    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CODEX, session_id="old"))
    first = estimate(invocation_id="first", complete=False)
    second = estimate(invocation_id="second", complete=succeed)
    failed = AgentExecutionError("session not found")
    failed.accounting_usage = first
    if succeed:
        final = AgentResponse(response="done", token_usage=second)
    else:
        final = AgentExecutionError("provider failed")
        final.accounting_usage = second
    invoke = Mock(side_effect=[failed, final])
    kwargs = dict(
        cmd=["codex", "resume", "old"],
        cli_name="Codex",
        create_new_session_fn=Mock(),
        update_cmd_with_session_fn=Mock(),
        invoke_attempt=invoke,
    )
    if succeed:
        usage = executor._execute_with_session_recovery(**kwargs).token_usage
    else:
        with pytest.raises(AgentExecutionError) as caught:
            executor._execute_with_session_recovery(**kwargs)
        usage = caught.value.accounting_usage
    assert usage.input_tokens == first.input_tokens + second.input_tokens
    assert [record["invocation_id"] for record in usage.cost_records] == ["first", "second"]
    assert summarize_cost(usage.cost_records)["estimated"] == Decimal("0.000702")
    assert summarize_cost(usage.cost_records)["incomplete"]
    assert invoke.call_count == 2


def test_manager_failed_attempt_accounting_retains_and_deduplicates_cost():
    from unittest.mock import Mock

    from cafe.agents.executor import AgentExecutionError
    from cafe.agents.manager import AgentManager
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentCLI

    manager = AgentManager(session_manager=Mock(spec=SessionManager))
    error = AgentExecutionError("rate limit", error_type="rate_limit")
    error.accounting_usage = estimate(invocation_id="spent", complete=False)
    for attempt in (1, 2):
        manager._record_failed_attempt(
            cli=AgentCLI.CODEX, chain_role="primary", attempt=attempt, error=error
        )
    assert manager._total_token_usage.input_tokens == 100
    assert len(manager._total_token_usage.cost_records) == 1
    assert len(error.accounting_usage.cost_records) == 1
    assert summarize_cost(error.accounting_usage.cost_records)["estimated"] == Decimal("0.000351")


def test_successful_manager_usage_survives_a_later_failure_merge():
    from unittest.mock import Mock, patch

    from cafe.agents.executor import AgentExecutionError, AgentExecutor
    from cafe.agents.manager import AgentManager
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentCLI, AgentConfig, AgentResponse

    sessions = Mock(spec=SessionManager)
    sessions.load_session.return_value = None
    manager = AgentManager(session_manager=sessions)
    manager.register_agent(AgentConfig(name="fixture", cli=AgentCLI.CODEX))
    success = estimate(invocation_id="success")
    with patch.object(
        AgentExecutor,
        "execute",
        return_value=AgentResponse(response="done", token_usage=success, cli=AgentCLI.CODEX),
    ):
        manager.execute("fixture", "prompt")
    error = AgentExecutionError("rate limit", error_type="rate_limit")
    error.accounting_usage = estimate(invocation_id="failed", complete=False)
    manager._record_failed_attempt(cli=AgentCLI.CODEX, chain_role="primary", attempt=1, error=error)
    totals = manager.get_total_token_usage()
    assert totals.input_tokens == 200
    assert [record["invocation_id"] for record in totals.cost_records] == ["success", "failed"]
    assert len(totals.model_dump(exclude_unset=True)["cost_records"]) == 2


def state(document=DOCUMENT):
    return {
        "snapshot": make_snapshot(document, fetched_at=100000, etag='"one"', last_modified="one"),
        "stale": False,
    }


def estimate(usage=None, **kwargs):
    usage = usage or TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=30,
        cache_write_input_tokens=10,
        reasoning_output_tokens=5,
    )
    return account_cost(usage, cli="codex", model="gpt-test", state=state(), **kwargs)


def test_cache_and_reasoning_are_subsets_and_the_exact_calculation_is_persisted():
    result = estimate()
    (record,) = result.cost_records
    # (60 * 2 + 30 * .2 + 10 * 2.5 + 20 * 10) / 1M.
    assert Decimal(record["amount_usd"]) == Decimal("0.000351")
    assert record["billed_tokens"] == dict(input=60, cached_input=30, cache_write=10, output=20)
    assert record["provenance"] == "estimated"
    assert record["pricing"]["version"] == state()["snapshot"]["version"]
    assert record["rates_usd_per_million_tokens"]["input"] == "2"
    assert "cost_records" in result.model_fields_set


@pytest.mark.parametrize("cost", [0, 0.125])
def test_reported_cost_takes_precedence_and_preserves_explicit_zero(cost):
    result = estimate(TokenUsage(total_cost_usd=cost, input_tokens=100, output_tokens=20))
    (record,) = result.cost_records
    assert record["provenance"] == "reported"
    assert result.total_cost_usd == cost
    assert "pricing" not in record
    assert "reported" in format_cost(summarize_cost(result.cost_records))


def test_estimated_zero_is_distinct_from_reported_zero_and_missing_cost():
    usage = TokenUsage(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_write_input_tokens=0
    )
    assert format_cost(summarize_cost(estimate(usage).cost_records)) == "$0.0000 estimated"
    assert format_cost(summarize_cost([], legacy_cost=0)) == "unknown"
    assert "legacy" in format_cost(summarize_cost([], legacy_cost=0.1))


def test_legacy_chat_zero_is_unknown_in_the_display(monkeypatch, capsys):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    StatusDisplay().render_chat_usage_table(
        [
            {
                "phase": "develop",
                "cli": "codex",
                "mode": "oneshot",
                "calls": 1,
                "stats": {"total_cost_usd": 0},
                "unknown_fields": [],
            }
        ]
    )
    row = capsys.readouterr().out.strip().splitlines()[-1]
    assert row.endswith(" | unknown")
    assert "$0.0000" not in row


@pytest.mark.parametrize(
    "usage,model,reason",
    [
        (TokenUsage(input_tokens=1, output_tokens=1), "gpt-test", "insufficient_token_usage"),
        (TokenUsage(), None, "model_unavailable"),
        (TokenUsage(), "alias", "unknown_model_or_rate"),
        (
            TokenUsage(input_tokens=1, output_tokens=1, cache_read_input_tokens=0),
            "gpt-test",
            "cache_write_usage_unavailable",
        ),
        (
            TokenUsage(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=2,
                cache_write_input_tokens=0,
            ),
            "gpt-test",
            "overlapping_token_counters",
        ),
        (
            TokenUsage(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_write_input_tokens=0,
                reasoning_output_tokens=2,
            ),
            "gpt-test",
            "overlapping_token_counters",
        ),
        (
            TokenUsage(
                input_tokens=272001,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_write_input_tokens=0,
            ),
            "gpt-test",
            "context_tier_unavailable",
        ),
        (
            TokenUsage(
                input_tokens=10,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_write_input_tokens=2,
                cache_creation_input_tokens=3,
            ),
            "gpt-test",
            "ambiguous_cache_write_counters",
        ),
    ],
)
def test_insufficient_or_ambiguous_evidence_does_not_guess(usage, model, reason):
    result = account_cost(usage, cli="codex", model=model, state=state())
    (record,) = result.cost_records
    assert record["provenance"] == "unavailable" and record["reason"] == reason
    assert "total_cost_usd" not in result.model_fields_set
    assert summarize_cost(result.cost_records)["unknown"] == 1


def test_cache_creation_and_cache_write_aliases_are_not_charged_twice():
    usage = TokenUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=10,
        cache_write_input_tokens=10,
    )
    assert estimate(usage).total_cost_usd == estimate().total_cost_usd


def test_specialized_codex_rate_does_not_need_a_nonexistent_cache_write_category():
    result = account_cost(
        TokenUsage(input_tokens=100, output_tokens=20, cache_read_input_tokens=30),
        cli="codex",
        model="gpt-test-codex",
        state=state(),
    )
    assert result.cost_records[0]["provenance"] == "estimated"


def test_changed_rates_do_not_modify_prior_recorded_estimate():
    first = estimate()
    persisted = json.dumps(first.model_dump())
    newer = state(DOCUMENT.replace("$2.00", "$4.00"))
    second = account_cost(
        TokenUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=30,
            cache_write_input_tokens=10,
        ),
        cli="codex",
        model="gpt-test",
        state=newer,
    )
    assert second.total_cost_usd > first.total_cost_usd
    assert json.dumps(first.model_dump()) == persisted
    assert summarize_cost(first.cost_records)["estimated"] == Decimal("0.000351")


def test_repeated_and_mixed_cumulative_records_merge_each_invocation_once():
    first, second = estimate(invocation_id="one"), estimate(invocation_id="two")
    merged = merge_token_usage_stats({}, first)
    assert merge_token_usage_stats(merged, first) == merged
    cumulative = TokenUsage(**merge_token_usage_stats(first.model_dump(), second))
    final = merge_token_usage_stats(merged, cumulative)
    assert final["input_tokens"] == 200
    assert final["total_cost_usd"] == pytest.approx(first.total_cost_usd * 2)
    assert len(final["cost_records"]) == 2


def test_partial_failure_and_stale_rates_keep_known_subtotals():
    result = estimate(complete=False)
    record = result.cost_records[0]
    record["pricing_stale"] = True
    missing = account_cost(TokenUsage(), cli="cursor", model=None)
    summary = summarize_cost([record, *missing.cost_records])
    assert summary["estimated"] == Decimal(record["amount_usd"])
    assert summary["unknown"] == 1
    assert "partial" in format_cost(summary) and "stale" in format_cost(summary)


def test_chat_and_iteration_keep_reproducible_estimates_without_double_counting(tmp_path):
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({"iteration": 1}))
    usage = estimate(invocation_id="chat")
    writer = chat_usage_sink(
        tmp_path, target, cli="codex", requested_model="alias", mode="one_shot", phase="develop"
    )
    result = (TransportResult(reported_model="gpt-test", usage=usage),)
    writer(result)
    writer(result)
    current = json.loads(target.read_text())
    (group,) = current["chat_usage"]
    assert group["calls"] == 1
    assert group["cost_records"] == usage.cost_records
    assert group["stats"]["input_tokens"] == 100
    remainder = phase_stats_without_chat(current["stats"], current["chat_usage"])
    assert remainder["cost_records"] == [] and remainder["total_cost_usd"] == 0


def test_status_iteration_model_step_and_workflow_agree_and_preserve_provenance(
    monkeypatch, capsys
):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    usage = estimate(invocation_id="workflow")
    entry = TimelineEntry(
        "iteration",
        "Iteration 1",
        "develop",
        datetime.now(timezone.utc),
        cli="codex",
        model="requested-alias",
        iteration=1,
        cost_usd=usage.total_cost_usd,
        cost_records=usage.cost_records,
    )
    display = StatusDisplay()
    assert "estimated" in display.format_iteration_entry(entry)
    aggregated = display._aggregate_model_usage([entry, entry])
    (model,) = aggregated.values()
    assert model["model"] == "gpt-test" and model["iterations"] == 1
    assert model["cost_usd"] == usage.total_cost_usd
    display.render_model_status_table([entry])
    display.render_cost_summary([entry], [])
    output = capsys.readouterr().out
    assert "Workflow: $0.0004 estimated" in output
    assert "develop: $0.0004 estimated" in output


def reported_usage(amount="0.20", invocation_id="new"):
    return account_cost(
        TokenUsage(
            input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
            cache_write_input_tokens=0, cache_read_input_tokens=0,
            reasoning_output_tokens=0, total_cost_usd=float(amount),
        ),
        cli="codex", model="selected", invocation_id=invocation_id,
    )


def test_existing_iteration_keeps_legacy_money_and_tokens_in_all_summaries(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({
        "iteration": 1,
        "stats": {"input_tokens": 100, "output_tokens": 50, "total_cost_usd": 1.0},
    }))
    usage = reported_usage()
    writer = iteration_usage_sink(tmp_path, target)
    writer(usage)
    writer(usage)  # Repeating publication must not duplicate the new amount.
    stats = json.loads(target.read_text())["stats"]
    assert stats["total_cost_usd"] == pytest.approx(1.2)
    entry = TimelineEntry(
        "iteration", "Iteration 1", "develop", datetime.now(timezone.utc),
        iteration=1, cli="codex", model="selected",
        input_tokens=stats["input_tokens"], output_tokens=stats["output_tokens"],
        cost_usd=stats["total_cost_usd"], cost_records=stats["cost_records"],
    )
    summary = summarize_cost(stats["cost_records"], legacy_cost=stats["total_cost_usd"])
    assert summary["known"] == Decimal("1.20") and summary["legacy"] == Decimal("1.00")
    display = StatusDisplay()
    assert "$1.0000 legacy" in display.format_iteration_entry(entry)
    model, = display._aggregate_model_usage([entry, entry]).values()
    assert model["input_tokens"] == 110 and model["output_tokens"] == 55
    assert model["cost_usd"] == pytest.approx(1.2)
    display.render_model_status_table([entry])
    display.render_cost_summary([entry], [])
    output = capsys.readouterr().out
    assert "develop: $0.2000 reported + $1.0000 legacy" in output
    assert "Workflow: $0.2000 reported + $1.0000 legacy" in output


def test_float_accumulation_is_not_mistaken_for_legacy_spend():
    first, second = reported_usage("0.1", "one"), reported_usage("0.2", "two")
    merged = merge_token_usage_stats(merge_token_usage_stats({}, first), second)
    summary = summarize_cost(merged["cost_records"], legacy_cost=merged["total_cost_usd"])
    assert summary["known"] == Decimal("0.3")
    assert summary["counts"]["legacy"] == 0


@pytest.mark.parametrize("legacy", [0.0, 1e-12, 1.25])
def test_chat_subtraction_preserves_phase_cost_without_float_residue(
    tmp_path, monkeypatch, capsys, legacy
):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({"iteration": 1, "stats": {"total_cost_usd": legacy}}))
    phase = reported_usage("0", "phase")
    iteration_usage_sink(tmp_path, target)(phase)
    writer = chat_usage_sink(tmp_path, target, cli="codex", requested_model="alias",
                             mode="one_shot", phase="develop")
    for index, (model, amount) in enumerate((("a", "0.1"), ("b", "0.1"), ("a", "0.2"))):
        writer((TransportResult(reported_model=model, usage=reported_usage(amount, str(index))),))
    current = json.loads(target.read_text())
    remaining = phase_stats_without_chat(current["stats"], current["chat_usage"])
    assert remaining["total_cost_usd"] == pytest.approx(legacy, abs=1e-16)
    if legacy == 0:
        assert remaining["total_cost_usd"] == 0
    else:
        assert remaining["total_cost_usd"] > 0
    assert remaining["cost_records"] == phase.cost_records
    entry = TimelineEntry("iteration", "Iteration 1", "develop", datetime.now(timezone.utc),
                          iteration=1, cli="codex", model="selected",
                          input_tokens=remaining["input_tokens"],
                          output_tokens=remaining["output_tokens"],
                          cost_usd=remaining["total_cost_usd"], cost_records=remaining["cost_records"])
    display = StatusDisplay()
    display.render_table([entry])
    display.render_model_status_table([entry])
    display.render_cost_summary([entry], current["chat_usage"])
    output = capsys.readouterr().out
    assert "Workflow: $0.4000 reported" in output
    assert "Invalid legacy cost" not in output


def test_chat_subtraction_does_not_hide_genuine_invalid_negative_cost():
    phase = reported_usage("0", "phase")
    stats = {"total_cost_usd": 0.1, "cost_records": phase.cost_records}
    remaining = phase_stats_without_chat(stats, [{"stats": {"total_cost_usd": 0.2}}])
    assert remaining["total_cost_usd"] < 0
    with pytest.raises(ValueError, match="Invalid legacy cost"):
        summarize_cost(remaining["cost_records"], legacy_cost=remaining["total_cost_usd"])


def test_legacy_unpriced_iteration_usage_remains_partial(monkeypatch, capsys):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    usage = reported_usage()
    entry = TimelineEntry(
        "iteration", "Iteration 1", "develop", datetime.now(timezone.utc),
        iteration=1, cli="codex", model="selected", input_tokens=110,
        output_tokens=55, cost_usd=0.2, cost_records=usage.cost_records,
    )
    display = StatusDisplay()
    assert "partial" in display.format_iteration_entry(entry)
    display.render_cost_summary([entry], [])
    assert "Workflow: $0.2000 reported (partial)" in capsys.readouterr().out


@pytest.mark.parametrize("old_cost", [None, 1.0])
def test_upgraded_chat_keeps_legacy_spend_and_missing_cost_coverage(
    tmp_path, monkeypatch, capsys, old_cost
):
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({"iteration": 1}))
    writer = chat_usage_sink(
        tmp_path, target, cli="codex", requested_model="selected",
        mode="one_shot", phase="develop",
    )
    # First call uses the old schema, including the legitimate no-usage failure.
    writer((TransportResult(
        reported_model="selected", usage=TokenUsage(total_cost_usd=old_cost)
        if old_cost is not None else None,
        failure_code=None if old_cost is not None else "execution_failed",
    ),))
    writer((TransportResult(reported_model="selected", usage=reported_usage()),))
    current = json.loads(target.read_text())
    group, = current["chat_usage"]
    assert group["calls"] == 2
    display = StatusDisplay()
    display.render_chat_usage_table([group])
    display.render_cost_summary([], [group])
    output = capsys.readouterr().out
    if old_cost is None:
        assert "develop: $0.2000 reported (partial)" in output
        assert "Workflow: $0.2000 reported (partial)" in output
    else:
        assert "develop: $0.2000 reported + $1.0000 legacy" in output
        assert "Workflow: $0.2000 reported + $1.0000 legacy" in output
    # Removing chat from phase aggregates must still leave no extra charge.
    remainder = phase_stats_without_chat(current["stats"], [group])
    assert remainder["total_cost_usd"] == pytest.approx(0)
    assert remainder["cost_records"] == []
