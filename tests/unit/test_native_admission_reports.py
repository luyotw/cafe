"""U3/U4/U5/U9, I2/I3/I8: public reports share token and money admission."""

import json
from copy import deepcopy
from decimal import Decimal

import pytest

from cafe.core.cost import combine_cost_summaries, format_cost, summarize_cost
from cafe.core.types import TokenUsage
from cafe.core.usage import merge_token_usage_stats
from cafe.manager.costs import inclusive_report, manager_usage_sink, preserve_worker_cost
from cafe.services.cost_summary import collect_cost_sources, summarize_sources
from tests.unit.test_manager_costs import cost_journey
from tests.unit.test_native_accounting import record


def segments(overlap=True):
    one = record(100, "final")
    two = deepcopy(one)
    two["invocation_id"] = "native:two"
    two["native_usage"]["segment_id"] = "two"
    if overlap:
        two["native_usage"]["start"].update(
            offset=30, counters=dict(input_tokens=20, output_tokens=2)
        )
        two["usage"] = dict(input_tokens=80, output_tokens=8, total_tokens=88)
        two["amount_usd"] = "0.8"
    else:
        two["native_usage"]["start"]["offset"] = 120
        two["native_usage"]["end"]["offset"] = 220
    return one, two


@pytest.mark.parametrize("overlap", [True, False])
def test_retained_inclusive_report_jointly_admits_distinct_segments(tmp_path, overlap):
    root, issue = cost_journey(tmp_path)
    one, two = segments(overlap)
    path = issue / "custom/iteration_001/iteration.json"
    path.write_text(json.dumps(dict(stats=dict(cost_records=[one], total_cost_usd=1.25))))
    manager_usage_sink(root, "topic", "wf", "native-manager")(TokenUsage(cost_records=[two]))
    preserve_worker_cost(root, issue, "topic", "wf")
    # Retained reporting works without the transient source.
    import shutil

    shutil.rmtree(issue)
    combined = inclusive_report(root, "topic", "wf")["combined"]
    assert combined["legacy"] == Decimal("0.25")
    assert combined["known"] == (Decimal("0.25") if overlap else Decimal("2.25"))
    # Historical host coverage remains unknown independently of native admission.
    assert combined["incomplete"]
    assert combined["native_usage"]["complete"] is not overlap
    assert combined["native_usage"]["tokens"] == (
        {} if overlap else dict(input_tokens=200, output_tokens=20, total_tokens=220)
    )


@pytest.mark.parametrize("legacy", [0, 0.25])
def test_persisted_overlap_is_not_recast_as_legacy_and_recollection_is_stable(tmp_path, legacy):
    one, two = segments()
    incoming = TokenUsage(
        input_tokens=180, output_tokens=18, total_cost_usd=1.8, cost_records=[one, two]
    )
    stats = merge_token_usage_stats(dict(input_tokens=7, total_cost_usd=legacy), incoming)
    assert stats["input_tokens"] == 7
    assert stats["output_tokens"] == 0
    assert stats["total_cost_usd"] == pytest.approx(legacy)
    assert merge_token_usage_stats(stats, incoming) == stats
    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    (directory / "iteration.json").write_text(json.dumps(dict(stats=stats)))
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["known"] == Decimal(str(legacy))
    assert result["incomplete"]
    assert result["native_usage"]["tokens"] == {}


@pytest.mark.parametrize("legacy", [0, 0.25])
def test_old_persisted_overlap_keeps_only_true_residual(tmp_path, legacy):
    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    (directory / "iteration.json").write_text(
        json.dumps(
            dict(
                stats=dict(
                    input_tokens=180,
                    output_tokens=18,
                    total_cost_usd=1.8 + legacy,
                    cost_records=list(segments()),
                )
            )
        )
    )
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["known"] == pytest.approx(Decimal(str(legacy)))
    assert result["incomplete"]


