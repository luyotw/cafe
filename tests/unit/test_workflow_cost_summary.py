"""U1/U2/U4: persisted sources preserve uniqueness, residuals and coverage."""

import json
from decimal import Decimal

from cafe.services.cost_summary import collect_cost_sources, summarize_sources


def record(identity, amount="1", **fields):
    return dict(
        invocation_id=identity,
        provenance="reported",
        amount_usd=amount,
        **{"complete": True, **fields},
    )


def source(identity, records=(), legacy=None, **fields):
    return dict(source_id=identity, records=list(records), legacy_cost=legacy, **fields)


def test_workflow_deduplicates_attempts_globally_but_keeps_each_source_residual():
    sources = [
        source("custom/one", [record("first"), record("retry")], "2.5"),
        source("other/two", [record("retry"), record("fallback")], "2.25"),
        source("custom/one", [record("first"), record("retry")], "2.5"),
    ]
    result = summarize_sources(sources)
    assert result["reported"] == Decimal("3")
    assert result["legacy"] == Decimal(".75")
    assert result["known"] == Decimal("3.75")


def test_exclusions_do_not_turn_blended_legacy_into_worker_money():
    result = summarize_sources(
        [source("phase", [record("worker"), record("manager")], "2.5")],
        exclude_ids={"manager"},
        ambiguous_sources={"phase"},
    )
    assert result["known"] == Decimal("1")
    assert result["incomplete"]


def test_zero_missing_malformed_partial_and_stale_are_distinct():
    assert summarize_sources([source("zero", [record("zero", "0")])])["unknown"] == 0
    for sources in ([], [source("old", legacy=0)], [source("bad", [{}])]):
        assert summarize_sources(sources)["incomplete"]
    result = summarize_sources([source("stale", [record("x", pricing_stale=True)])])
    assert result["stale"]


def test_custom_phases_iteration_precedence_and_chat_overlap(tmp_path):
    phase = tmp_path / "custom-step" / "iteration_001"
    phase.mkdir(parents=True)
    (phase / "context.json").write_text(json.dumps({"stats": {"total_cost_usd": 99}}))
    chat = dict(cost_records=[record("chat", "2")], stats={"total_cost_usd": 2})
    (phase / "iteration.json").write_text(
        json.dumps(
            {
                "stats": {
                    "total_cost_usd": 3,
                    "cost_records": [record("worker"), record("chat", "2")],
                },
                "chat_usage": [chat],
            }
        )
    )
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["known"] == Decimal("3")
    assert result["counts"]["reported"] == 2
    (phase / "iteration.json").write_text("broken")
    assert summarize_sources(collect_cost_sources(tmp_path))["incomplete"]


def test_status_workflow_total_deduplicates_cross_phase_records(monkeypatch, capsys):
    from cafe.services.status_display import StatusDisplay
    from cafe.services.timeline_builder import TimelineEntry

    monkeypatch.setattr("cafe.services.status_display.RICH_AVAILABLE", False)
    entries = [
        TimelineEntry(
            entry_type="iteration",
            phase=phase,
            name=phase,
            start_time=None,
            iteration=1,
            cost_records=[record("shared")],
            cost_usd=1,
        )
        for phase in ("custom-a", "custom-b")
    ]
    StatusDisplay().render_cost_summary(entries, [])
    assert "Workflow: $1.0000 reported" in capsys.readouterr().out


def test_corrupt_record_does_not_hide_other_valid_known_costs():
    result = summarize_sources([source("mixed", [record("good"), record("bad", "oops")])])
    assert result["known"] == 1
    assert result["incomplete"]


def test_conflicting_invocation_is_unknown_instead_of_an_arbitrary_amount():
    result = summarize_sources(
        [source("one", [record("same", "1")]), source("two", [record("same", "2")])]
    )
    assert result["known"] == 0
    assert result["incomplete"]


def test_unpriced_legacy_usage_keeps_source_incomplete(tmp_path):
    phase = tmp_path / "custom" / "iteration_001"
    phase.mkdir(parents=True)
    stats = dict(
        total_cost_usd=1,
        input_tokens=100,
        output_tokens=100,
        cost_records=[record("new", usage=dict(input_tokens=10, output_tokens=10))],
    )
    (phase / "iteration.json").write_text(json.dumps(dict(stats=stats)))
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["known"] == 1
    assert result["incomplete"]
    # A priced legacy remainder keeps the existing status interpretation.
    stats["total_cost_usd"] = 2
    (phase / "iteration.json").write_text(json.dumps(dict(stats=stats)))
    result = summarize_sources(collect_cost_sources(tmp_path))
    assert result["known"] == 2
    assert not result["incomplete"]
