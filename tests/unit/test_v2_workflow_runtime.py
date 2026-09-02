"""Internal version 2 ownership and advancement runtime tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverModelMismatchError,
    DriverUnavailableError,
)
from cafe.core.v2_workflow_runtime import Version2WorkflowRuntime
from cafe.core.workflow_models import PlaybookRunResult


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


class FakeOperationalStopRuntime(FakePhaseRuntime):
    def __init__(self, issue_dir: Path, status_code: str) -> None:
        super().__init__(issue_dir)
        self.status_code = status_code

    def run(self, *, start_step=None, single_step=False, **_kwargs) -> PlaybookRunResult:
        assert single_step is True
        self.executed.append(start_step or self.blackboard.current_step)
        return PlaybookRunResult(
            final_step="spec", final_status_code=self.status_code, completed=False
        )


class FakeConfirmedPhaseRuntime(FakePhaseRuntime):
    def run(self, *, start_step=None, single_step=False, **_kwargs) -> PlaybookRunResult:
        assert single_step is True
        step = start_step or self.blackboard.current_step
        self.executed.append(step)
        if step == "spec":
            self.blackboard_store.set_current_step(self.blackboard, "plan")
            return PlaybookRunResult(
                final_step="spec", final_status_code="confirmed", completed=False
            )
        self.blackboard_store.set_current_step(self.blackboard, "done")
        return PlaybookRunResult(
            final_step="plan", final_status_code="workflow_complete", completed=True
        )


def _policy(mode: str) -> DriverPolicyContract:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 10
    elif mode == "delegated":
        driver.update({"cli": "codex", "model": "gpt-5.6-codex"})
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": driver,
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


@pytest.mark.parametrize(
    ("mode", "expected_steps", "expects_decision"),
    [
        ("attached", ["spec"], False),
        ("unattended", ["spec", "plan"], False),
        ("delegated", ["spec", "plan"], True),
    ],
)
def test_each_driver_mode_follows_its_promised_boundary_behavior(
    tmp_path: Path, mode: str, expected_steps: list[str], expects_decision: bool
) -> None:
    phase_runtime = FakePhaseRuntime(tmp_path / mode)
    decisions: list[int] = []

    def decide(packet):
        decisions.append(packet.sequence)
        return _decision(packet)

    result = Version2WorkflowRuntime(
        phase_runtime,
        _policy(mode),
        delegated_decision_provider=decide if mode == "delegated" else None,
    ).run()

    assert phase_runtime.executed == expected_steps
    assert result.completed is (mode != "attached")
    assert bool(decisions) is expects_decision
    state = phase_runtime.blackboard_store.load_or_create("spec")
    assert bool(state.driver_state.get("packets")) is expects_decision


@pytest.mark.parametrize("mode", ["attached", "unattended", "delegated"])
def test_each_mode_revalidates_live_policy_before_phase_use(tmp_path: Path, mode: str) -> None:
    configured = _policy(mode)
    replacement = _policy("unattended" if mode != "unattended" else "attached")
    runtime = FakePhaseRuntime(tmp_path / mode)

    @contextmanager
    def changed_authority():
        yield replacement

    result = Version2WorkflowRuntime(
        runtime,
        configured,
        delegated_decision_provider=(
            (lambda packet: _decision(packet)) if mode == "delegated" else None
        ),
        policy_authority=changed_authority,
    ).run()

    assert result.final_status_code == "DRIVER_POLICY_PAUSED"
    assert runtime.executed == []
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["lifecycle"] == "paused"
    assert state.driver_state["pause_reason"] == "driver_policy_changed"


@pytest.mark.parametrize("mode", ["attached", "unattended", "delegated"])
def test_each_mode_holds_live_policy_authority_through_phase_use(tmp_path: Path, mode: str) -> None:
    configured = _policy(mode)
    authority_held = False
    observations: list[bool] = []

    @contextmanager
    def live_authority():
        nonlocal authority_held
        authority_held = True
        try:
            yield configured
        finally:
            authority_held = False

    class AuthorityObservingRuntime(FakePhaseRuntime):
        def run(self, **kwargs) -> PlaybookRunResult:
            observations.append(authority_held)
            return super().run(**kwargs)

    runtime = AuthorityObservingRuntime(tmp_path / mode)
    Version2WorkflowRuntime(
        runtime,
        configured,
        delegated_decision_provider=(
            (lambda packet: _decision(packet)) if mode == "delegated" else None
        ),
        policy_authority=live_authority,
    ).run(single_step=True)

    assert observations == [True]


def test_manual_single_step_is_invocation_only_and_restart_continues(tmp_path: Path) -> None:
    issue_dir = tmp_path / "restart"
    first_runtime = FakePhaseRuntime(issue_dir)
    first = Version2WorkflowRuntime(first_runtime, _policy("unattended")).run(single_step=True)

    second_runtime = FakePhaseRuntime(issue_dir)
    second = Version2WorkflowRuntime(second_runtime, _policy("unattended")).run()

    assert first.completed is False
    assert first_runtime.executed == ["spec"]
    assert second.completed is True
    assert second_runtime.executed == ["plan"]
    state = second_runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state.get("consumed_sequences", []) == []


def test_unattended_advances_after_confirmed_status(tmp_path: Path) -> None:
    runtime = FakeConfirmedPhaseRuntime(tmp_path)

    result = Version2WorkflowRuntime(runtime, _policy("unattended")).run()

    assert result.completed is True
    assert runtime.executed == ["spec", "plan"]


def test_human_task_bypasses_driver_boundary_and_decision(tmp_path: Path) -> None:
    runtime = FakeHumanTaskRuntime(tmp_path)
    decisions = []

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated"),
        delegated_decision_provider=lambda packet: decisions.append(packet),
    ).run()

    assert result.final_status_code == "HUMAN_TASK_PENDING"
    assert decisions == []
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state.get("packets", {}) == {}
    assert state.driver_state["lifecycle"] == "human_task"


@pytest.mark.parametrize(
    "status_code",
    [
        "NO_BATON_TRANSITION",
        "NO_STATUS_CODE",
        "INVALID_STATUS_CODE",
        "MISSING_CAPABILITY_RECEIPT",
        "EXECUTOR_REJECTED",
    ],
)
def test_unattended_operational_stop_is_returned_without_retry(
    tmp_path: Path, status_code: str
) -> None:
    runtime = FakeOperationalStopRuntime(tmp_path, status_code)

    result = Version2WorkflowRuntime(runtime, _policy("unattended")).run()

    assert result.final_status_code == status_code
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["lifecycle"] == "error"
    assert state.driver_state["error_reason"] == status_code


def test_required_unavailable_delegated_driver_pauses_durably(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated"),
        delegated_decision_provider=None,
    ).run()

    assert result.completed is False
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["lifecycle"] == "paused"
    assert state.driver_state["pause_reason"] == "delegated_driver_unavailable"


def test_transport_failure_pauses_without_fallback(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    def unavailable(_packet):
        raise DriverUnavailableError("codex unavailable")

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated"),
        delegated_decision_provider=unavailable,
    ).run()

    assert result.completed is False
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["pause_reason"] == "delegated_driver_unavailable"
    assert "fallback_reason" not in state.driver_state


def test_runtime_completion_does_not_create_workflow_notification_receipts(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    Version2WorkflowRuntime(runtime, _policy("unattended")).run()

    state = runtime.blackboard_store.load_or_create("spec")
    assert "notification_receipts" not in state.driver_state


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (DriverUnavailableError("acquisition failed"), "delegated_driver_unavailable"),
        (DriverUnavailableError("resume failed"), "delegated_driver_unavailable"),
        (DriverModelMismatchError("wrong model"), "delegated_model_mismatch"),
        (ValueError("invalid response"), "delegated_invalid_decision"),
    ],
)
def test_shared_fake_transport_faults_pause_durably(
    tmp_path: Path, failure: Exception, expected_reason: str
) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    def fail(_packet):
        raise failure

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated"),
        delegated_decision_provider=fail,
    ).run()

    assert result.completed is False
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["lifecycle"] == "paused"
    assert state.driver_state["pause_reason"] == expected_reason


@pytest.mark.parametrize(
    ("restart_stage", "expected_provider_calls"),
    [("packet", 1), ("decision", 0), ("consumed", 0)],
)
def test_shared_fake_transport_restart_reuses_boundary_ledger_once(
    tmp_path: Path, restart_stage: str, expected_provider_calls: int
) -> None:
    issue_dir = tmp_path / restart_stage
    staged = FakePhaseRuntime(issue_dir)
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    packet = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="durable-spec-plan",
        policy=_policy("delegated"),
    )
    decision = _decision(packet)
    if restart_stage in {"decision", "consumed"}:
        coordinator.record_decision(decision)
    if restart_stage == "consumed":
        assert coordinator.consume_authorization(packet.sequence) == decision

    resumed = FakePhaseRuntime(issue_dir)
    provider_calls: list[int] = []

    def decide(pending):
        provider_calls.append(pending.sequence)
        return decision

    result = Version2WorkflowRuntime(
        resumed,
        _policy("delegated"),
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert len(provider_calls) == expected_provider_calls
    state = resumed.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == [packet.sequence]
    assert len(state.driver_state["decisions"]) == 1


def test_restart_reconciles_durable_transition_missing_its_boundary_packet(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "missing-boundary"
    staged = FakePhaseRuntime(issue_dir)
    staged.blackboard_store.record_event(
        staged.blackboard,
        "transition",
        {
            "from": "spec",
            "to": "plan",
            "status_code": "ready_for_plan",
            "source": "test.crash_window",
            "runtime": "single_step",
        },
    )
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")

    resumed = FakePhaseRuntime(issue_dir)
    decisions: list[int] = []

    def decide(packet):
        decisions.append(packet.sequence)
        return _decision(packet)

    result = Version2WorkflowRuntime(
        resumed,
        _policy("delegated"),
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert decisions == [1]
    state = resumed.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == [1]
    assert state.driver_state["packets"]["1"]["completed_phase"] == "spec"


def test_missing_boundary_reconciliation_distinguishes_repeated_requested_action(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "repeated-action"
    staged = FakePhaseRuntime(issue_dir)
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    first = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="first-plan",
        policy=_policy("delegated"),
    )
    coordinator.record_decision(_decision(first))
    assert coordinator.consume_authorization(first.sequence) is not None
    staged.blackboard_store.record_event(
        staged.blackboard,
        "transition",
        {
            "from": "review",
            "to": "plan",
            "status_code": "retry_plan",
            "source": "test.repeated_action",
            "runtime": "single_step",
        },
    )
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    resumed = FakePhaseRuntime(issue_dir)
    packets = []

    def decide(packet):
        packets.append(packet)
        return _decision(packet)

    Version2WorkflowRuntime(
        resumed,
        _policy("delegated"),
        delegated_decision_provider=decide,
    ).run()

    assert [packet.sequence for packet in packets] == [2]
    assert packets[0].completed_phase == "review"


def test_policy_change_reauthorizes_pending_boundary_with_current_exact_model(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "policy-change"
    staged = FakePhaseRuntime(issue_dir)
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    old_policy = _policy("delegated")
    old_packet = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="durable-spec-plan",
        policy=old_policy,
    )
    coordinator.record_decision(_decision(old_packet))
    new_policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": "new-exact-model"},
        }
    )
    resumed = FakePhaseRuntime(issue_dir)
    provider_packets = []

    def decide(packet):
        provider_packets.append(packet)
        return _decision(packet)

    result = Version2WorkflowRuntime(
        resumed,
        new_policy,
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert [packet.driver_model for packet in provider_packets] == ["new-exact-model"]
    state = resumed.blackboard_store.load_or_create("spec")
    assert old_packet.sequence in state.driver_state["superseded_sequences"]
    assert state.driver_state["consumed_sequences"] != [old_packet.sequence]


def test_policy_change_after_decision_pauses_before_consumption_and_phase_use(
    tmp_path: Path,
) -> None:
    old_policy = _policy("delegated")
    new_policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": "new-exact-model"},
        }
    )
    authority = [old_policy]
    runtime = FakePhaseRuntime(tmp_path)

    def decide(packet):
        authority[0] = new_policy
        return _decision(packet)

    @contextmanager
    def current_authority():
        yield authority[0]

    result = Version2WorkflowRuntime(
        runtime,
        old_policy,
        delegated_decision_provider=decide,
        policy_authority=current_authority,
    ).run()

    assert result.completed is False
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == []
    assert state.driver_state["lifecycle"] == "paused"
    assert state.driver_state["pause_reason"] == "driver_policy_changed"


def test_policy_authority_guard_spans_consumption_and_phase_use(tmp_path: Path) -> None:
    issue_dir = tmp_path / "guarded-policy-use"
    staged = FakePhaseRuntime(issue_dir)
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    old_policy = _policy("delegated")
    old_packet = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="durable-spec-plan",
        policy=old_policy,
    )
    coordinator.record_decision(_decision(old_packet))
    new_policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": "new-exact-model"},
        }
    )
    authority = [old_policy]
    events: list[str] = []

    @contextmanager
    def guarded_authority():
        events.append("authority_entered")
        try:
            yield authority[0]
        finally:
            authority[0] = new_policy
            events.append("authority_released")

    class AuthorityObservingRuntime(FakePhaseRuntime):
        def run(self, **kwargs) -> PlaybookRunResult:
            events.append(f"phase_used:{authority[0].driver.model}")
            return super().run(**kwargs)

    resumed = AuthorityObservingRuntime(issue_dir)
    result = Version2WorkflowRuntime(
        resumed,
        old_policy,
        policy_authority=guarded_authority,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert events == [
        "authority_entered",
        "phase_used:gpt-5.6-codex",
        "authority_released",
    ]
    state = resumed.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == [old_packet.sequence]


def test_policy_change_after_consumption_reauthorizes_before_recovered_phase(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "consumed-policy-change"
    staged = FakePhaseRuntime(issue_dir)
    staged.blackboard_store.set_current_step(staged.blackboard, "plan")
    coordinator = DriverCoordinator(staged.blackboard_store, staged.blackboard)
    old_policy = _policy("delegated")
    old_packet = coordinator.open_boundary(
        completed_phase="spec",
        requested_action="plan",
        boundary_id="durable-spec-plan",
        policy=old_policy,
    )
    coordinator.record_decision(_decision(old_packet))
    assert coordinator.consume_authorization(old_packet.sequence) is not None
    new_policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": "codex", "model": "new-exact-model"},
        }
    )
    resumed = FakePhaseRuntime(issue_dir)
    provider_packets = []

    def decide(packet):
        provider_packets.append(packet)
        return _decision(packet)

    result = Version2WorkflowRuntime(
        resumed,
        new_policy,
        delegated_decision_provider=decide,
    ).run()

    assert result.completed is True
    assert resumed.executed == ["plan"]
    assert [packet.driver_model for packet in provider_packets] == ["new-exact-model"]
    state = resumed.blackboard_store.load_or_create("spec")
    assert old_packet.sequence in state.driver_state["superseded_sequences"]
    assert len(state.driver_state["consumed_sequences"]) == 2