def test_conflicting_final_evidence_cannot_reappear_as_legacy():
    one = record(100, "final")
    two = record(160, "final")
    stats = merge_token_usage_stats(
        {},
        TokenUsage(input_tokens=260, output_tokens=26, total_cost_usd=2.6, cost_records=[one, two]),
    )
    assert stats["input_tokens"] == 0
    assert stats["total_cost_usd"] == 0
    summary = summarize_cost(stats["cost_records"], legacy_cost=stats["total_cost_usd"])
    assert summary["known"] == 0 and summary["incomplete"]
    assert format_cost(summary).startswith("unknown")


def test_admitted_non_native_categories_survive_and_refine_once():
    child = record()
    parent = dict(
        invocation_id="provider-neutral",
        usage=dict(
            input_tokens=10, output_tokens=2, total_tokens=12, cache_creation_input_tokens=3
        ),
        provenance="reported",
        amount_usd="0.2",
        complete=True,
    )
    stats = merge_token_usage_stats(
        {},
        TokenUsage(
            input_tokens=110,
            output_tokens=12,
            cache_creation_input_tokens=3,
            total_cost_usd=1.2,
            cost_records=[parent, child],
        ),
    )
    final = record(150, "final")
    stats = merge_token_usage_stats(
        stats,
        TokenUsage(input_tokens=150, output_tokens=15, total_cost_usd=1.5, cost_records=[final]),
    )
    assert stats["input_tokens"] == 160 and stats["output_tokens"] == 17
    assert stats["cache_write_input_tokens"] == stats["cache_creation_input_tokens"] == 3
    assert stats["total_cost_usd"] == pytest.approx(1.7)
    assert (
        merge_token_usage_stats(
            stats,
            TokenUsage(
                input_tokens=150, output_tokens=15, total_cost_usd=1.5, cost_records=[final]
            ),
        )
        == stats
    )


def test_combined_nested_summaries_preserve_independent_records_and_true_legacy_once():
    one, two = segments()
    other = dict(
        invocation_id="other",
        provenance="reported",
        amount_usd="0.3",
        complete=True,
        usage=dict(input_tokens=4, output_tokens=1, total_tokens=5),
    )
    a = summarize_cost([one, other], legacy_cost=1.55)
    b = summarize_cost([two, other])
    combined = combine_cost_summaries([combine_cost_summaries([a]), b])
    assert combined["known"] == Decimal("0.55")
    assert combined["legacy"] == Decimal("0.25")
    assert combined["reported"] == Decimal("0.3")
    assert combined["native_usage"]["tokens"]["total_tokens"] == 5
    assert combined["incomplete"]


@pytest.mark.parametrize("zero", [False, True])
def test_public_cost_status_distinguishes_excluded_amount_from_verified_zero(
    monkeypatch, capsys, zero
):
    from cafe.services.status_display import StatusDisplay
    from cafe.services.timeline_builder import TimelineEntry

    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    rows = [record(0, "final")] if zero else list(segments())
    summary = summarize_cost(rows)
    assert bool(summary["incomplete"]) is not zero
    text = format_cost(summary)
    assert text.startswith("$0.0000") if zero else text.startswith("unknown")
    entry = TimelineEntry(
        entry_type="iteration",
        phase="compose",
        name="compose",
        start_time=None,
        iteration=1,
        cost_records=rows,
        cost_usd=0 if zero else 1.8,
    )
    StatusDisplay().render_cost_summary([entry], [])
    text = capsys.readouterr().out
    assert "$0.0000" in text if zero else "$1.8000" not in text and "unknown" in text


