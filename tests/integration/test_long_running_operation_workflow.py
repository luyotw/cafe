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
import threading
import time
from pathlib import Path

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import (
    LongRunningOperationState,
    OperationLogPolicy,
    OperationMonitoring,
    OperationRisk,
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
        "develop": {"skill": "develop", "role": "developer", "on": {"await_agent": "review"}},
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


def test_runtime_rechecks_operation_created_inside_executor_before_no_status_fallback(
    tmp_path: Path,
) -> None:
    """Executable reproduction for issue #386's NO_STATUS_CODE gap.

    The runtime is constructed first; the executor then uses the production
    helper and returns no status/baton. The same run must notice the trusted
    operation and pause as OPERATION_RUNNING instead of falling through to
    NO_STATUS_CODE.
    """
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-same-run"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-same-run"
    script = tmp_path / "tracked_wait.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys, time\n"
        "from pathlib import Path\n"
        "release_file = Path(sys.argv[1])\n"
        "while not release_file.exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    executor_calls = 0

    def launching_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        nonlocal executor_calls
        executor_calls += 1
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        launched = run_operation_command(
            issue_dir=issue_dir,
            step=step_name,
            iteration_dir=iteration_dir,
            command=[sys.executable, str(script), str(release_file)],
            cwd=tmp_path,
            playbook=_PLAYBOOK,
            reason="same_run_real_helper_probe",
        )
        assert launched.started is True
        return StepExecutionResult(response="waiting for tracked operation", artifacts={})

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=launching_executor,
    )
    first_result = runtime.run(start_step="develop", single_step=True)

    assert first_result.final_status_code == "OPERATION_RUNNING"
    assert executor_calls == 1
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "running"

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=duplicate_launch_executor,
    ).run(start_step="develop", single_step=True)

    release_file.write_text("go", encoding="utf-8")
    assert second_result.final_status_code == "OPERATION_RUNNING"


def test_runtime_run_rechecks_helper_liveness_and_marks_lost_without_manual_status(
    tmp_path: Path,
) -> None:
    """Resume must perform the controlled status/liveness check itself.

    This intentionally does not call ``get_operation_status`` before the
    second ``BlackboardWorkflowRuntime.run``. A running operation whose helper
    handle is gone must become actionable/lost instead of staying
    ``OPERATION_RUNNING`` forever.
    """
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-runtime-lost"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-runtime-lost"
    script = tmp_path / "tracked_wait_runtime_lost.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys, time\n"
        "from pathlib import Path\n"
        "release_file = Path(sys.argv[1])\n"
        "while not release_file.exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    launched_operations = []

    def launching_executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        launched = run_operation_command(
            issue_dir=issue_dir,
            step=step_name,
            iteration_dir=iteration_dir,
            command=[sys.executable, str(script), str(release_file)],
            cwd=tmp_path,
            playbook=_PLAYBOOK,
            reason="runtime_liveness_probe",
        )
        launched_operations.append(launched)
        assert launched.started is True
        return StepExecutionResult(response="waiting for tracked operation", artifacts={})

    first_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=launching_executor,
    ).run(start_step="develop", single_step=True)

    assert first_result.final_status_code == "OPERATION_RUNNING"
    launched = launched_operations[0]
    deadline = time.time() + 2
    while not launched.handle_path.exists() and time.time() < deadline:
        time.sleep(0.02)
    handle = json.loads(launched.handle_path.read_text(encoding="utf-8"))
    monitor_pid = int(handle["monitor_pid"])
    try:
        os.killpg(monitor_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    launched.handle_path.unlink(missing_ok=True)

    def duplicate_launch_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked again for {step_name}")

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=duplicate_launch_executor,
    ).run(start_step="develop", single_step=True)

    release_file.write_text("go", encoding="utf-8")
    assert second_result.final_status_code == "OPERATION_LOST"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"
    assert json.loads((iteration_dir / "operation_receipt.json").read_text())["state"] == "lost"


