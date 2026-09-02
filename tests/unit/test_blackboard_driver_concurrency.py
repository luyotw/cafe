"""Blackboard writes preserve concurrent durable workflow state."""

import multiprocessing
import time
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverCoordinator, DriverDecision


def _write_generic_state_without_platform_lock(
    issue_dir_value: str,
    action: str,
    rendezvous: object,
    results: object,
) -> None:
    """Overlap generic production saves after both processes loaded one baseline."""
    import cafe.core.blackboard as blackboard_mod

    blackboard_mod.fcntl = None
    blackboard_mod.msvcrt = None
    original_save = blackboard_mod.BlackboardStore._save_unlocked

    def _slow_save(store: BlackboardStore, state: object) -> None:
        time.sleep(0.1)
        original_save(store, state)

    blackboard_mod.BlackboardStore._save_unlocked = _slow_save
    try:
        store = BlackboardStore(Path(issue_dir_value))
        state = store.load_or_create("spec")
        rendezvous.wait(timeout=10)
        if action == "advance":
            store.set_current_step(state, "plan")
        else:
            store.record_event(state, action, {"step": "spec", "source": action})
    except BaseException as exc:
        results.put(("error", repr(exc)))
        return
    results.put(("ok", action))


def _write_generic_event_with_mixed_platform_lock(
    issue_dir_value: str,
    event_type: str,
    lock_route: str,
    states_loaded: object,
    native_save_entered: object,
    fallback_save_attempted: object,
    fallback_save_entered: object,
    release_native_save: object,
    results: object,
) -> None:
    """Exercise one native-success writer beside one native-failure writer."""
    import cafe.core.blackboard as blackboard_mod

    store = BlackboardStore(Path(issue_dir_value))
    state = store.load_or_create("spec")
    original_acquire = blackboard_mod._acquire_process_file_lock
    original_save = blackboard_mod.BlackboardStore._save_unlocked

    if lock_route == "fallback":

        def _fail_platform_lock(_lock_file: object) -> None:
            raise OSError("simulated per-writer platform lock failure")

        blackboard_mod._acquire_process_file_lock = _fail_platform_lock

    def _observed_save(save_store: BlackboardStore, save_state: object) -> None:
        if lock_route == "native":
            native_save_entered.set()
            if not release_native_save.wait(timeout=10):
                raise TimeoutError("native save was not released")
        else:
            fallback_save_entered.set()
        original_save(save_store, save_state)

    blackboard_mod.BlackboardStore._save_unlocked = _observed_save
    try:
        states_loaded.wait(timeout=10)
        if lock_route == "fallback":
            if not native_save_entered.wait(timeout=10):
                raise TimeoutError("native save did not enter its persistence span")
            fallback_save_attempted.set()
        store.record_event(state, event_type, {"step": "spec", "source": lock_route})
    except BaseException as exc:
        results.put(("error", repr(exc)))
        return
    finally:
        blackboard_mod._acquire_process_file_lock = original_acquire
        blackboard_mod.BlackboardStore._save_unlocked = original_save
    results.put(("ok", event_type))


def _hold_strict_driver_transaction(
    issue_dir_value: str,
    transaction_entered: object,
    release_transaction: object,
    results: object,
) -> None:
    """Commit strict driver state after a coordinated generic writer attempts a save."""
    store = BlackboardStore(Path(issue_dir_value))
    state = store.load_or_create("spec")
    try:
        with store.driver_transaction(state) as persisted:
            persisted.driver_state["mixed_lock_probe"] = {"committed": True}
            transaction_entered.set()
            if not release_transaction.wait(timeout=10):
                raise TimeoutError("strict driver transaction was not released")
    except BaseException as exc:
        results.put(("error", repr(exc)))
        return
    results.put(("ok", "driver"))