def test_persisted_residual_reaches_public_timeline_and_chat_partition(
    tmp_path, monkeypatch, capsys
):
    from cafe.agents.transport_types import TransportResult
    from cafe.core.usage import chat_usage_sink, phase_stats_without_chat
    from cafe.services.status_display import StatusDisplay
    from cafe.services.timeline_builder import TimelineBuilder

    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    rows = list(segments())
    initial = dict(input_tokens=7, total_cost_usd=0.25)
    stats = merge_token_usage_stats(
        initial,
        TokenUsage(input_tokens=180, output_tokens=18, total_cost_usd=1.8, cost_records=rows),
    )
    status = dict(
        timestamp="2026-10-11T00:00:00+00:00", iteration=1, status="completed", stats=stats
    )
    entries = TimelineBuilder("custom", phase_names=["compose"]).build_timeline_entries(
        {}, {"compose": [status]}
    )
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    StatusDisplay().render_cost_summary(entries, [])
    text = capsys.readouterr().out
    assert "$0.2500 legacy" in text and "$1.8000" not in text

    metadata = directory / "iteration.json"
    metadata.write_text(json.dumps(dict(iteration=1, stats=initial)))
    sink = chat_usage_sink(
        tmp_path, metadata, cli="codex", requested_model="parent", mode="one_shot", phase="compose"
    )
    usage = TokenUsage(input_tokens=180, output_tokens=18, total_cost_usd=2.3, cost_records=rows)
    sink((TransportResult(usage=usage),))
    sink((TransportResult(usage=usage),))
    data = json.loads(metadata.read_text())
    remaining = phase_stats_without_chat(data["stats"], data["chat_usage"])
    assert remaining["input_tokens"] == 7 and remaining["total_cost_usd"] == pytest.approx(0.25)
    summary = summarize_sources(collect_cost_sources(tmp_path))
    assert summary["known"] == pytest.approx(Decimal("0.75")) and summary["incomplete"]


def test_exact_inclusive_combination_preserves_parent_and_true_residual():
    child = record(100, "final", inclusion="inclusive")
    n = child["native_usage"]
    parent = dict(
        invocation_id="root",
        session_id="root",
        usage=dict(input_tokens=200, output_tokens=20, total_tokens=220),
        provenance="reported",
        amount_usd="2",
        complete=True,
        native_inclusion=dict(
            kind="exact_inclusive",
            source="neutral_attestation",
            segments=[{k: n[k] for k in ("segment_id", "session_id", "start", "end")}],
        ),
    )
    combined = combine_cost_summaries(
        [summarize_cost([parent], legacy_cost=2.25), summarize_cost([child])]
    )
    assert combined["known"] == Decimal("2.25") and not combined["incomplete"]
    assert combined["native_usage"]["tokens"]["total_tokens"] == 220
    assert combined["native_usage"]["child_tokens"]["total_tokens"] == 110
    assert summarize_cost([parent, child], legacy_cost=2.25)["legacy"] == Decimal("0.25")


def test_refined_summaries_jointly_admit_only_latest_physical_endpoint():
    old, final = record(), record(150, "final")
    combined = combine_cost_summaries([summarize_cost([old]), summarize_cost([final])])
    assert combined["known"] == Decimal("1.5")
    assert not combined["incomplete"]
    assert combined["native_usage"]["tokens"]["total_tokens"] == 165


def test_native_float_rounding_does_not_create_a_legacy_component():
    row = record(30, "final")
    row["amount_usd"] = "0.3"
    stats = merge_token_usage_stats(
        {},
        TokenUsage(input_tokens=30, output_tokens=3, total_cost_usd=0.1 + 0.2, cost_records=[row]),
    )
    assert stats.get("accounting_residual", {}).get("total_cost_usd", 0) == 0
    summary = summarize_sources(
        [
            dict(
                source_id="native",
                records=stats["cost_records"],
                legacy_cost=stats["total_cost_usd"],
                legacy_residual=stats.get("accounting_residual", {}).get("total_cost_usd"),
            )
        ]
    )
    assert summary["known"] == Decimal("0.3") and summary["counts"]["legacy"] == 0


@pytest.mark.parametrize(
    "residual",
    [
        {"input_tokens": -1},
        {"input_tokens": 0.5},
        {"total_cost_usd": float("nan")},
        {"total_cost_usd": True},
        {"unrecognized": 1},
    ],
)
def test_chat_rejects_invalid_residual_metadata_without_overwriting(tmp_path, residual):
    from cafe.agents.transport_types import TransportResult
    from cafe.core.usage import chat_usage_sink

    path = tmp_path / "iteration.json"
    path.write_text(json.dumps(dict(iteration=1)))
    sink = chat_usage_sink(
        tmp_path, path, cli="codex", requested_model="parent", mode="one_shot", phase="compose"
    )
    usage = TokenUsage(
        input_tokens=100, output_tokens=10, total_cost_usd=1, cost_records=[record(100, "final")]
    )
    sink((TransportResult(usage=usage),))
    data = json.loads(path.read_text())
    data["chat_usage"][0]["stats"]["accounting_residual"] = residual
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        sink((TransportResult(usage=usage),))
    assert path.read_bytes() == before


