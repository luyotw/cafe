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
