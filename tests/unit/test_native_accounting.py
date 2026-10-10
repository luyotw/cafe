"""Plan U3/U4/U5/U8/U9: durable refinements, subsets and physical replay."""

from copy import deepcopy

import pytest

from cafe.core.native_accounting import interval_delta, merge_native_records, native_projection
from cafe.core.types import TokenUsage
from cafe.core.usage import merge_token_usage_stats


def record(end=100, status="progress", model="gpt-5.4", inclusion="exclusive", kind="child"):
    return dict(
        invocation_id="native:one",
        session_id="child",
        model=model,
        cli="codex",
        currency="USD",
        provenance="estimated",
        amount_usd=str(end / 100),
        complete=status == "final",
        usage=dict(input_tokens=end, output_tokens=end // 10),
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


def test_refinement_replay_freeze_and_conflict():
    old, final = record(), record(150, "final")
    merged = merge_native_records([old], [final, final])
    assert len(merged) == 1 and merged[0]["usage"]["input_tokens"] == 150
    assert merge_native_records(merged, [old]) == merged
    conflict = merge_native_records(merged, [record(160, "final")])[0]
    assert conflict["native_usage"]["gaps"] and not conflict["complete"]
    assert conflict["amount_usd"] is None


def test_usage_refinement_replaces_tokens_and_money_once():
    a, b = record(), record(150, "final")
    old = merge_token_usage_stats(
        {}, TokenUsage(input_tokens=100, output_tokens=10, total_cost_usd=1, cost_records=[a])
    )
    refined = merge_token_usage_stats(
        old, TokenUsage(input_tokens=150, output_tokens=15, total_cost_usd=1.5, cost_records=[b])
    )
    assert refined["input_tokens"] == 150 and refined["total_cost_usd"] == 1.5
    assert (
        merge_token_usage_stats(
            refined,
            TokenUsage(input_tokens=150, output_tokens=15, total_cost_usd=1.5, cost_records=[b]),
        )
        == refined
    )


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
    two["usage"] = dict(input_tokens=80, output_tokens=8)
    two["amount_usd"] = "0.8"
    view = native_projection([one, two])
    assert not view["complete"] and view["combined_tokens"] is None


def test_chat_native_refinement_counts_one_attempt_and_retains_latest_endpoint(tmp_path):
    import json

    from cafe.agents.transport_types import TransportResult
    from cafe.core.usage import chat_usage_sink

    metadata = tmp_path / "iteration.json"
    metadata.write_text(json.dumps(dict(iteration=1)))
    sink = chat_usage_sink(
        tmp_path, metadata, cli="codex", requested_model="parent", mode="one_shot", phase="custom"
    )
    for row in [record(), record(150, "final"), record(150, "final")]:
        sink(
            (
                TransportResult(
                    usage=TokenUsage(
                        input_tokens=row["usage"]["input_tokens"],
                        output_tokens=row["usage"]["output_tokens"],
                        total_cost_usd=float(row["amount_usd"]),
                        cost_records=[row],
                    )
                ),
            )
        )
    data = json.loads(metadata.read_text())
    (group,) = data["chat_usage"]
    assert group["calls"] == 1
    assert group["stats"]["input_tokens"] == data["stats"]["input_tokens"] == 150
    assert group["stats"]["total_cost_usd"] == 1.5
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
        usage=dict(input_tokens=200, output_tokens=20),
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
