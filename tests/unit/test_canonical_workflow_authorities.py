"""New workflow audit and receipt authorities (Plan Unit 1, 2, 3, and 5)."""

import json

import pytest

from cafe.core.audit_events import MAX_EVENT_BYTES
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.services.status_service import StatusService


def test_complete_events_and_receipts_live_outside_the_board(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    message = "long audit detail " * 20000
    store.log_event(state, "spec", "ordinary", message, {"detail": message})
    callback = store.prepare_workflow_callback_event(
        state, {"event_type": "phase_terminal", "step": "spec"}
    )
    store.append_capability_receipt(state, {"capability": "test", "success": True})

    board = json.loads(store.file_path.read_text())
    assert "events" not in board
    assert "capability_receipts" not in board
    reloaded = store.load_or_create("spec")
    assert [(event.event_type, event.message) for event in reloaded.events] == [
        ("ordinary", message),
        ("workflow_event_callback_enqueued", json.dumps(callback, ensure_ascii=False)),
    ]
    assert reloaded.capability_receipts == [{"capability": "test", "success": True}]


def test_exact_callback_identity_uses_canonical_record(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    callback = store.prepare_workflow_callback_event(state, {"event_type": "phase_terminal"})
    assert store.validate_workflow_callback_event(state.workflow_id, callback)
    with pytest.raises(ValueError):
        store.validate_workflow_callback_event(state.workflow_id, {**callback, "event_id": "wrong"})


def test_receipt_append_from_stale_view_preserves_both_writers(tmp_path):
    first = BlackboardStore(tmp_path / "issue")
    original = first.load_or_create("spec")
    stale = BlackboardStore(tmp_path / "issue").load_or_create("spec")
    first.append_capability_receipt(original, {"capability": "a"})
    first.append_capability_receipt(stale, {"capability": "b"})
    assert [r["capability"] for r in first.load_or_create("spec").capability_receipts] == ["a", "b"]


def test_ordinary_status_shows_single_source_callback_failure(tmp_path):
    issue_dir = tmp_path / "issues" / "issue"
    state = BlackboardStore(issue_dir).load_or_create("spec")
    service = StatusService(issues_root=tmp_path / "issues")
    before = service.load_current_state("issue", ["spec"])
    (issue_dir / "status_sources.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workflow_id": state.workflow_id,
                "callback_failure_receipt": "driver/callback_failure_notifications.json",
            }
        )
    )
    (issue_dir / "driver").mkdir()
    (issue_dir / "driver" / "callback_failure_notifications.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow_id": state.workflow_id,
                "records": {
                    "failure": {
                        "occurred_at": "2026-09-28T00:00:00+00:00",
                        "outcome": "disabled",
                        "error_code": "callback_failure",
                    }
                },
            }
        )
    )
    status = service.load_current_state("issue", ["spec"])
    assert status["State"] != before["State"]
    assert "dispatch_state.json" in status["Next"]


def test_ordinary_status_reads_failure_without_large_audit_body(tmp_path, monkeypatch):
    issue_dir = tmp_path / "issues" / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.log_event(state, "spec", "ordinary", "large detail " * 25000)
    ordinary_path = store.audit._path(1)
    (issue_dir / "status_sources.json").write_text(json.dumps({
        "version": 1,
        "workflow_id": state.workflow_id,
        "callback_failure_receipt": "driver/callback_failure_notifications.json",
    }))
    (issue_dir / "driver").mkdir()
    (issue_dir / "driver" / "callback_failure_notifications.json").write_text(json.dumps({
        "schema_version": 1,
        "workflow_id": state.workflow_id,
        "records": {"failure": {
            "occurred_at": "2026-09-28T00:00:00+00:00",
            "outcome": "pending",
            "error_code": "callback_failure",
        }},
    }))
    original = type(ordinary_path).read_text

    def read_text(path, *args, **kwargs):
        if path == ordinary_path:
            raise AssertionError("ordinary audit body was hydrated")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(ordinary_path), "read_text", read_text)
    status = StatusService(issues_root=tmp_path / "issues").load_current_state(
        "issue", ["spec"]
    )
    assert status["Reason"] == "callback_failure"
    assert "dispatch_state.json" in status["Next"]


