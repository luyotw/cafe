"""Blackboard writes preserve concurrent durable workflow state."""

import multiprocessing
import time
from pathlib import Path

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