def test_succeeded_operation_without_phase_artifacts_runs_finalize_only_once(
    tmp_path: Path,
) -> None:
    """A normal long command only exits 0; it cannot write CAFE artifacts.

    Once the trusted receipt is succeeded, the next runtime invocation should
    call the executor in a finalize-only path so the phase can verify and
    write output/checklist/baton, without relaunching the tracked command.
    """
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-finalize-only"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    start_count = tmp_path / "long-command-start-count.txt"
    script = tmp_path / "only_exits_zero.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "counter = Path(sys.argv[1])\n"
        "count = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
        "counter.write_text(str(count + 1), encoding='utf-8')\n"
        "time.sleep(0.2)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    executor_calls = 0
    received_extra_prompts: list[str | None] = []

    def executor(
        step_name: str,
        step_def: dict,
        state: object,
        extra_prompt: str | None = None,
        **_kwargs: object,
    ) -> StepExecutionResult:
        nonlocal executor_calls
        executor_calls += 1
        received_extra_prompts.append(extra_prompt)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": 1}), encoding="utf-8"
        )
        if executor_calls == 1:
            launched = run_operation_command(
                issue_dir=issue_dir,
                step=step_name,
                iteration_dir=iteration_dir,
                command=[sys.executable, str(script), str(start_count)],
                cwd=tmp_path,
                playbook=_PLAYBOOK,
                reason="finalize_only_long_command",
            )
            assert launched.started is True
            return StepExecutionResult(response="waiting for tracked operation", artifacts={})

        (iteration_dir / "output.md").write_text("# finalized\n", encoding="utf-8")
        (iteration_dir / "checklist.md").write_text("- [x] finalized\n", encoding="utf-8")
        _write_baton(issue_dir, from_step=step_name, to_step="review")
        return StepExecutionResult(response="finalized", artifacts={})

    first_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=executor,
    ).run(start_step="develop", single_step=True)
    assert first_result.final_status_code == "OPERATION_RUNNING"

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

    # The real CLI reserves the next iteration before runtime reconciliation.
    # That empty directory must not hide iteration_001's trusted receipt.
    next_iteration_dir = issue_dir / "develop" / "iteration_002"
    next_iteration_dir.mkdir(parents=True)
    (next_iteration_dir / "iteration.json").write_text(
        json.dumps({"iteration": 2}), encoding="utf-8"
    )

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=executor,
    ).run(start_step="develop", single_step=True)

    assert second_result.final_status_code != "OPERATION_RUNNING"
    assert executor_calls == 2
    assert received_extra_prompts[0] is None
    assert received_extra_prompts[1] is not None
    assert "[LONG-RUNNING OPERATION FINALIZE ONLY]" in received_extra_prompts[1]
    assert start_count.read_text(encoding="utf-8") == "1"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "succeeded"
    completed_runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=executor,
    )
    assert completed_runtime._pending_operation_iteration_dir("develop") is None


def test_run_operation_command_claims_single_operation_atomically(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-atomic-claim"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-atomic"
    script = tmp_path / "tracked_atomic_wait.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import sys, time\n"
        "from pathlib import Path\n"
        "release_file = Path(sys.argv[1])\n"
        "while not release_file.exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    barrier = threading.Barrier(2)
    results = []

    def launch() -> object:
        barrier.wait(timeout=5)
        return run_operation_command(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            command=[sys.executable, str(script), str(release_file)],
            cwd=tmp_path,
            playbook=_PLAYBOOK,
            reason="atomic_claim_probe",
        )

    threads = [threading.Thread(target=lambda: results.append(launch())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    release_file.write_text("go", encoding="utf-8")
    assert len(results) == 2
    assert sum(1 for result in results if result.started) == 1
    assert len({result.operation.operation_id for result in results}) == 1


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
        command=[
            sys.executable,
            str(script),
            str(release_file),
            str(issue_dir),
            str(iteration_dir),
        ],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_long_command",
        risk=OperationRisk.MEDIUM,
        monitoring=OperationMonitoring.PERIODIC,
        log_policy=OperationLogPolicy.INCREMENTAL_TAIL,
        stop_condition="stop if the integration fixture reports failure",
        recovery="inspect the same operation id before retrying",
    )
    duplicate = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[
            sys.executable,
            str(script),
            str(release_file),
            str(issue_dir),
            str(iteration_dir),
        ],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="integration_fake_long_command",
    )
    assert duplicate.operation.operation_id == launched.operation.operation_id
    assert duplicate.started is False
    assert (
        get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        ).state
        == LongRunningOperationState.RUNNING
    )

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
    assert (status.risk, status.monitoring, status.log_policy) == (
        OperationRisk.MEDIUM,
        OperationMonitoring.PERIODIC,
        OperationLogPolicy.INCREMENTAL_TAIL,
    )

    second_result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=duplicate_launch_executor
    ).run(start_step="develop", single_step=True)

    assert second_result.final_status_code != "OPERATION_RUNNING"
    assert (
        json.loads((iteration_dir / "operation_receipt.json").read_text())["state"] == "succeeded"
    )
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "succeeded"


def test_production_helper_records_nonzero_exit_as_failed(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-op-helper-failed"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    script = tmp_path / "fake_long_failed.py"
    script.write_text(
        "from __future__ import annotations\n" "import sys\n" "sys.exit(7)\n",
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

    assert result.final_status_code == "OPERATION_LOST"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"


def test_agent_timeout_without_helper_evidence_becomes_lost_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    """Timeout-created operation state must not stay running without helper evidence."""
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

    assert second_result.final_status_code == "OPERATION_LOST"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert json.loads((iteration_dir / "operation.json").read_text())["state"] == "lost"