def _write_generic_state_beside_strict_transaction(
    issue_dir_value: str,
    generic_action: str,
    state_loaded: object,
    transaction_entered: object,
    save_attempted: object,
    save_entered: object,
    results: object,
) -> None:
    """Exercise a native-failure generic save through event and receipt callers."""
    import cafe.core.blackboard as blackboard_mod

    store = BlackboardStore(Path(issue_dir_value))
    state = store.load_or_create("spec")
    original_acquire = blackboard_mod._acquire_process_file_lock
    original_save = blackboard_mod.BlackboardStore._save_unlocked

    def _fail_state_file_lock(lock_file: object) -> object:
        if Path(str(lock_file.name)) == store.state_lock_path:
            raise OSError("simulated generic state-lock failure")
        return original_acquire(lock_file)

    def _observed_save(save_store: BlackboardStore, save_state: object) -> None:
        save_entered.set()
        original_save(save_store, save_state)

    blackboard_mod._acquire_process_file_lock = _fail_state_file_lock
    blackboard_mod.BlackboardStore._save_unlocked = _observed_save
    try:
        state_loaded.set()
        if not transaction_entered.wait(timeout=10):
            raise TimeoutError("strict driver transaction did not enter")
        save_attempted.set()
        if generic_action == "event":
            store.record_event(
                state,
                "mixed_lock_event",
                {"step": "spec", "source": "native_failure"},
            )
        else:
            with store.capability_receipt_transaction(state):
                store.upsert_capability_receipt(
                    state,
                    {
                        "notification_attempt_id": "mixed-lock-receipt",
                        "code": "notification_delivered",
                        "outcome": "success",
                    },
                )
    except BaseException as exc:
        results.put(("error", repr(exc)))
        return
    finally:
        blackboard_mod._acquire_process_file_lock = original_acquire
        blackboard_mod.BlackboardStore._save_unlocked = original_save
    results.put(("ok", generic_action))


def test_generic_save_cannot_clobber_driver_owned_state(tmp_path: Path) -> None:
    store = BlackboardStore(tmp_path)
    stale_generic_state = store.load_or_create("spec")
    driver_state = store.load_or_create("spec")
    coordinator = DriverCoordinator(store, driver_state)
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": "exact-model"},
        }
    )
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=policy
    )
    coordinator.record_decision(
        DriverDecision(
            workflow_id=packet.workflow_id,
            sequence=packet.sequence,
            requested_action=packet.requested_action,
            completed_phase=packet.completed_phase,
            boundary_id=packet.boundary_id,
            contract_version=packet.contract_version,
            driver_cli=packet.driver_cli,
            driver_model=packet.driver_model,
            action="advance",
        )
    )
    assert coordinator.claim_advancement_lease("worker", ttl_seconds=60)
    with store.driver_transaction(driver_state) as persisted:
        persisted.driver_state["notification_guidance"] = {
            "proactive_events": [],
            "inspection_available": True,
            "inspection_command": "cafe status",
        }
    store.upsert_capability_receipt(
        driver_state,
        {
            "notification_attempt_id": "slack-human-task:task-1",
            "code": "slack_notification_delivered",
            "outcome": "success",
        },
    )
    expected_driver_state = store.load_or_create("spec").driver_state
    expected_receipts = store.load_or_create("spec").capability_receipts

    stale_generic_state.current_step = "plan"
    store.save(stale_generic_state)

    reloaded = store.load_or_create("spec")
    assert reloaded.current_step == "plan"
    assert reloaded.driver_state == expected_driver_state
    assert reloaded.capability_receipts == expected_receipts


def test_generic_saves_merge_concurrent_lifecycle_and_pointer_changes(tmp_path: Path) -> None:
    """Unit 7/9 + Integration 5: successful writers retain independent facts."""
    issue_dir = tmp_path / "issue"
    BlackboardStore(issue_dir).load_or_create("spec")
    context = multiprocessing.get_context("spawn")
    actions = ("probe_first", "probe_second", "advance")
    rendezvous = context.Barrier(len(actions))
    results = context.Queue()
    workers = [
        context.Process(
            target=_write_generic_state_without_platform_lock,
            args=(str(issue_dir), action, rendezvous, results),
        )
        for action in actions
    ]

    for process in workers:
        process.start()
    outcomes = [results.get(timeout=15) for _ in workers]
    for process in workers:
        process.join(timeout=15)

    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    event_types = {event.event_type for event in reloaded.events}
    assert all(process.exitcode == 0 for process in workers)
    assert all(status == "ok" for status, _action in outcomes)
    assert reloaded.current_step == "plan"
    assert {"probe_first", "probe_second"} <= event_types


