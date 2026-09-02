"""Tests for the temporary outer delegated controller."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.workflow_models import PlaybookRunResult
from cafe.orchestration.delegated_controller import DelegatedWorkflowController
from cafe.orchestration.driver_policy import DriverPolicyContract
from cafe.orchestration.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverModelMismatchError,
    DriverUnavailableError,
)


class FakePhaseRuntime:
    steps = {"spec": {}, "plan": {}}

    def __init__(self, issue_dir: Path) -> None:
        self.blackboard_store = BlackboardStore(issue_dir)
        self.blackboard = self.blackboard_store.load_or_create("spec")
        self.executed: list[str] = []

    def run(self, *, start_step=None, single_step=False, **_kwargs) -> PlaybookRunResult:
        assert single_step is True
        step = start_step or self.blackboard.current_step
        self.executed.append(step)
        if step == "spec":
            self.blackboard_store.set_current_step(self.blackboard, "plan")
            return PlaybookRunResult(
                final_step="spec", final_status_code="ready_for_plan", completed=False
            )
        self.blackboard_store.set_current_step(self.blackboard, "done")
        return PlaybookRunResult(
            final_step="plan", final_status_code="workflow_complete", completed=True
        )


class FakeHumanTaskRuntime(FakePhaseRuntime):
    def run(self, *, start_step=None, single_step=False, **_kwargs) -> PlaybookRunResult:
        assert single_step is True
        self.executed.append(start_step or self.blackboard.current_step)
        self.blackboard_store.set_current_step(self.blackboard, "user")
        return PlaybookRunResult(
            final_step="spec", final_status_code="HUMAN_TASK_PENDING", completed=False
        )


class FailingPhaseRuntime(FakePhaseRuntime):
    def run(self, **_kwargs) -> PlaybookRunResult:
        raise ValueError("core phase failure")


class AfterPointerCompletionRuntime(FakePhaseRuntime):
    """Model core recovery after the completion pointer, before its baton."""

    def __init__(self, issue_dir: Path) -> None:
        super().__init__(issue_dir)
        self.blackboard_store.set_current_step(self.blackboard, "done")

    def run(self, **_kwargs) -> PlaybookRunResult:
        return PlaybookRunResult(
            final_step="spec",
            final_status_code="workflow_complete",
            completed=True,
            detail="completion-458",
        )


def _policy(model: str = "gpt-5.6-codex") -> DriverPolicyContract:
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": model},
        }
    )


def _decision(packet, *, action: str = "advance") -> DriverDecision:
    return DriverDecision(
        workflow_id=packet.workflow_id,
        sequence=packet.sequence,
        requested_action=packet.requested_action,
        completed_phase=packet.completed_phase,
        boundary_id=packet.boundary_id,
        contract_version=packet.contract_version,
        driver_cli=packet.driver_cli,
        driver_model=packet.driver_model,
        action=action,
    )


def test_delegated_controller_gates_each_eligible_successor(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    decisions: list[int] = []

    result = DelegatedWorkflowController(
        runtime,
        _policy(),
        delegated_decision_provider=lambda packet: decisions.append(packet.sequence)
        or _decision(packet),
    ).run()

    assert result.completed is True
    assert runtime.executed == ["spec", "plan"]
    assert decisions == [1]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == [1]


def test_delegated_controller_stops_at_human_task_without_a_packet(tmp_path: Path) -> None:
    runtime = FakeHumanTaskRuntime(tmp_path)

    result = DelegatedWorkflowController(runtime, _policy()).run()

    assert result.final_status_code == "HUMAN_TASK_PENDING"
    assert runtime.executed == ["spec"]
    assert runtime.blackboard_store.load_or_create("spec").driver_state.get("packets", {}) == {}


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (DriverUnavailableError("unavailable"), "delegated_driver_unavailable"),
        (DriverModelMismatchError("wrong model"), "delegated_model_mismatch"),
        (ValueError("invalid response"), "delegated_invalid_decision"),
    ],
)
def test_delegated_transport_failures_pause_without_fallback(
    tmp_path: Path, failure: Exception, reason: str
) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    result = DelegatedWorkflowController(
        runtime,
        _policy(),
        delegated_decision_provider=lambda _packet: (_ for _ in ()).throw(failure),
    ).run()

    assert result.final_status_code == "DELEGATED_DRIVER_PAUSED"
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["pause_reason"] == reason
    assert "fallback_reason" not in state.driver_state


@pytest.mark.parametrize(
    ("restart_stage", "provider_calls"),
    [("packet", 1), ("decision", 0), ("consumed", 0)],
)
def test_restart_reuses_a_delegated_boundary_at_most_once(
    tmp_path: Path, restart_stage: str, provider_calls: int
) -> None:
    staged = FakePhaseRuntime(tmp_path)
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    packet = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="transition:stable:plan",
        policy=_policy(),
    )
    decision = _decision(packet)
    if restart_stage in {"decision", "consumed"}:
        coordinator.record_decision(decision)
    if restart_stage == "consumed":
        assert coordinator.consume_authorization(packet.sequence) is not None

    resumed = FakePhaseRuntime(tmp_path)
    provider_seen: list[int] = []
    result = DelegatedWorkflowController(
        resumed,
        _policy(),
        delegated_decision_provider=lambda pending: provider_seen.append(pending.sequence)
        or decision,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert len(provider_seen) == provider_calls
    assert resumed.blackboard_store.load_or_create("spec").driver_state["consumed_sequences"] == [
        packet.sequence
    ]


def test_restart_recreates_one_missing_boundary_from_transition_identity(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    runtime.blackboard_store.record_event(
        runtime.blackboard,
        "transition",
        {
            "transition_id": "transition-458",
            "from": "spec",
            "to": "plan",
            "status_code": "ready_for_plan",
        },
    )
    runtime.blackboard_store.set_current_step(runtime.blackboard, "plan")
    resumed = FakePhaseRuntime(tmp_path)
    packets = []

    result = DelegatedWorkflowController(
        resumed,
        _policy(),
        delegated_decision_provider=lambda packet: packets.append(packet) or _decision(packet),
    ).run()

    assert result.completed is True
    assert [packet.boundary_id for packet in packets] == ["transition:transition-458:plan"]


def test_pending_gate_blocks_before_phase_execution(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    coordinator = DriverCoordinator(runtime.blackboard_store, runtime.blackboard)
    coordinator.open_boundary(
        completed_phase="prior",
        requested_action="spec",
        boundary_id="transition:already-open:spec",
        policy=_policy(),
    )

    result = DelegatedWorkflowController(runtime, _policy()).run()

    assert result.final_status_code == "DELEGATED_DRIVER_PAUSED"
    assert runtime.executed == []


def test_policy_authority_spans_delegated_phase_execution(tmp_path: Path) -> None:
    policy = _policy()
    authority_held = False
    observations: list[bool] = []

    @contextmanager
    def authority():
        nonlocal authority_held
        authority_held = True
        try:
            yield policy
        finally:
            authority_held = False

    class ObservingRuntime(FakePhaseRuntime):
        def run(self, **kwargs) -> PlaybookRunResult:
            observations.append(authority_held)
            return super().run(**kwargs)

    runtime = ObservingRuntime(tmp_path)
    DelegatedWorkflowController(
        runtime,
        policy,
        delegated_decision_provider=_decision,
        policy_authority=authority,
    ).run()

    assert observations == [True, True]


def test_changed_policy_pauses_without_phase_execution(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    @contextmanager
    def replacement_authority():
        yield _policy("replacement-model")

    result = DelegatedWorkflowController(
        runtime,
        _policy(),
        delegated_decision_provider=_decision,
        policy_authority=replacement_authority,
    ).run()

    assert result.final_status_code == "DRIVER_POLICY_PAUSED"
    assert runtime.executed == []


def test_core_phase_error_is_not_relabelled_as_policy_invalidation(tmp_path: Path) -> None:
    runtime = FailingPhaseRuntime(tmp_path)

    with pytest.raises(ValueError, match="core phase failure"):
        DelegatedWorkflowController(
            runtime,
            _policy(),
            delegated_decision_provider=_decision,
        ).run()


def test_completion_replay_after_pointer_does_not_open_a_delegated_gate(tmp_path: Path) -> None:
    runtime = AfterPointerCompletionRuntime(tmp_path)
    decisions: list[object] = []

    result = DelegatedWorkflowController(
        runtime,
        _policy(),
        delegated_decision_provider=lambda packet: decisions.append(packet) or _decision(packet),
    ).run()

    assert result.completed is True
    assert decisions == []
    assert runtime.blackboard.driver_state.get("packets", {}) == {}
