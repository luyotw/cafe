"""U1/U2/U4/U6/U7: identity-bound retained accounting survives closeout."""

import json
import shutil
from decimal import Decimal

import pytest

from cafe.core.types import TokenUsage
from cafe.manager.costs import (
    CostStore,
    inclusive_report,
    manager_usage_sink,
    preserve_worker_cost,
    worker_cost,
)
from tests.fixtures.manager_chat import repository
from tests.unit.test_workflow_cost_summary import record


def cost_journey(tmp_path):
    root = repository(tmp_path / "repo")
    issue = root / ".cafe/issues/topic"
    issue.mkdir(parents=True)
    (issue / "blackboard.json").write_text(json.dumps({"workflow_id": "wf"}))
    from cafe.core.audit_events import AuditEventStore

    AuditEventStore(issue).initialize("wf")
    phase = issue / "custom" / "iteration_001"
    phase.mkdir(parents=True)
    (phase / "iteration.json").write_text(
        json.dumps({"stats": {"cost_records": [record("worker")], "total_cost_usd": 1.5}})
    )
    return root, issue


def test_manager_sink_is_independent_deduplicated_and_retained(tmp_path):
    root, issue = cost_journey(tmp_path)
    sink = manager_usage_sink(root, "topic", "wf", "callback-event")
    usage = TokenUsage(cost_records=[record("manager", "2")])
    sink(usage)
    sink(usage)
    preserve_worker_cost(root, issue, "topic", "wf")
    shutil.rmtree(issue)
    sink(TokenUsage(cost_records=[record("later", "3", complete=False)]))
    sink.gap("native-turn")
    result = inclusive_report(root, "topic", "wf")
    assert result["worker"]["known"] == Decimal("1.5")
    assert result["manager"]["known"] == Decimal("5")
    assert result["combined"]["known"] == Decimal("6.5")
    assert result["combined"]["incomplete"]
    assert result["captured_at"].endswith("+00:00")
    assert "completed_at" not in json.dumps(CostStore(root, "topic", "wf").read())


def test_tagged_overlapping_manager_is_excluded_and_ambiguous_residual_withheld(tmp_path):
    root, issue = cost_journey(tmp_path)
    path = issue / "custom/iteration_001/iteration.json"
    path.write_text(
        json.dumps(
            {
                "stats": {
                    "cost_records": [record("worker"), record("manager", "2", actor="manager")],
                    "total_cost_usd": 3.5,
                }
            }
        )
    )
    result = worker_cost(root, issue, "topic", "wf")
    assert result["known"] == Decimal("1")
    assert result["incomplete"]


def test_wrong_workflow_cannot_read_or_replace_snapshot(tmp_path):
    root, issue = cost_journey(tmp_path)
    with pytest.raises(ValueError):
        preserve_worker_cost(root, issue, "topic", "other")
    for name in ("../bad", "", "bad/name"):
        with pytest.raises(ValueError):
            CostStore(root, "topic", name)


@pytest.mark.parametrize("damage", ["symlink", "identity", "corrupt", "oversize"])
def test_unsafe_retained_evidence_is_rejected_losslessly(tmp_path, damage, monkeypatch):
    root, issue = cost_journey(tmp_path)
    preserve_worker_cost(root, issue, "topic", "wf")
    store = CostStore(root, "topic", "wf")
    if damage == "symlink":
        other = tmp_path / "other"
        store.path.rename(other)
        store.path.symlink_to(other)
    elif damage == "identity":
        data = json.loads(store.path.read_text())
        data["identity"]["workflow_id"] = "other"
        store.path.write_text(json.dumps(data))
    elif damage == "corrupt":
        store.path.write_text("broken")
    else:
        monkeypatch.setattr("cafe.manager.costs.MAX_ACCOUNTING_BYTES", 4)
    with pytest.raises(ValueError):
        store.read()


def test_source_precedence_never_adds_snapshot_and_archive_twice(tmp_path, monkeypatch):
    root, issue = cost_journey(tmp_path)
    preserve_worker_cost(root, issue, "topic", "wf")
    from pathlib import Path

    from cafe.manager.costs import _archive

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    archive = _archive(root, "topic")
    archive.parent.mkdir(parents=True)
    shutil.move(issue, archive)
    result = inclusive_report(root, "topic", "wf", issue_dir=archive)
    assert result["worker"]["known"] == Decimal("1.5")
    assert result["manager"]["incomplete"]
    assert result["combined"]["incomplete"]


def test_snapshot_boundary_excludes_later_records_and_missing_usage(tmp_path):
    root, issue = cost_journey(tmp_path)
    preserve_worker_cost(root, issue, "topic", "wf")
    sink = manager_usage_sink(root, "topic", "wf", "chat")
    sink.gap("missing")
    first = inclusive_report(root, "topic", "wf")
    sink(TokenUsage(cost_records=[record("later", "2")]))
    second = inclusive_report(root, "topic", "wf")
    assert first["manager"]["known"] == 0
    assert second["manager"]["known"] == 2
    assert first["combined"]["incomplete"]


