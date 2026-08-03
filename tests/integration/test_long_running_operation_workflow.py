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
from cafe.core.blackboard import BlackboardStore
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

    # Between crash and resume, the backgrounded agent actually finished and
    # left the ordinary evidence a completed step would leave: output,
    # checklist, and its own downstream baton.
    (iteration_dir / "output.md").write_text("# done\n", encoding="utf-8")
    (iteration_dir / "checklist.md").write_text("- [x] done\n", encoding="utf-8")
    _write_baton(issue_dir, from_step="develop", to_step="review")

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


def test_agent_timeout_then_resume_stays_running_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Journey: resume happens before the backgrounded work leaves any evidence and
    before the recovery window elapses; the operation stays running, recoverable,
    and the executor is not invoked again."""
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


def test_agent_timeout_then_resume_marks_lost_after_recovery_window(
    tmp_path: Path, monkeypatch
) -> None:
    """Journey: no evidence ever appears and the bounded recovery window elapses;
    resume gives up and marks the operation lost instead of pausing forever, and
    still does not invoke the executor again."""
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

    monkeypatch.setattr(BlackboardWorkflowRuntime, "_OPERATION_LOST_AFTER_SECONDS", -1)
    second_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    second_result = second_run.run(start_step="develop", single_step=True)

    assert second_result.final_status_code == "OPERATION_LOST"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"

    third_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    third_result = third_run.run(start_step="develop", single_step=True)
    assert third_result.final_status_code == "OPERATION_LOST"


def test_explicit_override_rerun_with_critical_error_marks_operation_failed(
    tmp_path: Path,
) -> None:
    """Journey: an explicit multi-transition re-run of the same interrupted step (a
    caller-driven override that bypasses resume reconciliation, distinct from the
    default single-step resume path covered above) hits a genuine classified
    critical failure; the runtime promotes the existing running operation to
    failed instead of leaving it running forever."""
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-journey-failed"

    attempts = {"count": 0}

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        attempts["count"] += 1
        iteration_dir = issue_dir / step_name / "iteration_001"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        if attempts["count"] == 1:
            raise AgentExecutionError(
                "agent did not produce output before the execution timeout",
                error_type="timeout",
            )
        raise AgentExecutionError("Rate limit exceeded", error_type="rate_limit")

    runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=_PLAYBOOK, executor=executor)
    first_result = runtime.run(start_step="develop", max_transitions=5)
    assert first_result.final_status_code.startswith("INTERRUPTED")
    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "running"

    second_result = runtime.run(start_step="develop", max_transitions=5)
    assert second_result.final_status_code == "OPERATION_FAILED"
    assert attempts["count"] == 2

    operation_data = json.loads((iteration_dir / "operation.json").read_text())
    assert operation_data["state"] == "failed"
    bb = BlackboardStore(issue_dir).load_or_create("develop")
    assert any(
        e.event_type == "long_running_operation" and e.data.get("state") == "failed"
        for e in bb.events
    )

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    resume_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    )
    resume_result = resume_runtime.run(max_transitions=5)
    assert resume_result.final_status_code == "OPERATION_FAILED"
