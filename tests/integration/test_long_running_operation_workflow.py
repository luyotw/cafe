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
import os
import signal
import sys
import time
from pathlib import Path

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import (
    BlackboardStore,
    LongRunningOperationState,
)
from cafe.core.long_running_operation_helper import (
    get_operation_status,
    run_operation_command,
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


def test_production_helper_owns_launch_status_and_terminal_receipt_for_success(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-helper-success"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-success"
    script = tmp_path / "fake_long_success.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

release_file = Path(sys.argv[1])
issue_dir = Path(sys.argv[2])
iteration_dir = Path(sys.argv[3])
while not release_file.exists():
    time.sleep(0.02)
iteration_dir.mkdir(parents=True, exist_ok=True)
(iteration_dir / "output.md").write_text("# done\\n", encoding="utf-8")
(iteration_dir / "checklist.md").write_text("- [x] done\\n", encoding="utf-8")
(issue_dir / "next_step.txt").write_text(
    json.dumps(
        {
            "version": 1,
            "from_step": "develop",
            "to_owner": "agent",
            "to_step": "review",
            "intent": "await_agent",
            "status_code": "",
            "created_at": "2026-04-26T23:00:00+08:00",
            "source": "fake-long-success",
        }
    ),
    encoding="utf-8",
)
""".strip(),
        encoding="utf-8",
    )

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script), str(release_file), str(issue_dir), str(iteration_dir)],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_long_command",
    )
    duplicate = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script), str(release_file), str(issue_dir), str(iteration_dir)],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_long_command",
    )
    assert duplicate.operation.operation_id == launched.operation.operation_id
    assert duplicate.started is False
    assert get_operation_status(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        playbook=_PLAYBOOK,
    ).state == LongRunningOperationState.RUNNING

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    running_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)
    assert running_result.final_status_code == "OPERATION_RUNNING"

    release_file.write_text("go", encoding="utf-8")
    deadline = time.time() + 5
    status = get_operation_status(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        playbook=_PLAYBOOK,
    )
    while status.state == LongRunningOperationState.RUNNING and time.time() < deadline:
        time.sleep(0.05)
        status = get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        )
    assert status.state == LongRunningOperationState.SUCCEEDED
    assert status.exit_code == 0

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)

    assert second_result.final_status_code != "OPERATION_RUNNING"
    assert json.loads((iteration_dir / "operation_receipt.json").read_text())["state"] == "succeeded"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "succeeded"


def test_production_helper_records_nonzero_exit_as_failed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-helper-failed"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    script = tmp_path / "fake_long_failed.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script)],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_failed_command",
    )
    assert launched.started is True

    deadline = time.time() + 5
    status = get_operation_status(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        playbook=_PLAYBOOK,
    )
    while status.state == LongRunningOperationState.RUNNING and time.time() < deadline:
        time.sleep(0.05)
        status = get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        )

    assert status.state == LongRunningOperationState.FAILED
    assert status.exit_code == 7

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)

    assert result.final_status_code == "OPERATION_FAILED"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "failed"


def test_production_helper_marks_lost_when_handle_identity_is_unverifiable(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-helper-lost"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-lost"
    script = tmp_path / "fake_long_lost.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys, time\n"
        "from pathlib import Path\n"
        "release_file = Path(sys.argv[1])\n"
        "while not release_file.exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script), str(release_file)],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_lost_command",
    )
    assert launched.started is True
    deadline = time.time() + 2
    while not launched.handle_path.exists() and time.time() < deadline:
        time.sleep(0.02)
    handle = json.loads(launched.handle_path.read_text(encoding="utf-8"))
    monitor_pid = int(handle["monitor_pid"])
    command_pid = int(handle["command_pid"])
    launched.handle_path.unlink()

    def command_alive() -> bool:
        stat_path = Path("/proc") / str(command_pid) / "stat"
        try:
            stat = stat_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return stat.rsplit(") ", 1)[1].split()[0] != "Z"

    try:
        status = get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        )
    finally:
        try:
            os.killpg(monitor_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.time() + 2
    while command_alive() and time.time() < deadline:
        time.sleep(0.02)
    try:
        assert not command_alive()
    finally:
        try:
            os.killpg(command_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    assert status.state == LongRunningOperationState.LOST
    assert status.reason == "operation_handle_missing"

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)

    assert result.final_status_code == "OPERATION_LOST"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"


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