def test_callback_public_sink_and_transport_keep_failed_and_unknown_attempts(tmp_path):
    from cafe.manager.costs import accounted_call
    from tests.fixtures.manager_chat import adapter

    root, issue = cost_journey(tmp_path)
    module = adapter("workflow_event_callback")
    sink = module._callback_usage_sink(
        issue / "manager", {"workflow_id": "wf", "event_id": "event"}, root
    )

    def failed(**kwargs):
        kwargs["on_usage"](TokenUsage(cost_records=[record("failed", "2", complete=False)]))
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError):
        accounted_call(sink, "callback:event:failed", failed, on_usage=sink)
    accounted_call(sink, "callback:event:unknown", lambda: None)
    assert worker_cost(root, issue, "topic", "wf")["known"] == Decimal("1.5")
    preserve_worker_cost(root, issue, "topic", "wf")
    result = inclusive_report(root, "topic", "wf")
    assert result["manager"]["known"] == 2
    assert result["manager"]["incomplete"]


def test_historical_callback_blend_cannot_be_certified_worker_only(tmp_path):
    root, issue = cost_journey(tmp_path)
    manager = issue / "manager"
    manager.mkdir()
    (manager / "dispatch_state.json").write_text(
        json.dumps(
            {
                "workflow_id": "wf",
                "events": {
                    "old": {"event": {"step": "custom"}, "attempts": [{"status": "accepted"}]}
                },
            }
        )
    )
    result = worker_cost(root, issue, "topic", "wf")
    assert result["known"] == 0
    assert result["incomplete"]


def test_current_missing_source_uses_retained_alternative_as_partial(tmp_path):
    root, issue = cost_journey(tmp_path)
    preserve_worker_cost(root, issue, "topic", "wf")
    (issue / "custom/iteration_001/iteration.json").unlink()
    result = inclusive_report(root, "topic", "wf", issue_dir=issue)
    assert result["worker"]["known"] == Decimal("1.5")
    assert result["worker"]["incomplete"]


def test_repeated_concurrent_usage_keeps_all_distinct_calls_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root, issue = cost_journey(tmp_path)

    def persist(index):
        sink = manager_usage_sink(root, "topic", "wf", f"chat:{index % 4}")
        sink(TokenUsage(cost_records=[record(f"manager:{index % 4}")]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(persist, range(12)))
    preserve_worker_cost(root, issue, "topic", "wf")
    assert inclusive_report(root, "topic", "wf")["manager"]["known"] == 4


def test_current_source_cannot_cross_project_identity(tmp_path):
    root, issue = cost_journey(tmp_path)
    other_root = repository(tmp_path / "other")
    with pytest.raises(ValueError):
        worker_cost(other_root, issue, "topic", "wf")


def test_source_version_change_retries_only_the_read(tmp_path, monkeypatch):
    import cafe.services.cost_summary as view

    root, issue = cost_journey(tmp_path)
    original = view.read_accounting_file
    source_path = issue / "custom/iteration_001/iteration.json"
    changed = []

    def read(path):
        value = original(path)
        if path == source_path and not changed:
            changed.append(True)
            source_path.write_text(json.dumps({"stats": {"cost_records": [record("fresh", "2")]}}))
        return value

    monkeypatch.setattr(view, "read_accounting_file", read)
    assert worker_cost(root, issue, "topic", "wf")["known"] == 2


def test_identified_historical_manager_cost_enters_inclusive_breakdown(tmp_path):
    root, issue = cost_journey(tmp_path)
    source_path = issue / "custom/iteration_001/iteration.json"
    source_path.write_text(
        json.dumps(
            {"stats": {"cost_records": [record("worker"), record("tagged", "2", actor="manager")]}}
        )
    )
    result = inclusive_report(root, "topic", "wf", issue_dir=issue)
    assert result["worker"]["known"] == 1
    assert result["manager"]["known"] == 2
    assert result["combined"]["known"] == 3


def test_new_host_gap_does_not_quarantine_unrelated_worker_money(tmp_path):
    root, issue = cost_journey(tmp_path)
    manager_usage_sink(root, "topic", "wf", "callback:new").gap("callback:new:host")
    manager = issue / "manager"
    manager.mkdir()
    (manager / "dispatch_state.json").write_text(
        json.dumps(
            {
                "workflow_id": "wf",
                "events": {
                    "new": {"event": {"step": "custom"}, "attempts": [{"status": "accepted"}]}
                },
            }
        )
    )
    assert worker_cost(root, issue, "topic", "wf")["known"] == Decimal("1.5")


def test_snapshot_invalid_cutoff_is_a_visible_validation_error(tmp_path):
    root, issue = cost_journey(tmp_path)
    preserve_worker_cost(root, issue, "topic", "wf")
    store = CostStore(root, "topic", "wf")
    data = json.loads(store.path.read_text())
    data["worker_snapshot"]["captured_at"] = "2026-10-10T00:00:00"
    store.path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        store.read()
