"""Plan U3/U4/U5/U8/U9: durable refinements, subsets and physical replay."""

from copy import deepcopy

import pytest

from cafe.agents.cli.codex_subagent_usage import interval_delta
from cafe.core.cost import accounting_admission
from cafe.core.cost import merge_cost_records as merge_native_records
from cafe.core.types import TokenUsage
from cafe.core.usage import merge_token_usage_stats


def native_projection(records):
    return accounting_admission(records)["native_usage"]


def record(end=100, status="partial", model="gpt-5.4", inclusion="exclusive", kind="child"):
    return dict(
        invocation_id="native:one",
        session_id="child",
        model=model,
        cli="codex",
        currency="USD",
        provenance="estimated",
        amount_usd=str(end / 100),
        complete=status == "final",
        usage=dict(input_tokens=end, output_tokens=end // 10, total_tokens=end + end // 10),
        native_usage=dict(
            version=1,
            segment_id="one",
            kind=kind,
            root_session_id="root",
            parent_session_id="root",
            session_id="child",
            workflow_id="flow",
            caller_id="custom",
            attempt_id="a",
            cli_version="0.159.3",
            inclusion=inclusion,
            status=status,
            turn_ids=["t"],
            gaps=[],
            start=dict(offset=10, counters=dict(input_tokens=0, output_tokens=0), anchor="a"),
            end=dict(
                offset=end + 10,
                counters=dict(input_tokens=end, output_tokens=end // 10),
                at="2026-10-10T00:00:20+00:00",
            ),
            source=dict(kind="codex_jsonl"),
        ),
    )


def test_partial_categories_and_subsets_remain_known():
    delta, gaps = interval_delta(
        dict(input_tokens=10, output_tokens=1),
        dict(input_tokens=30, output_tokens=3, cached_input_tokens=9),
    )
    assert delta == dict(input_tokens=20, output_tokens=2, total_tokens=22)
    assert "cache_read_input_tokens" not in delta
    delta, gaps = interval_delta(
        dict(input_tokens=0, output_tokens=0, cached_input_tokens=0),
        dict(input_tokens=10, output_tokens=2, cached_input_tokens=99),
    )
    assert delta["input_tokens"] == 10 and "cache_read_input_tokens" not in delta
    assert gaps


def test_scope_binding_refines_but_child_cutoffs_are_immutable():
    from datetime import datetime, timezone

    from cafe.agents.cli.codex_subagent_usage import NativeInterval
    from tests.unit.test_codex_subagent_usage import ROOT

    scope = NativeInterval(
        "/nonexistent",
        workflow_id="flow",
        caller_id="caller",
        attempt_id="a",
        started_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    opened = scope.open_record()
    scope.bind(ROOT)
    final = scope.open_record(final=True)
    merged = merge_native_records([opened], [scope.open_record(), final, final])
    assert len(merged) == 1 and merged[0]["native_usage"]["status"] == "final"
    assert merge_native_records(merged, [opened]) == merged
    child = record(150, "final")
    assert merge_native_records([child], [child]) == [child]
    conflict = merge_native_records([child], [record(160, "final")])[0]
    assert conflict["native_usage"]["gaps"] and not conflict["complete"]
    assert conflict["amount_usd"] is None


def test_records_only_child_replay_never_changes_caller_scalars():
    child = record(150, "final")
    old = merge_token_usage_stats(
        dict(input_tokens=7, total_cost_usd=0.25), TokenUsage(cost_records=[child])
    )
    assert old["input_tokens"] == 7 and old["total_cost_usd"] == 0.25
    assert merge_token_usage_stats(old, TokenUsage(cost_records=[child])) == old


@pytest.mark.parametrize(
    "field,value",
    [("version", True), ("version", 2), ("kind", "other"), ("caller_id", "")],
)
def test_public_cost_reader_rejects_invalid_native_contract(field, value):
    from cafe.core.cost import summarize_cost

    child = record(100, "final")
    child["native_usage"][field] = value
    with pytest.raises(ValueError):
        summarize_cost([child])


def test_historical_progress_is_readable_but_never_refines_child_evidence():
    from cafe.core.cost import summarize_cost

    old = record(100, "progress")
    summary = summarize_cost([old], legacy_cost=1.25)
    assert summary["legacy"] == 0.25 and summary["incomplete"]
    conflict = summarize_cost([old, record(150, "final")], legacy_residual=0.25)
    assert conflict["known"] == 0.25 and conflict["incomplete"]


@pytest.mark.parametrize("inclusion", ["exclusive", "inclusive", "unknown"])
def test_parent_overlap_never_adds_ambiguous_components(inclusion):
    child = record(100, "final", inclusion=inclusion)
    root = dict(
        invocation_id="root",
        usage=dict(input_tokens=200, output_tokens=20),
        provenance="estimated",
        amount_usd="2",
        model="different-model",
        complete=True,
        session_id="root",
    )
    view = native_projection([root, child])
    assert view["child_tokens"]["input_tokens"] == 100
    if inclusion == "exclusive":
        assert view["tokens"]["input_tokens"] == 300
    else:
        assert not view["complete"] and view["combined_tokens"] is None


def test_overlapping_physical_ranges_are_not_additive():
    one = record(100, "final")
    two = deepcopy(one)
    two["invocation_id"] = "native:two"
    two["native_usage"]["segment_id"] = "two"
    two["native_usage"]["start"].update(offset=30, counters=dict(input_tokens=20, output_tokens=2))
    two["usage"] = dict(input_tokens=80, output_tokens=8, total_tokens=88)
    two["amount_usd"] = "0.8"
    view = native_projection([one, two])
    assert not view["complete"] and view["combined_tokens"] is None


def test_chat_child_replay_counts_one_attempt_without_child_scalar_projection(tmp_path):
    import json

    from cafe.agents.transport_types import TransportResult
    from cafe.core.usage import chat_usage_sink
    metadata = tmp_path / "iteration.json"
    metadata.write_text(json.dumps(dict(iteration=1)))
    sink = chat_usage_sink(
        tmp_path, metadata, cli="codex", requested_model="parent", mode="one_shot", phase="custom"
    )
    child = record(150, "final")
    for _ in range(2):
        sink((TransportResult(usage=TokenUsage(cost_records=[child])),))
    data = json.loads(metadata.read_text())
    (group,) = data["chat_usage"]
    assert group["calls"] == 1
    assert "input_tokens" not in group["stats"]
    assert data["stats"]["input_tokens"] == 0
    assert len(group["cost_records"]) == 1


def test_exact_inclusive_parent_preserves_authoritative_amount_and_child_detail():
    from cafe.core.cost import summarize_cost

    child = record(100, "final", inclusion="inclusive", model="child-model")
    native = child["native_usage"]
    parent = dict(
        invocation_id="root",
        session_id="root",
        cli="codex",
        model="parent-model",
        usage=dict(input_tokens=200, output_tokens=20, total_tokens=220),
        provenance="reported",
        amount_usd="2",
        complete=True,
        native_inclusion=dict(
            kind="exact_inclusive",
            source="verified_provider_attestation",
            segments=[{key: native[key] for key in ("segment_id", "session_id", "start", "end")}],
        ),
    )
    summary = summarize_cost([parent, child, child])
    view = summary["native_usage"]
    assert view["complete"] and view["tokens"]["total_tokens"] == 220
    assert view["child_tokens"]["input_tokens"] == 100
    assert summary["reported"] == 2 and summary["estimated"] == 0
    assert not summary["incomplete"]
    parent["native_inclusion"]["segments"][0] = dict(native["start"])
    assert not native_projection([parent, child])["complete"]


@pytest.mark.parametrize("invalid", ["cached_input_tokens", "reasoning_output_tokens"])
def test_invalid_subset_never_discards_independent_categories_regardless_of_order(invalid):
    from itertools import permutations

    from cafe.core.cost import normalized_counters

    entries = [(invalid, -1), ("input_tokens", 10), ("output_tokens", 2)]
    for order in permutations(entries):
        values, gaps = normalized_counters(dict(order))
        assert values == dict(input_tokens=10, output_tokens=2)
        assert len(gaps) == 1
