"""Durable driver boundary protocol tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest
from pydantic import ValidationError

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverCoordinator, DriverDecision, resolve_driver_boundary


def _coordinator(issue_dir: Path) -> DriverCoordinator:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    return DriverCoordinator(store, state)


def _consume_in_process(issue_dir: str, sequence: int, queue) -> None:
    consumed = _coordinator(Path(issue_dir)).consume_authorization(sequence) is not None
    queue.put(consumed)


def test_packets_are_monotonic_structured_and_workflow_correlated(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)

    first = coordinator.open_boundary(completed_phase="spec", requested_action="plan")
    second = coordinator.open_boundary(completed_phase="plan", requested_action="develop")

    assert first.sequence == 1
    assert second.sequence == 2
    assert first.workflow_id == second.workflow_id == coordinator.state.workflow_id
    assert first.completed_phase == "spec"
    assert first.requested_action == "plan"


def test_decision_must_match_workflow_sequence_and_requested_action(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(completed_phase="spec", requested_action="plan")

    with pytest.raises(ValueError):
        coordinator.record_decision(
            DriverDecision(
                workflow_id="other-workflow",
                sequence=packet.sequence,
                requested_action="plan",
                action="advance",
            )
        )
    with pytest.raises(ValueError):
        coordinator.record_decision(
            DriverDecision(
                workflow_id=packet.workflow_id,
                sequence=packet.sequence,
                requested_action="review",
                action="advance",
            )
        )


def test_one_sequence_retains_one_decision_and_consumes_authorization_once(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(completed_phase="spec", requested_action="plan")
    decision = DriverDecision(
        workflow_id=packet.workflow_id,
        sequence=packet.sequence,
        requested_action=packet.requested_action,
        action="advance",
        rationale="continue",
    )

    assert coordinator.record_decision(decision) == decision
    assert coordinator.record_decision(decision) == decision
    with pytest.raises(ValueError):
        coordinator.record_decision(decision.model_copy(update={"action": "pause"}))
    assert coordinator.consume_authorization(packet.sequence) == decision
    assert coordinator.consume_authorization(packet.sequence) is None

    reloaded = _coordinator(tmp_path)
    assert reloaded.consume_authorization(packet.sequence) is None


def test_concurrent_runtimes_cannot_consume_one_authorization_twice(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(completed_phase="spec", requested_action="plan")
    coordinator.record_decision(
        DriverDecision(
            workflow_id=packet.workflow_id,
            sequence=packet.sequence,
            requested_action=packet.requested_action,
            action="advance",
        )
    )

    def consume() -> bool:
        return _coordinator(tmp_path).consume_authorization(packet.sequence) is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: consume(), range(16)))

    assert results.count(True) == 1


def test_concurrent_processes_cannot_consume_one_authorization_twice(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(completed_phase="spec", requested_action="plan")
    coordinator.record_decision(
        DriverDecision(
            workflow_id=packet.workflow_id,
            sequence=packet.sequence,
            requested_action=packet.requested_action,
            action="advance",
        )
    )
    context = get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_consume_in_process,
            args=(str(tmp_path), packet.sequence, queue),
        )
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert results.count(True) == 1


def test_only_one_runtime_can_claim_advancement_lease(tmp_path: Path) -> None:
    def claim(holder: str) -> bool:
        return _coordinator(tmp_path).claim_advancement_lease(holder, ttl_seconds=60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, [f"worker-{index}" for index in range(8)]))

    assert results.count(True) == 1


def test_driver_decision_rejects_unknown_actions() -> None:
    with pytest.raises(ValidationError):
        DriverDecision(
            workflow_id="workflow",
            sequence=1,
            requested_action="plan",
            action="invented",
        )


@pytest.mark.parametrize(
    ("mode", "delegated_available", "expected_source"),
    [
        ("attached", False, "attached"),
        ("unattended", False, "unattended"),
        ("delegated", True, "delegated"),
        ("delegated", False, "unattended_fallback"),
    ],
)
def test_boundary_resolution_keeps_ownership_and_availability_explicit(
    mode: str, delegated_available: bool, expected_source: str
) -> None:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["attached"] = {"poll_interval_seconds": 10}
    elif mode == "delegated":
        driver["delegated"] = {"cli": "codex", "availability": "best_effort"}
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": driver,
            "execution": {"advancement": "continuous", "hosting": "foreground"},
        }
    )

    resolution = resolve_driver_boundary(policy, delegated_available=delegated_available)

    assert resolution.action_source == expected_source
    assert resolution.pause is False


def test_required_delegated_unavailability_pauses_safely() -> None:
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {
                "mode": "delegated",
                "delegated": {"cli": "codex", "availability": "required"},
            },
            "execution": {"advancement": "continuous", "hosting": "background"},
        }
    )

    resolution = resolve_driver_boundary(policy, delegated_available=False)

    assert resolution.pause is True
    assert resolution.action_source == "delegated_unavailable"
