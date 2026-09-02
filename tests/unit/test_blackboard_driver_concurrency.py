"""Blackboard writes preserve driver-owned durable state."""

from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverCoordinator, DriverDecision


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
