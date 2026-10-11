"""U3/U4/U9, I2/I3/I8: caller-only scalars and joint descendant admission."""

import json
from copy import deepcopy
from decimal import Decimal

from cafe.core.cost import combine_cost_summaries, summarize_cost
from cafe.core.types import TokenUsage
from cafe.core.usage import merge_token_usage_stats
from cafe.services.cost_summary import collect_cost_sources, summarize_sources
from tests.unit.test_native_accounting import record


def caller(amount="1", tokens=10):
    return dict(
        invocation_id="caller",
        session_id="root",
        usage=dict(input_tokens=tokens, output_tokens=2),
        amount_usd=amount,
        provenance="reported",
        complete=True,
    )


def persisted_summary(tmp_path, stats):
    path = tmp_path / "compose/iteration_001/iteration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(stats=stats)))
    return summarize_sources(collect_cost_sources(tmp_path))


def test_caller_scalar_and_legacy_remainder_exclude_child_work(tmp_path):
    root, child = caller(), record(80, "final")
    incoming = TokenUsage(
        input_tokens=10, output_tokens=2, total_cost_usd=1, cost_records=[root, child]
    )
    stats = merge_token_usage_stats(dict(total_cost_usd=0.25), incoming)
    assert stats["input_tokens"] == 10 and stats["total_cost_usd"] == 1.25
    result = persisted_summary(tmp_path, stats)
    assert result["legacy"] == Decimal("0.25") and result["known"] == Decimal("2.05")
    assert result["native_usage"]["child_tokens"]["input_tokens"] == 80


def test_replayed_caller_still_merges_new_child_records(tmp_path):
    root, child = caller(), record(80, "final")
    initial = TokenUsage(input_tokens=10, total_cost_usd=1, cost_records=[root, child])
    stats = merge_token_usage_stats(dict(total_cost_usd=0.25), initial)
    other = record(40, "final")
    other["invocation_id"] = "native:other"
    other["session_id"] = other["native_usage"]["session_id"] = "other"
    other["native_usage"]["segment_id"] = "other"
    replay = TokenUsage(input_tokens=10, total_cost_usd=1, cost_records=[root, child, other])
    for _ in range(2):
        stats = merge_token_usage_stats(stats, replay)
        assert stats["input_tokens"] == 10 and stats["total_cost_usd"] == 1.25
        assert persisted_summary(tmp_path, stats)["known"] == Decimal("2.45")


def test_joint_overlap_withholds_children_and_keeps_caller_and_legacy(tmp_path):
    root, child = caller(), record(80, "final")
    stats = merge_token_usage_stats(
        dict(total_cost_usd=0.25), TokenUsage(total_cost_usd=1, cost_records=[root, child])
    )
    local = persisted_summary(tmp_path, stats)
    overlap = deepcopy(child)
    overlap["invocation_id"] = "native:overlap"
    overlap["native_usage"]["segment_id"] = "overlap"
    overlap["native_usage"]["start"].update(
        offset=30, counters=dict(input_tokens=20, output_tokens=2)
    )
    overlap["usage"] = dict(input_tokens=60, output_tokens=6, total_tokens=66)
    overlap["amount_usd"] = "0.6"
    result = combine_cost_summaries([local, summarize_cost([overlap])])
    assert result["known"] == Decimal("1.25") and result["incomplete"]
    assert result["native_usage"]["tokens"]["input_tokens"] == 10


def test_exact_inclusive_parent_keeps_only_parent_and_legacy_money(tmp_path):
    root, child = caller("2", 200), record(100, "final", inclusion="inclusive")
    native = child["native_usage"]
    root["native_inclusion"] = dict(
        kind="exact_inclusive",
        source="verified",
        segments=[{key: native[key] for key in ("segment_id", "session_id", "start", "end")}],
    )
    root["usage"]["output_tokens"] = 20
    stats = merge_token_usage_stats(
        dict(total_cost_usd=0.25), TokenUsage(total_cost_usd=2, cost_records=[root, child])
    )
    assert stats["total_cost_usd"] == 2.25
    assert persisted_summary(tmp_path, stats)["known"] == Decimal("2.25")