def test_incomplete_zero_known_amount_is_unknown_in_public_renderers(monkeypatch, capsys):
    from cafe.manager.costs import format_summary
    from cafe.services.status_display import StatusDisplay
    from cafe.services.timeline_builder import TimelineEntry

    row = record(0, "progress")
    summary = summarize_cost([row])
    assert summary["known"] == 0 and summary["incomplete"]
    assert "unknown" in format_cost(summary)
    assert "unknown" in format_summary(summary)
    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    entry = TimelineEntry(
        entry_type="iteration",
        phase="compose",
        name="compose",
        start_time=None,
        iteration=1,
        cost_records=[row],
        cost_usd=0,
    )
    StatusDisplay().render_cost_summary([entry], [])
    output = capsys.readouterr().out
    assert "unknown" in output and "$0.0000" not in output


def test_token_usage_roundtrip_preserves_residual_through_public_iteration_sink(tmp_path):
    from cafe.core.usage import iteration_usage_sink

    incoming = TokenUsage(
        input_tokens=180, output_tokens=18, total_cost_usd=1.8, cost_records=list(segments())
    )
    projected = TokenUsage.model_validate(
        merge_token_usage_stats(dict(input_tokens=7, total_cost_usd=0.25), incoming)
    )
    assert projected.model_dump(exclude_unset=True)["accounting_residual"]["total_cost_usd"] == 0.25
    path = tmp_path / "iteration.json"
    path.write_text(json.dumps(dict(iteration=1)))
    sink = iteration_usage_sink(tmp_path, path)
    for usage in (projected, incoming, projected):
        sink(usage)
        stats = json.loads(path.read_text())["stats"]
        assert stats["input_tokens"] == 7 and stats["total_cost_usd"] == 0.25


@pytest.mark.parametrize(
    "usage",
    [
        dict(input_tokens=10, output_tokens=2, total_tokens=99),
        dict(input_tokens=10, output_tokens=2, total_tokens=12, cache_read_input_tokens=99),
    ],
)
def test_public_source_preserves_normalization_gaps_with_known_categories(tmp_path, usage):
    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    parent = dict(
        invocation_id="neutral",
        provenance="reported",
        amount_usd="0.2",
        usage=usage,
        complete=True,
        token_total_evidence=dict(
            kind="input_plus_output", source="native_adapter", version="verified"
        ),
    )
    (directory / "iteration.json").write_text(
        json.dumps(dict(stats=dict(cost_records=[parent, record(100, "final")])))
    )
    summary = summarize_sources(collect_cost_sources(tmp_path))
    assert summary["native_usage"]["tokens"]["input_tokens"] == 110
    assert summary["native_usage"]["tokens"]["output_tokens"] == 12
    assert summary["native_usage"]["combined_tokens"] is None
    assert not summary["native_usage"]["complete"] and summary["native_usage"]["gaps"]
    assert summary["incomplete"]


@pytest.mark.parametrize("legacy_cache", [0, 7])
def test_public_sink_does_not_recast_rejected_represented_cache_as_legacy(tmp_path, legacy_cache):
    from cafe.core.usage import iteration_usage_sink

    parent = dict(
        invocation_id="attested",
        provenance="reported",
        amount_usd="0.2",
        complete=True,
        usage=dict(input_tokens=10, output_tokens=2, total_tokens=12, cache_read_input_tokens=99),
        token_total_evidence=dict(kind="input_plus_output", source="native_adapter"),
    )
    child = record(100, "final")
    path = tmp_path / "iteration.json"
    path.write_text(
        json.dumps(
            dict(
                iteration=1,
                stats=dict(
                    input_tokens=110,
                    output_tokens=12,
                    cache_read_input_tokens=99 + legacy_cache,
                    total_cost_usd=1.2,
                    cost_records=[parent, child],
                ),
            )
        )
    )
    sink = iteration_usage_sink(tmp_path, path)
    for _ in range(2):
        sink(TokenUsage(cost_records=[child]))
        stats = json.loads(path.read_text())["stats"]
        assert stats["cache_read_input_tokens"] == legacy_cache
        assert (
            stats.get("accounting_residual", {}).get("cache_read_input_tokens", 0) == legacy_cache
        )
        summary = summarize_cost(stats["cost_records"], legacy_cost=stats["total_cost_usd"])
        assert "cache_read_input_tokens" not in summary["native_usage"]["tokens"]
        assert summary["incomplete"]