def test_ordinary_status_chooses_latest_failure_independent_of_record_key(tmp_path):
    issue_dir = tmp_path / "issues" / "issue"
    state = BlackboardStore(issue_dir).load_or_create("spec")
    (issue_dir / "status_sources.json").write_text(json.dumps({
        "version": 1,
        "workflow_id": state.workflow_id,
        "callback_failure_receipt": "driver/callback_failure_notifications.json",
    }))
    (issue_dir / "driver").mkdir()
    (issue_dir / "driver" / "callback_failure_notifications.json").write_text(json.dumps({
        "schema_version": 1,
        "workflow_id": state.workflow_id,
        "records": {
            "z_older": {
                "occurred_at": "2026-09-28T00:00:00+00:00",
                "outcome": "disabled",
                "error_code": "older_failure",
            },
            "a_newer": {
                "occurred_at": "2026-09-28T00:01:00+00:00",
                "outcome": "pending",
                "error_code": "newer_failure",
            },
        },
    }, sort_keys=True))
    status = StatusService(issues_root=tmp_path / "issues").load_current_state(
        "issue", ["spec"]
    )
    assert status["Reason"] == "newer_failure"


def test_committed_transition_replays_once_after_board_write_interruption(tmp_path, monkeypatch):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    state.current_step = "develop"
    original = store._save_unlocked

    def interrupted(_state):
        raise OSError("interrupted board replacement")

    monkeypatch.setattr(store, "_save_unlocked", interrupted)
    with pytest.raises(OSError):
        store.log_event(state, "spec", "transition", "to develop", {"next": "develop"})
    monkeypatch.setattr(store, "_save_unlocked", original)
    recovered = store.load_or_create("spec")
    assert recovered.current_step == "develop"
    assert [event.event_type for event in recovered.events] == ["transition"]
    assert store.load_or_create("spec").applied_event_sequence == recovered.applied_event_sequence


def test_interruption_before_event_commit_leaves_no_transition(tmp_path, monkeypatch):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    state.current_step = "develop"

    def interrupted(*_args):
        raise OSError("interrupted event replacement")

    monkeypatch.setattr(store.audit, "commit", interrupted)
    with pytest.raises(OSError):
        store.log_event(state, "spec", "transition", "to develop")
    reloaded = BlackboardStore(store.issue_dir).load_or_create("spec")
    assert reloaded.current_step == "spec"
    assert reloaded.events == []


def test_conflicting_committed_transition_fails_closed(tmp_path, monkeypatch):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    state.current_step = "develop"

    def interrupted(_state):
        raise OSError("interrupted board replacement")

    monkeypatch.setattr(store, "_save_unlocked", interrupted)
    with pytest.raises(OSError):
        store.log_event(state, "spec", "transition", "to develop")
    raw = json.loads(store.file_path.read_text())
    raw["current_step"] = "unrelated"
    store.file_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        BlackboardStore(store.issue_dir).load_or_create("spec")


def test_committed_transition_replays_baton_before_board_checkpoint(tmp_path, monkeypatch):
    import cafe.core.blackboard as blackboard_module

    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    contract = store.build_handoff_contract(
        from_step="spec", to_owner=HandoffOwner.AGENT, to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
    )
    state.current_step = "develop"
    state.handoff_contract = contract
    original_write = blackboard_module.atomic_write_bytes

    def interrupted(path, content):
        if path == store.next_step_path:
            raise OSError("interrupted baton replacement")
        return original_write(path, content)

    monkeypatch.setattr(blackboard_module, "atomic_write_bytes", interrupted)
    with pytest.raises(OSError):
        store.record_event(
            state, "transition", {"step": "spec", "to": "develop"},
            baton_contract=contract,
        )
    monkeypatch.setattr(blackboard_module, "atomic_write_bytes", original_write)
    recovered = BlackboardStore(store.issue_dir).load_or_create("spec")
    assert recovered.current_step == "develop"
    assert recovered.handoff_contract.to_step == "develop"
    assert json.loads(store.next_step_path.read_text())["to_step"] == "develop"


def test_transition_replays_after_baton_commit_before_board_checkpoint(tmp_path, monkeypatch):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    contract = store.build_handoff_contract(
        from_step="spec", to_owner=HandoffOwner.AGENT, to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
    )
    state.current_step = "develop"
    state.handoff_contract = contract

    def interrupted(_state):
        raise OSError("interrupted board replacement")

    monkeypatch.setattr(store, "_save_unlocked", interrupted)
    with pytest.raises(OSError):
        store.record_event(
            state, "transition", {"step": "spec", "to": "develop"},
            baton_contract=contract,
        )
    recovered = BlackboardStore(store.issue_dir).load_or_create("spec")
    assert recovered.current_step == "develop"
    assert recovered.handoff_contract.to_step == "develop"
    assert json.loads(store.next_step_path.read_text())["to_step"] == "develop"