def test_generic_saves_share_lock_when_one_platform_lock_fails(tmp_path: Path) -> None:
    """Unit 7/9 + Integration 5: mixed lock routes cannot overlap saves."""
    issue_dir = tmp_path / "issue"
    BlackboardStore(issue_dir).load_or_create("spec")
    context = multiprocessing.get_context("spawn")
    states_loaded = context.Barrier(2)
    native_save_entered = context.Event()
    fallback_save_attempted = context.Event()
    fallback_save_entered = context.Event()
    release_native_save = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_write_generic_event_with_mixed_platform_lock,
            args=(
                str(issue_dir),
                f"{lock_route}_event",
                lock_route,
                states_loaded,
                native_save_entered,
                fallback_save_attempted,
                fallback_save_entered,
                release_native_save,
                results,
            ),
        )
        for lock_route in ("native", "fallback")
    ]

    try:
        for process in workers:
            process.start()
        assert native_save_entered.wait(timeout=10)
        assert fallback_save_attempted.wait(timeout=10)
        persistence_spans_overlapped = fallback_save_entered.wait(timeout=0.5)
    finally:
        release_native_save.set()
        for process in workers:
            process.join(timeout=15)

    outcomes = [results.get(timeout=5) for _ in workers]
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    event_types = {event.event_type for event in reloaded.events}
    assert not persistence_spans_overlapped
    assert all(process.exitcode == 0 for process in workers)
    assert all(status == "ok" for status, _event_type in outcomes)
    assert {"native_event", "fallback_event"} <= event_types


@pytest.mark.parametrize("generic_action", ["event", "receipt"])
def test_native_failure_generic_save_serializes_with_strict_transaction(
    tmp_path: Path, generic_action: str
) -> None:
    """Unit 7/9/10 + Integration 5/6: every successful writer retains its fact."""
    issue_dir = tmp_path / generic_action
    BlackboardStore(issue_dir).load_or_create("spec")
    context = multiprocessing.get_context("spawn")
    state_loaded = context.Event()
    transaction_entered = context.Event()
    save_attempted = context.Event()
    save_entered = context.Event()
    release_transaction = context.Event()
    results = context.Queue()
    generic_writer = context.Process(
        target=_write_generic_state_beside_strict_transaction,
        args=(
            str(issue_dir),
            generic_action,
            state_loaded,
            transaction_entered,
            save_attempted,
            save_entered,
            results,
        ),
    )
    strict_writer = context.Process(
        target=_hold_strict_driver_transaction,
        args=(str(issue_dir), transaction_entered, release_transaction, results),
    )

    try:
        generic_writer.start()
        assert state_loaded.wait(timeout=10)
        strict_writer.start()
        assert transaction_entered.wait(timeout=10)
        assert save_attempted.wait(timeout=10)
        persistence_spans_overlapped = save_entered.wait(timeout=0.5)
    finally:
        release_transaction.set()
        generic_writer.join(timeout=15)
        strict_writer.join(timeout=15)

    outcomes = [results.get(timeout=5) for _ in range(2)]
    reloaded = BlackboardStore(issue_dir).load_or_create("spec")
    assert not persistence_spans_overlapped
    assert generic_writer.exitcode == 0
    assert strict_writer.exitcode == 0
    assert all(status == "ok" for status, _action in outcomes)
    assert reloaded.driver_state["mixed_lock_probe"] == {"committed": True}
    if generic_action == "event":
        assert any(event.event_type == "mixed_lock_event" for event in reloaded.events)
    else:
        assert any(
            receipt.get("notification_attempt_id") == "mixed-lock-receipt"
            for receipt in reloaded.capability_receipts
        )
