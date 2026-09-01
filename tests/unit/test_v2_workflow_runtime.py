"""Internal version 2 ownership and advancement runtime tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverDecision, DriverUnavailableError
from cafe.core.v2_workflow_runtime import Version2WorkflowRuntime
from cafe.core.workflow_notifications import WorkflowNotifier
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


def _policy(mode: str, advancement: str, hosting: str) -> DriverPolicyContract:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["attached"] = {"poll_interval_seconds": 10}
    elif mode == "delegated":
        driver["delegated"] = {"cli": "codex", "availability": "required"}
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": driver,
            "execution": {"advancement": advancement, "hosting": hosting},
        }
    )


@pytest.mark.parametrize("mode", ["attached", "unattended", "delegated"])
@pytest.mark.parametrize("advancement", ["continuous", "single_step"])
@pytest.mark.parametrize("hosting", ["foreground", "background"])
def test_ownership_advancement_matrix_is_host_independent(
    tmp_path: Path, mode: str, advancement: str, hosting: str
) -> None:
    phase_runtime = FakePhaseRuntime(tmp_path / f"{mode}-{advancement}-{hosting}")
    decisions: list[int] = []

    def decide(packet):
        decisions.append(packet.sequence)
        return DriverDecision(
            workflow_id=packet.workflow_id,
            sequence=packet.sequence,
            requested_action=packet.requested_action,
            action="advance",
        )

    result = Version2WorkflowRuntime(
        phase_runtime,
        _policy(mode, advancement, hosting),
        delegated_decision_provider=decide if mode == "delegated" else None,
    ).run()

    if advancement == "continuous":
        assert phase_runtime.executed == ["spec", "plan"]
        assert result.completed is True
    else:
        assert phase_runtime.executed == ["spec"]
        assert result.completed is False
    assert bool(decisions) is (mode == "delegated")


def test_single_step_authorization_is_consumed_once_after_restart(tmp_path: Path) -> None:
    issue_dir = tmp_path / "restart"
    first_runtime = FakePhaseRuntime(issue_dir)
    first = Version2WorkflowRuntime(
        first_runtime, _policy("unattended", "single_step", "foreground")
    ).run()

    second_runtime = FakePhaseRuntime(issue_dir)
    second = Version2WorkflowRuntime(
        second_runtime, _policy("unattended", "single_step", "background")
    ).run()

    assert first.completed is False
    assert first_runtime.executed == ["spec"]
    assert second.completed is True
    assert second_runtime.executed == ["plan"]
    state = second_runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["consumed_sequences"] == [1]


def test_human_task_bypasses_driver_boundary_and_decision(tmp_path: Path) -> None:
    runtime = FakeHumanTaskRuntime(tmp_path)
    decisions = []

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated", "continuous", "foreground"),
        delegated_decision_provider=lambda packet: decisions.append(packet),
    ).run()

    assert result.final_status_code == "HUMAN_TASK_PENDING"
    assert decisions == []
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state.get("packets", {}) == {}
    assert state.driver_state["lifecycle"] == "human_task"


def test_required_unavailable_delegated_driver_pauses_durably(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)

    result = Version2WorkflowRuntime(
        runtime,
        _policy("delegated", "continuous", "foreground"),
        delegated_decision_provider=None,
    ).run()

    assert result.completed is False
    assert runtime.executed == ["spec"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["lifecycle"] == "paused"
    assert state.driver_state["pause_reason"] == "delegated_driver_unavailable"


def test_best_effort_transport_failure_records_fallback_and_continues(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    policy = _policy("delegated", "continuous", "foreground")
    policy = policy.model_copy(
        update={
            "driver": policy.driver.model_copy(
                update={
                    "delegated": policy.driver.delegated.model_copy(
                        update={"availability": "best_effort"}
                    )
                }
            )
        }
    )

    def unavailable(_packet):
        raise DriverUnavailableError("codex unavailable")

    result = Version2WorkflowRuntime(
        runtime,
        policy,
        delegated_decision_provider=unavailable,
    ).run()

    assert result.completed is True
    assert runtime.executed == ["spec", "plan"]
    state = runtime.blackboard_store.load_or_create("spec")
    assert state.driver_state["fallback_reason"] == "delegated_driver_unavailable"


def test_runtime_notifies_phase_boundaries_and_completion_only(tmp_path: Path) -> None:
    runtime = FakePhaseRuntime(tmp_path)
    requests: list[dict] = []
    notifier = WorkflowNotifier(
        tmp_path,
        configured=True,
        dispatcher=lambda request: requests.append(request) or {"success": True},
    )

    Version2WorkflowRuntime(
        runtime,
        _policy("unattended", "continuous", "foreground"),
        notifier=notifier,
    ).run()

    assert [request["args"]["event_type"] for request in requests] == [
        "phase_boundary",
        "completion",
    ]
