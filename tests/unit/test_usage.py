"""Existing iteration telemetry invariants (Plan U6/U11/I6)."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from cafe.core.types import TokenUsage
from cafe.core.usage import iteration_usage_sink, merge_token_usage_stats


def test_merge_preserves_supported_statistics_and_unrelated_fields():
    usage = TokenUsage(
        input_tokens=2,
        output_tokens=3,
        cache_creation_input_tokens=4,
        cache_write_input_tokens=5,
        cache_read_input_tokens=6,
        reasoning_output_tokens=7,
        total_cost_usd=0.25,
        duration_ms=10,
        duration_api_ms=8,
        turn_usages=[dict(turn=1, input_tokens=2)],
    )
    merged = merge_token_usage_stats(dict(input_tokens=1, other="retained"), usage)
    assert merged["other"] == "retained"
    assert merged["input_tokens"] == 3
    for name, value in usage.model_dump().items():
        if name != "input_tokens":
            assert merged[name] == value


def test_existing_iteration_sink_preserves_concurrent_metadata(tmp_path):
    target = tmp_path / ".cafe/issues/x/custom/iteration_004/iteration.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            dict(iteration=4, timestamp="original", session_id="s", stats=dict(input_tokens=1))
        )
    )
    sink = iteration_usage_sink(tmp_path, target)
    metadata = json.loads(target.read_text())
    metadata["unrelated"] = "new"
    target.write_text(json.dumps(metadata))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(sink, [TokenUsage(input_tokens=2), TokenUsage(input_tokens=3)]))
    merged = json.loads(target.read_text())
    assert merged["stats"]["input_tokens"] == 6
    assert merged["unrelated"] == "new"
    assert merged["session_id"] == "s"


def test_sink_does_not_create_missing_or_replaced_iteration(tmp_path):
    assert iteration_usage_sink(tmp_path, tmp_path / "missing.json") is None
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps(dict(iteration=1, timestamp="original")))
    sink = iteration_usage_sink(tmp_path, target)
    target.write_text(json.dumps(dict(iteration=2, timestamp="replacement")))
    with pytest.raises(ValueError):
        sink(TokenUsage(input_tokens=3))
    assert "stats" not in json.loads(target.read_text())


def test_independently_admitted_writers_merge_latest_counts_with_bounded_empty_slot(tmp_path):
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps(dict(iteration=1, timestamp="pinned", other="kept", stats=dict(input_tokens=1))))
    sinks = [iteration_usage_sink(tmp_path, target) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda pair: pair[0](TokenUsage(input_tokens=pair[1])),
                      [(sink, n) for _ in range(2) for n, sink in enumerate(sinks, start=1)]))
    current = json.loads(target.read_text())
    assert current['stats']['input_tokens'] == 73
    assert current['timestamp'] == 'pinned' and current['other'] == 'kept'
    slots = list(tmp_path.glob('.usage-*'))
    assert len(slots) == 1 and slots[0].stat().st_size == 0


def test_unsupported_exchange_preserves_metadata_and_clears_only_its_publication_slot(tmp_path, monkeypatch):
    import errno
    import cafe.core.usage as usage_module

    target = tmp_path / 'iteration.json'
    target.write_text(json.dumps(dict(iteration=1, timestamp='pinned', other='kept')))
    original = target.read_bytes()
    sink = iteration_usage_sink(tmp_path, target)
    monkeypatch.setattr(usage_module.ctypes, 'CDLL', lambda *args, **kwargs: object())
    with pytest.raises(OSError) as caught:
        sink(TokenUsage(input_tokens=2))
    assert caught.value.errno == errno.ENOTSUP
    assert target.read_bytes() == original
    slots = list(tmp_path.glob('.usage-*'))
    assert len(slots) == 1 and slots[0].stat().st_size == 0
