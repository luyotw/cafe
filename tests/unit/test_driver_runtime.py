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


def _policy(model: str = "exact-driver-model") -> DriverPolicyContract:
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": model},
        }
    )


def _decision(packet, **updates) -> DriverDecision:
    values = {
        "workflow_id": packet.workflow_id,
        "sequence": packet.sequence,
        "requested_action": packet.requested_action,
        "completed_phase": packet.completed_phase,
        "boundary_id": packet.boundary_id,
        "contract_version": packet.contract_version,
        "driver_cli": packet.driver_cli,
        "driver_model": packet.driver_model,
        "action": "advance",
    }
    values.update(updates)
    return DriverDecision(**values)


def _consume_in_process(issue_dir: str, sequence: int, queue) -> None:
    consumed = _coordinator(Path(issue_dir)).consume_authorization(sequence) is not None
    queue.put(consumed)


def test_packets_are_monotonic_structured_and_workflow_correlated(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)

    first = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=_policy()
    )
    second = coordinator.open_boundary(
        completed_phase="plan", requested_action="develop", policy=_policy()
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert first.workflow_id == second.workflow_id == coordinator.state.workflow_id
    assert first.completed_phase == "spec"
    assert first.requested_action == "plan"


def test_decision_must_match_workflow_sequence_and_requested_action(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=_policy()
    )

    with pytest.raises(ValueError):
        coordinator.record_decision(
            _decision(packet, workflow_id="other-workflow")
        )
    with pytest.raises(ValueError):
        coordinator.record_decision(
            _decision(packet, requested_action="review")
        )


def test_one_sequence_retains_one_decision_and_consumes_authorization_once(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=_policy()
    )
    decision = _decision(packet, rationale="continue")

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
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=_policy()
    )
    coordinator.record_decision(_decision(packet))

    def consume() -> bool:
        return _coordinator(tmp_path).consume_authorization(packet.sequence) is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: consume(), range(16)))

    assert results.count(True) == 1


def test_concurrent_processes_cannot_consume_one_authorization_twice(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=_policy()
    )
    coordinator.record_decision(_decision(packet))
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
            completed_phase="spec",
            boundary_id="spec:plan",
            contract_version=2,
            driver_cli="codex",
            driver_model="exact-driver-model",
            action="invented",
        )


@pytest.mark.parametrize(
    (
        "mode",
        "delegated_available",
        "expected_source",
        "expected_return",
        "expected_pause",
    ),
    [
        ("attached", False, "attached", True, False),
        ("unattended", False, "unattended", False, False),
        ("delegated", True, "delegated", False, False),
        ("delegated", False, "delegated_unavailable", True, True),
    ],
)
def test_boundary_resolution_keeps_only_driver_ownership_explicit(
    mode: str,
    delegated_available: bool,
    expected_source: str,
    expected_return: bool,
    expected_pause: bool,
) -> None:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 10
    elif mode == "delegated":
        driver.update({"cli": "codex", "model": "gpt-5.6-codex"})
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": driver,
        }
    )

    resolution = resolve_driver_boundary(policy, delegated_available=delegated_available)

    assert resolution.action_source == expected_source
    assert resolution.return_after_boundary is expected_return
    assert resolution.pause is expected_pause
    assert resolution.requires_decision is (mode == "delegated" and delegated_available)
