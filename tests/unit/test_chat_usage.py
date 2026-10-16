"""Known subtotals, unknown coverage and shared publication (Plan U6/U11/I6)."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from cafe.agents.transport_types import TransportResult
from cafe.core.types import TokenUsage
from cafe.core.usage import CHAT_USAGE_FIELDS, chat_usage_sink
from cafe.services import status_display


def sink(root, target, **kwargs):
    return chat_usage_sink(
        root,
        target,
        cli="claude",
        requested_model="alias",
        phase="develop",
        mode="one_shot",
        **kwargs,
    )


def test_known_zero_is_distinct_from_unknown_across_calls(tmp_path, capsys, monkeypatch):
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({"iteration": 1}))
    writer = sink(tmp_path, target)
    writer(
        (
            TransportResult(
                reported_model="actual", usage=TokenUsage(input_tokens=0, total_cost_usd=0)
            ),
        )
    )
    writer((TransportResult(reported_model="actual", usage=TokenUsage(input_tokens=3)),))
    (group,) = json.loads(target.read_text())["chat_usage"]
    assert group["stats"] == {"input_tokens": 3, "total_cost_usd": 0}
    assert group["calls"] == 2 and group["incomplete_calls"] == 2
    assert "total_cost_usd" in group["unknown_fields"]
    monkeypatch.setattr(status_display, "RICH_AVAILABLE", False)
    status_display.StatusDisplay().render_chat_usage_table([group])
    output = capsys.readouterr().out
    assert "unknown" in output and "partial" in output and "actual" in output


def test_all_known_zero_usage_can_have_complete_coverage(tmp_path):
    target = tmp_path / "iteration.json"
    target.write_text(json.dumps({"iteration": 1}))
    sink(tmp_path, target)(
        (
            TransportResult(
                reported_model="actual", usage=TokenUsage(**dict.fromkeys(CHAT_USAGE_FIELDS, 0))
            ),
        )
    )
    (group,) = json.loads(target.read_text())["chat_usage"]
    assert group["unknown_fields"] == [] and group["incomplete_calls"] == 0


@pytest.mark.parametrize("alias", ["cache_creation_input_tokens", "cache_write_input_tokens"])
def test_cache_write_alias_is_one_explicit_counter(tmp_path, alias):
    target = tmp_path / "iteration.json"
    target.write_text('{"iteration":1}')
    values = dict.fromkeys(CHAT_USAGE_FIELDS, 0)
    values.pop(
        "cache_write_input_tokens"
        if alias == "cache_creation_input_tokens"
        else "cache_creation_input_tokens"
    )
    sink(tmp_path, target)([TransportResult(reported_model="actual", usage=TokenUsage(**values))])
    (group,) = json.loads(target.read_text())["chat_usage"]
    assert group["unknown_fields"] == []
    assert group["incomplete_calls"] == 0
    assert group["stats"]["cache_write_input_tokens"] == 0
    assert "cache_creation_input_tokens" not in group["stats"]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("second", [3, 5])
@pytest.mark.parametrize("native", [False, True])
def test_cache_alias_switch_accumulates_every_call(tmp_path, reverse, second, native):
    from datetime import datetime, timezone

    from cafe.agents.cli.codex_subagent_usage import NativeInterval
    from tests.unit.test_workflow_cost_summary import record

    target = tmp_path / "iteration.json"
    target.write_text('{"iteration":1}')
    writer = sink(tmp_path, target)
    aliases = ["cache_creation_input_tokens", "cache_write_input_tokens"]
    if reverse:
        aliases.reverse()
    for index, (alias, value) in enumerate(zip(aliases, [3, second])):
        values = dict(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=0,
            reasoning_output_tokens=0,
            total_cost_usd=0,
            **{alias: value},
        )
        records = []
        if native:
            scope = NativeInterval(
                tmp_path / "native",
                workflow_id="wf",
                caller_id="custom",
                attempt_id=f"call-{index}",
            )
            identity = f"00000000-0000-4000-8000-00000000000{index}"
            scope.bind(identity)
            records = [
                scope.open_record(
                    datetime.now(timezone.utc), final=True, gaps=["native_discovery_bound"]
                ),
                record(
                    f"call-{index}",
                    "0",
                    model="actual",
                    session_id=identity,
                    usage={k: v for k, v in values.items() if k != "total_cost_usd"},
                ),
            ]
        writer(
            [
                TransportResult(
                    reported_model="actual", usage=TokenUsage(**values, cost_records=records)
                )
            ]
        )
    data = json.loads(target.read_text())
    (group,) = data["chat_usage"]
    assert group["calls"] == 2
    assert group["stats"]["cache_write_input_tokens"] == 3 + second
    assert data["stats"]["cache_write_input_tokens"] == 3 + second
    assert "cache_creation_input_tokens" not in group["stats"]
    assert not (set(group["unknown_fields"]) & set(aliases))


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_cache_alias_mixed_replay_counts_each_record_once(tmp_path, reverse, native):
    from datetime import datetime, timezone

    from cafe.agents.cli.codex_subagent_usage import NativeInterval
    from tests.unit.test_workflow_cost_summary import record

    target = tmp_path / "iteration.json"
    target.write_text('{"iteration":1}')
    writer = sink(tmp_path, target)
    aliases = ["cache_creation_input_tokens", "cache_write_input_tokens"]
    if reverse:
        aliases.reverse()
    callers, scopes = [], []
    for index, alias in enumerate(aliases):
        values = dict(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=0,
            reasoning_output_tokens=0,
            **{alias: 3 if index == 0 else 5},
        )
        identity = f"00000000-0000-4000-8000-00000000000{index}"
        callers.append(
            record(f"call-{index}", "1", model="actual", session_id=identity, usage=values)
        )
        if native:
            scope = NativeInterval(
                tmp_path / "native",
                workflow_id="wf",
                caller_id="custom",
                attempt_id=f"call-{index}",
            )
            scope.bind(identity)
            scopes.append(scope.open_record(datetime.now(timezone.utc), final=True, gaps=[]))
        # The second observation repeats A and adds B, with a different scalar alias.
        cumulative = dict(values, input_tokens=10 * (index + 1), output_tokens=2 * (index + 1))
        cumulative[alias] = 3 if index == 0 else 8
        usage = TokenUsage(**cumulative, total_cost_usd=index + 1, cost_records=[*scopes, *callers])
        writer([TransportResult(reported_model="actual", usage=usage)])
    data = json.loads(target.read_text())
    (group,) = data["chat_usage"]
    for stats in (group["stats"], data["stats"]):
        assert stats["input_tokens"] == 20
        assert stats["cache_write_input_tokens"] == 8
        assert stats.get("cache_creation_input_tokens", 0) == 0
        assert stats["total_cost_usd"] == 2
    assert [r for r in group["cost_records"] if "native_usage" not in r] == callers
    assert not (set(group["unknown_fields"]) & set(aliases))


def test_conflicting_cache_write_aliases_do_not_publish_usage(tmp_path):
    target = tmp_path / "iteration.json"
    target.write_text('{"iteration":1}')
    before = target.read_bytes()
    values = dict.fromkeys(CHAT_USAGE_FIELDS, 0)
    values.update(cache_write_input_tokens=2, cache_creation_input_tokens=1)
    with pytest.raises(ValueError, match="conflicting chat cache-write aliases"):
        sink(tmp_path, target)(
            [TransportResult(reported_model="actual", usage=TokenUsage(**values))]
        )
    assert target.read_bytes() == before


def test_issue_metadata_writers_preserve_aliases_readers_and_concurrent_counts(tmp_path):
    target = tmp_path / "issue.yaml"
    target.write_text(yaml.safe_dump({"feature_branch": "x", "other": "kept"}))
    first = sink(tmp_path, target, issue_metadata=True)
    second = sink(tmp_path, target, issue_metadata=True)
    alias = tmp_path / "alias.yaml"
    alias.hardlink_to(target)
    original = target.read_bytes()
    with target.open("rb") as reader:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda writer: writer(
                        (
                            TransportResult(
                                reported_model="actual", usage=TokenUsage(input_tokens=2)
                            ),
                        )
                    ),
                    [first, second],
                )
            )
        assert reader.read() == original and alias.read_bytes() == original
    data = yaml.safe_load(target.read_text())
    assert data["other"] == "kept"
    assert data["chat_usage"][0]["stats"]["input_tokens"] == 4
    assert list(tmp_path.glob(".usage-*")) == []


def test_issue_metadata_identity_and_symlink_admission_are_preserved(tmp_path):
    target = tmp_path / "issue.yaml"
    target.write_text(yaml.safe_dump({"feature_branch": "x"}))
    writer = sink(tmp_path, target, issue_metadata=True)
    target.write_text(yaml.safe_dump({"feature_branch": "y"}))
    with pytest.raises(ValueError):
        writer((TransportResult(),))
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(target)
    with pytest.raises(ValueError):
        sink(tmp_path, alias, issue_metadata=True)


def test_descriptor_lock_serializes_with_existing_settings_writer(tmp_path, monkeypatch):
    import fcntl
    import os
    from threading import Event, get_ident

    from cafe.utils.issue_config import issue_config_lock, write_issue_config_atomic

    target = tmp_path / "issue.yaml"
    target.write_text(yaml.safe_dump({"feature_branch": "x", "unrelated": "kept"}))
    writer = sink(tmp_path, target, issue_metadata=True)
    attempt = Event()
    owner = get_ident()
    original_flock = fcntl.flock
    lock_identity = None

    def observe_lock(descriptor, operation):
        info = os.fstat(descriptor)
        if (
            get_ident() != owner
            and operation == fcntl.LOCK_EX
            and (info.st_dev, info.st_ino) == lock_identity
        ):
            attempt.set()
        return original_flock(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", observe_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with issue_config_lock(target):
            info = target.with_name("issue-settings.lock").stat()
            lock_identity = info.st_dev, info.st_ino
            future = pool.submit(
                writer,
                (TransportResult(reported_model="actual", usage=TokenUsage(input_tokens=2)),),
            )
            assert attempt.wait(2), "chat did not acquire the existing settings lock"
            assert not future.done()
            current = yaml.safe_load(target.read_text())
            current["pr"] = {"auto_create": False}
            write_issue_config_atomic(target, current)
        future.result(timeout=2)
    current = yaml.safe_load(target.read_text())
    assert current["pr"] == {"auto_create": False}
    assert current["unrelated"] == "kept"
    assert current["chat_usage"][0]["stats"]["input_tokens"] == 2