@pytest.mark.parametrize(
    "canonical,alias",
    [
        ("cache_read_input_tokens", "cached_input_tokens"),
        ("cache_write_input_tokens", "cache_creation_input_tokens"),
    ],
)
@pytest.mark.parametrize("independent", [False, True])
def test_public_persisted_source_withholds_conflicted_alias_residual_without_independent_evidence(
    tmp_path, canonical, alias, independent
):
    from cafe.core.usage import iteration_usage_sink

    parent = dict(
        invocation_id="attested",
        provenance="reported",
        amount_usd="0.2",
        complete=True,
        usage=dict(input_tokens=10, output_tokens=2, total_tokens=12, **{canonical: 3, alias: 4}),
        token_total_evidence=dict(kind="input_plus_output", source="native_adapter"),
    )
    child = record(100, "final")
    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    path = directory / "iteration.json"
    stats = dict(
        input_tokens=110,
        output_tokens=12,
        total_cost_usd=1.2,
        cost_records=[parent, child],
        **{canonical: 3},
    )
    residual_keys = [canonical]
    if alias == "cache_creation_input_tokens":
        stats[alias] = 4
        residual_keys.append(alias)
    if independent:
        stats["accounting_residual"] = dict.fromkeys(residual_keys, 7)
        stats.update(dict.fromkeys(residual_keys, 7))
    path.write_text(json.dumps(dict(iteration=1, stats=stats)))
    sink = iteration_usage_sink(tmp_path, path)
    for _ in range(2):
        sink(TokenUsage(cost_records=[child]))
        stats = json.loads(path.read_text())["stats"]
        for key in residual_keys:
            assert stats[key] == (7 if independent else 0)
            assert stats.get("accounting_residual", {}).get(key, 0) == (7 if independent else 0)
        summary = summarize_sources(collect_cost_sources(tmp_path))
        assert canonical not in summary["native_usage"]["tokens"]
        assert summary["incomplete"]
        assert summary["known"] == Decimal("1.2")


@pytest.mark.parametrize("provider", ["cursor-agent", "custom-provider"])
def test_public_source_keeps_non_native_exclusive_cache_categories(tmp_path, provider):
    directory = tmp_path / "compose/iteration_001"
    directory.mkdir(parents=True)
    parent = dict(
        invocation_id="neutral",
        cli=provider,
        provenance="reported",
        amount_usd="0.2",
        complete=True,
        usage=dict(
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
            cache_read_input_tokens=20,
            cache_creation_input_tokens=30,
        ),
    )
    stats = merge_token_usage_stats(
        {},
        TokenUsage(
            input_tokens=110,
            output_tokens=12,
            cache_read_input_tokens=20,
            cache_creation_input_tokens=30,
            total_cost_usd=1.2,
            cost_records=[parent, record(100, "final")],
        ),
    )
    (directory / "iteration.json").write_text(json.dumps(dict(stats=stats)))
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["native_usage"]["tokens"]["cache_read_input_tokens"] == 20
    assert result["native_usage"]["tokens"]["cache_write_input_tokens"] == 30
    assert not any(stats.get("accounting_residual", {}).values())
    assert result["known"] == Decimal("1.2")


@pytest.mark.parametrize(
    "residual",
    [
        {"input_tokens": -1},
        {"input_tokens": 0.5},
        {"total_cost_usd": True},
        {"total_cost_usd": float("nan")},
        {"unsupported": 1},
    ],
)
def test_token_usage_validates_residual_before_roundtrip(residual):
    with pytest.raises(ValueError):
        TokenUsage(accounting_residual=residual)


