"""Production-journey integration tests for the long-running operation lifecycle.

These drive the real ``BlackboardWorkflowRuntime.run()`` entry point across
separate runtime instances against the same on-disk issue directory to
simulate a genuine crash/resume journey (the way the CLI/agent harness
actually re-invokes CAFE one step at a time), rather than hand-writing
``operation.json``. They prove the workflow runtime itself is the only
production writer that ever moves a ``running`` operation to
``succeeded``/``failed``/``lost``, and that resume never launches the
underlying agent a second time while an operation is unresolved.
"""

from __future__ import annotations

import json
from pathlib import Path

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import (
    BlackboardStore,
    LongRunningOperationArtifact,
    LongRunningOperationState,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

_PLAYBOOK = {
    "playbook": {"id": "default"},
    "steps": {
        "develop": {"skill": "develop", "role": "developer", "on": {"confirmed": "review"}},
        "review": {"skill": "review", "role": "developer", "on": {"confirmed": "_done"}},
    },
}


def _write_baton(issue_dir: Path, *, from_step: str, to_step: str) -> None:
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": from_step,
                "to_owner": "agent",
                "to_step": to_step,
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-04-26T23:00:00+08:00",
                "source": "agent",
            }
        ),
        encoding="utf-8",
    )


def test_agent_timeout_then_resume_promotes_running_to_succeeded_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Journey: a controlled agent-timeout leaves a running operation; the background
    work actually finished and left ordinary evidence; resume promotes running ->
    succeeded itself and applies the handoff, without invoking the executor again."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-succeeded"

    def crashing_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        raise AgentExecutionError(
            "agent did not produce output before the execution timeout", error_type="timeout"
        )

    first_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    )
    first_result = first_run.run(start_step="develop", single_step=True)
    assert first_result.final_status_code.startswith("INTERRUPTED")

    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "running"

    # Between crash and resume, the backgrounded agent leaves ordinary
    # artifacts, and a controlled production helper records the terminal
    # receipt for the same operation identity. The runtime must not infer the
    # terminal operation outcome from agent-writable files alone.
    (iteration_dir / "output.md").write_text("# done\n", encoding="utf-8")
    (iteration_dir / "checklist.md").write_text("- [x] done\n", encoding="utf-8")
    _write_baton(issue_dir, from_step="develop", to_step="review")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    running_operation = store.read_operation_artifact(iteration_dir)
    assert running_operation is not None
    store.write_operation_receipt(
        state,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=running_operation.operation_id,
        artifact=LongRunningOperationArtifact(
            operation_id=running_operation.operation_id,
            state=LongRunningOperationState.SUCCEEDED,
            reason="controlled_helper_completed",
            exit_code=0,
        ),
    )

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    second_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    second_result = second_run.run(start_step="develop", single_step=True)

    assert second_result.final_status_code != "INTERRUPTED:agent_timeout"
    operation_data = json.loads((iteration_dir / "operation.json").read_text())
    assert operation_data["state"] == "succeeded"

    bb = BlackboardStore(issue_dir).load_or_create("develop")
    assert any(e.event_type == "step_reconciled" for e in bb.events)
    assert any(
        e.event_type == "long_running_operation" and e.data.get("state") == "succeeded"
        for e in bb.events
    )


def test_agent_writable_completion_evidence_without_receipt_does_not_promote_to_succeeded(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-no-receipt"

    def crashing_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        raise AgentExecutionError(
            "agent did not produce output before the execution timeout", error_type="timeout"
        )

    BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    ).run(start_step="develop", single_step=True)

    iteration_dir = issue_dir / "develop" / "iteration_001"
    (iteration_dir / "output.md").write_text("# agent-authored\n", encoding="utf-8")
    (iteration_dir / "checklist.md").write_text("- [x] done\n", encoding="utf-8")
    _write_baton(issue_dir, from_step="develop", to_step="review")

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)

    assert result.final_status_code == "OPERATION_RUNNING"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "running"


def test_agent_timeout_then_resume_stays_running_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Journey: resume happens before the controlled helper leaves a terminal receipt;
    the operation stays running, recoverable, and the executor is not invoked again."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-still-running"

    def crashing_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        raise AgentExecutionError(
            "agent did not produce output before the execution timeout", error_type="timeout"
        )

    first_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    )
    first_run.run(start_step="develop", single_step=True)

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    second_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    second_result = second_run.run(start_step="develop", single_step=True)

    assert second_result.final_status_code == "OPERATION_RUNNING"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "running"


def test_agent_timeout_then_controlled_receipt_marks_lost_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Journey: the controlled helper can no longer find/verify the operation and
    records a lost receipt; resume promotes running -> lost without a relaunch."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-lost"

    def crashing_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        raise AgentExecutionError(
            "agent did not produce output before the execution timeout", error_type="timeout"
        )

    first_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    )
    first_run.run(start_step="develop", single_step=True)

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    iteration_dir = issue_dir / "develop" / "iteration_001"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    running_operation = store.read_operation_artifact(iteration_dir)
    assert running_operation is not None
    store.write_operation_receipt(
        state,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=running_operation.operation_id,
        artifact=LongRunningOperationArtifact(
            operation_id=running_operation.operation_id,
            state=LongRunningOperationState.LOST,
            reason="controlled_helper_could_not_find_operation",
        ),
    )

    second_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    second_result = second_run.run(start_step="develop", single_step=True)

    assert second_result.final_status_code == "OPERATION_LOST"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"

    third_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    third_result = third_run.run(start_step="develop", single_step=True)
    assert third_result.final_status_code == "OPERATION_LOST"


def test_agent_timeout_then_controlled_receipt_marks_failed_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Journey: the controlled helper records failure for the same operation
    identity; resume promotes running -> failed without invoking the executor
    again."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-failed"

    def crashing_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        raise AgentExecutionError(
            "agent did not produce output before the execution timeout",
            error_type="timeout",
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    )
    first_result = runtime.run(start_step="develop", max_transitions=5)
    assert first_result.final_status_code.startswith("INTERRUPTED")
    iteration_dir = issue_dir / "develop" / "iteration_001"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    running_operation = store.read_operation_artifact(iteration_dir)
    assert running_operation is not None
    assert running_operation.state == LongRunningOperationState.RUNNING
    store.write_operation_receipt(
        state,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=running_operation.operation_id,
        artifact=LongRunningOperationArtifact(
            operation_id=running_operation.operation_id,
            state=LongRunningOperationState.FAILED,
            reason="controlled_helper_failed",
            exit_code=1,
        ),
    )

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", max_transitions=5)
    assert second_result.final_status_code == "OPERATION_FAILED"

    operation_data = json.loads((iteration_dir / "operation.json").read_text())
    assert operation_data["state"] == "failed"
    bb = BlackboardStore(issue_dir).load_or_create("develop")
    assert any(
        e.event_type == "long_running_operation" and e.data.get("state") == "failed"
        for e in bb.events
    )

    resume_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    resume_result = resume_runtime.run(max_transitions=5)
    assert resume_result.final_status_code == "OPERATION_FAILED"