def test_reserved_sequence_gap_does_not_hide_later_event(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    gap = store.audit.reserve(state.workflow_id)
    store.log_event(state, "spec", "after_gap", "kept")
    assert [event.event_type for event in store.load_or_create("spec").events] == ["after_gap"]
    assert store.audit.read(state.workflow_id, gap) is None


def test_receipt_transaction_allows_hook_facade_in_same_process(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    with store.capability_receipt_transaction(state):
        BlackboardStore(store.issue_dir).append_capability_receipt(
            state, {"capability": "hook", "success": True}
        )
    assert store.load_or_create("spec").capability_receipts == [
        {"capability": "hook", "success": True}
    ]


def test_missing_receipt_authority_after_audit_activity_fails_closed(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    store.record_event(state, "capability_receipt", {"capability": "test", "success": True})
    store.receipts_path.unlink()
    with pytest.raises(ValueError):
        store.load_or_create("spec")


def test_board_save_cannot_replace_authoritative_receipts(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    store.append_capability_receipt(state, {"capability": "real", "success": True})
    state.capability_receipts = [{"capability": "fake", "success": True}]
    store.save(state)
    assert store.load_or_create("spec").capability_receipts == [
        {"capability": "real", "success": True}
    ]


def test_ordinary_status_shows_pre_dispatch_audit_failure(tmp_path, monkeypatch):
    issue_dir = tmp_path / "issues" / "issue"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    service = StatusService(issues_root=tmp_path / "issues")
    before = service.load_current_state("issue", ["spec"])
    store.log_event(state, "spec", "ordinary", "large detail " * 25000)
    ordinary_path = store.audit._path(1)
    store.record_event(
        state,
        "workflow_event_callback_dispatch_failed",
        {
            "event_type": "phase_terminal",
            "error": "TimeoutError",
        },
    )
    original = type(ordinary_path).read_text

    def read_text(path, *args, **kwargs):
        if path == ordinary_path:
            raise AssertionError("ordinary audit body was hydrated")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(ordinary_path), "read_text", read_text)
    status = service.load_current_state("issue", ["spec"])
    assert status["State"] != before["State"]
    assert "audit_events" in status["Next"]


def test_callback_rejects_symlinked_and_mismatched_canonical_record(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    event = store.prepare_workflow_callback_event(state, {"event_type": "phase_terminal"})
    path = store.audit._path(event["sequence"])
    original = path.read_bytes()
    path.unlink()
    target = tmp_path / "outside.json"
    target.write_bytes(original)
    path.symlink_to(target)
    with pytest.raises(ValueError):
        store.validate_workflow_callback_event(state.workflow_id, event)
    path.unlink()
    path.write_bytes(original)
    with pytest.raises(ValueError):
        store.validate_workflow_callback_event(state.workflow_id, {**event, "occurred_at": "wrong"})


def test_failure_source_rejects_another_workflow(tmp_path):
    issue_dir = tmp_path / "issues" / "issue"
    BlackboardStore(issue_dir).load_or_create("spec")
    service = StatusService(issues_root=tmp_path / "issues")
    before = service.load_current_state("issue", ["spec"])
    (issue_dir / "status_sources.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workflow_id": "another-workflow",
                "callback_failure_receipt": "driver/callback_failure_notifications.json",
            }
        )
    )
    status = service.load_current_state("issue", ["spec"])
    assert status["State"] != before["State"]
    assert "Next" in status


def test_resume_selection_skips_large_ordinary_body(tmp_path, monkeypatch):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    store.log_event(state, "spec", "large_history", "complete detail " * 20000)
    callback = store.prepare_workflow_callback_event(state, {"event_type": "phase_terminal"})
    original_read = store.audit.read

    def bounded_read(workflow_id, sequence, **kwargs):
        assert sequence != 1
        assert kwargs.get("bounded") is True
        return original_read(workflow_id, sequence, **kwargs)

    monkeypatch.setattr(store.audit, "read", bounded_read)
    selected = list(
        store.audit.iter_open_callbacks(
            state.workflow_id, store.audit.high_water(state.workflow_id)
        )
    )
    assert [record["event_id"] for record in selected] == [callback["event_id"]]


def test_callback_marker_close_stays_within_bounded_record(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("spec")
    sequence = store.audit.reserve(state.workflow_id)
    record = {
        "workflow_id": state.workflow_id,
        "event_id": "boundary-event",
        "sequence": sequence,
        "timestamp": "2026-09-28T00:00:00+00:00",
        "step": "spec",
        "event_type": "workflow_event_callback_enqueued",
        "message": "",
        "data": {},
        "patch": {},
        "delivery": "open",
    }
    empty_size = len(store.audit._event_bytes(record))
    record["message"] = "x" * (MAX_EVENT_BYTES - empty_size - 2)
    store.audit.commit(state.workflow_id, record)
    store.audit.close(state.workflow_id, sequence)
    assert store.audit.read(state.workflow_id, sequence, bounded=True)["delivery"] == "closed"