@pytest.mark.parametrize("route", ["confirmed", "complete", "clarification", "already_completed"])
def test_phase_result_keeps_residual_for_following_persisted_consumer(tmp_path, route):
    from cafe.agents.manager import AgentManager
    from cafe.core.session import SessionManager
    from cafe.core.status_codes import PhaseStatusCode
    from tests.unit.test_phase_progress import ConcretePhase

    projected = TokenUsage.model_validate(
        merge_token_usage_stats(
            dict(input_tokens=7, total_cost_usd=0.25),
            TokenUsage(
                input_tokens=180,
                output_tokens=18,
                total_cost_usd=1.8,
                cost_records=list(segments()),
            ),
        )
    )
    phase_dir = tmp_path / "compose"
    phase_dir.mkdir()
    phase = ConcretePhase(phase_dir, interactive=False)
    phase.agent_manager = AgentManager(
        session_manager=SessionManager(sessions_dir=str(tmp_path / "sessions"))
    )
    phase.agent_manager._total_token_usage = projected
    if route == "already_completed":
        (phase_dir / "status.json").write_text(
            json.dumps(
                dict(status="completed", status_code=PhaseStatusCode.CONFIRMED.value, iteration=1)
            )
        )
        result = phase._check_if_already_completed([PhaseStatusCode.CONFIRMED])
    else:
        code = {
            "confirmed": PhaseStatusCode.CONFIRMED,
            "complete": PhaseStatusCode.READY_FOR_REVIEW,
            "clarification": PhaseStatusCode.NEED_CLARIFICATION,
        }[route]
        result = phase._handle_standard_status_codes(
            code,
            "evidence",
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
        )
    assert result is not None
    roundtrip = TokenUsage.model_validate(result.data["token_usage"])
    replay = merge_token_usage_stats(roundtrip.model_dump(exclude_unset=True), projected)
    assert replay["input_tokens"] == 7 and replay["total_cost_usd"] == 0.25


@pytest.mark.parametrize("consumer", ["executor", "manager"])
def test_public_execution_retains_residual_across_repeated_usage_roundtrips(
    tmp_path, monkeypatch, consumer
):
    from cafe.agents.executor import AgentExecutor
    from cafe.agents.manager import AgentManager
    from cafe.core.session import SessionManager
    from cafe.core.types import AgentCLI, AgentConfig
    from tests.unit.test_conversation_transport import provider_process

    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    supply = provider_process.__wrapped__(monkeypatch)
    projected = TokenUsage.model_validate(
        merge_token_usage_stats(
            dict(input_tokens=7, total_cost_usd=0.25),
            TokenUsage(
                input_tokens=180,
                output_tokens=18,
                total_cost_usd=1.8,
                cost_records=list(segments()),
            ),
        )
    )
    config = AgentConfig(name="accounting", cli=AgentCLI.CLAUDE)
    if consumer == "executor":
        caller = AgentExecutor(config, stream_output=False)
    else:
        caller = AgentManager(
            session_manager=SessionManager(sessions_dir=str(tmp_path / "sessions")),
            stream_agent_output=False,
        )
        caller.register_agent(config)
    # Restore the already persisted accounting state before exercising public execution.
    caller._total_token_usage = projected
    for _ in range(2):
        supply(
            [
                dict(type="system", subtype="init", session_id="accounting-session"),
                dict(
                    type="result",
                    result="complete",
                    usage=dict(input_tokens=0, output_tokens=0),
                    total_cost_usd=0,
                ),
            ]
        )
        if consumer == "executor":
            caller.execute("Collect accounting evidence")
        else:
            caller.execute("accounting", "Collect accounting evidence")
        observed = caller.get_total_token_usage()
        assert observed.input_tokens == 7
        assert observed.total_cost_usd == 0.25
        assert observed.accounting_residual["input_tokens"] == 7
        assert observed.accounting_residual["total_cost_usd"] == 0.25
