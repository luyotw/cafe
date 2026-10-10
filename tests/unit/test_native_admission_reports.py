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
