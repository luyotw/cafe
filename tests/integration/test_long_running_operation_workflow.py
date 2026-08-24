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
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from cafe.agents.executor import AgentExecutionError
from cafe.core import long_running_operation_helper as operation_helper
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

_LOW_OPERATION_DECISION = {
    "risk": OperationRisk.LOW,
    "monitoring": OperationMonitoring.FINAL_ONLY,
    "log_policy": OperationLogPolicy.SUMMARY_ONLY,
    "stop_condition": "stop at the declared test boundary",
    "recovery": "inspect the same operation id",
}


@pytest.fixture(autouse=True)
def _codex_sandbox_process_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Use a strict CLI double for workflow tests and the real backend for its boundary journey."""
    if request.node.name != "test_real_operation_enforces_declared_sandbox_boundary":
        binary = tmp_path / "bin" / "codex"
        binary.parent.mkdir()
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "if not args or args.pop(0) != 'sandbox': raise SystemExit(2)\n"
            "state = None; network_denied = False\n"
            "while args and args[0].startswith('--'):\n"
            " option = args.pop(0)\n"
            " if option == '--sandbox-state-json': state = json.loads(args.pop(0))\n"
            " elif option == '--sandbox-state-readable-root': args.pop(0)\n"
            " elif option == '--sandbox-state-disable-network': network_denied = True\n"
            "if state is None or not network_denied: raise SystemExit(3)\n"
            "entries = state['permissionProfile']['file_system']['entries']\n"
            "if not any(item['access'] == 'write' for item in entries): raise SystemExit(4)\n"
            "if not args: raise SystemExit(2)\n"
            "os.execvp(args[0], args)\n",
            encoding="utf-8",
        )
        binary.chmod(0o700)
        monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        return


def test_real_operation_enforces_declared_sandbox_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    issue_dir = allowed / ".cafe" / "issues" / "real-boundary"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    outside = tmp_path / "outside.txt"
    result_file = allowed / "result.txt"
    script = allowed / "probe.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os, sys\n"
        "result, outside = map(Path, sys.argv[1:])\n"
        "try:\n outside.write_text('escaped')\n except OSError:\n pass\n"
        "result.write_text(str('GH_TOKEN' in os.environ))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN", "sentinel")
    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script), str(result_file), str(outside)],
        cwd=allowed,
        readable_roots=(allowed,),
        writable_roots=(allowed,),
        playbook=_PLAYBOOK,
        reason="real_sandbox_boundary_probe",
        **_LOW_OPERATION_DECISION,
    )
    if launched.started is False:
        assert launched.operation.reason == "sandbox_user_namespace_unavailable"
        assert not launched.handle_path.exists()
        stderr = (iteration_dir / "operation.stderr.log").read_text(encoding="utf-8")
        assert "bwrap: loopback:" in stderr
        assert "AppArmor profile" in stderr
        assert not result_file.exists()
        assert not outside.exists()
        return

    assert launched.started is True
    deadline = time.time() + 10
    while time.time() < deadline:
        status = get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        )
        if status.state is not LongRunningOperationState.RUNNING:
            break
        time.sleep(0.05)
    if status.state is LongRunningOperationState.SUCCEEDED:
        assert result_file.read_text(encoding="utf-8") == "False"
        assert not outside.exists()
    elif shutil.which("codex") is None:
        assert status.state is LongRunningOperationState.FAILED
        assert status.reason == "sandbox_backend_unavailable"
        assert not result_file.exists()
        assert not outside.exists()
    else:
        assert status.state is LongRunningOperationState.FAILED
        stderr = (iteration_dir / "operation.stderr.log").read_text(encoding="utf-8")
        assert "bwrap: loopback:" in stderr
        assert "Operation not permitted" in stderr
        assert not result_file.exists()
        assert not outside.exists()


def test_sandbox_preflight_fails_before_handle_or_child_launch(tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "codex"
    binary.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    marker = tmp_path / "child-started"
    child = tmp_path / "child.py"
    child.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n",
        encoding="utf-8",
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "preflight-denied"
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(child)],
        cwd=tmp_path,
        readable_roots=(tmp_path,),
        writable_roots=(tmp_path,),
        playbook=_PLAYBOOK,
        reason="sandbox_preflight_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert launched.started is False
    assert launched.operation.state is LongRunningOperationState.FAILED
    assert launched.operation.reason == "sandbox_user_namespace_unavailable"
    assert not launched.handle_path.exists()
    assert not marker.exists()
    stderr = (iteration_dir / "operation.stderr.log").read_text(encoding="utf-8")
    assert "RTM_NEWADDR" in stderr
    assert "AppArmor profile" in stderr


def test_monitor_handshake_waits_for_a_slow_successful_preflight(tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "codex"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        "args = sys.argv[1:]\n"
        "if not args or args.pop(0) != 'sandbox': raise SystemExit(2)\n"
        "state = None; network_denied = False\n"
        "while args and args[0].startswith('--'):\n"
        " option = args.pop(0)\n"
        " if option == '--sandbox-state-json': state = json.loads(args.pop(0))\n"
        " elif option == '--sandbox-state-readable-root': args.pop(0)\n"
        " elif option == '--sandbox-state-disable-network': network_denied = True\n"
        "if state is None or not network_denied or not args: raise SystemExit(3)\n"
        "if args == ['/bin/true']: time.sleep(2.2)\n"
        "os.execvp(args[0], args)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    issue_dir = tmp_path / ".cafe" / "issues" / "slow-preflight"
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=["/bin/true"],
        cwd=tmp_path,
        readable_roots=(tmp_path,),
        writable_roots=(tmp_path,),
        playbook=_PLAYBOOK,
        reason="slow_sandbox_preflight_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert launched.started is True
    assert launched.handle_path.exists()
    assert launched.operation.state is LongRunningOperationState.RUNNING
    deadline = time.time() + 5
    status = launched.operation
    while status.state is LongRunningOperationState.RUNNING and time.time() < deadline:
        time.sleep(0.05)
        status = get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        )
    assert status.state is LongRunningOperationState.SUCCEEDED


def test_relative_workflow_paths_survive_distinct_command_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The monitor must not resolve workflow artifacts from the command cwd."""
    workflow_root = tmp_path / "workflow"
    command_root = tmp_path / "disposable-clone"
    user_root = tmp_path / "user-scope"
    workflow_root.mkdir()
    command_root.mkdir()
    user_root.mkdir()
    script = command_root / "install_user_file.py"
    installed_file = user_root / ".local" / "bin" / "cafe"
    shadow_marker = tmp_path / "untrusted-cafe-imported"
    shadow_package = command_root / "cafe"
    shadow_package.mkdir()
    (shadow_package / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(shadow_marker)!r}).write_text('shadowed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "target = Path(sys.argv[1])\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_text('installed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workflow_root)
    issue_dir = Path(".cafe/issues/relative-bootstrap")
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=[sys.executable, str(script), str(installed_file)],
        cwd=command_root,
        readable_roots=(command_root, user_root),
        writable_roots=(user_root,),
        playbook=_PLAYBOOK,
        reason="relative_user_scope_bootstrap_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert launched.handle_path == (workflow_root / iteration_dir / "operation_handle.json")
    assert launched.handle_path.exists()
    request = json.loads(
        (workflow_root / iteration_dir / "operation_monitor_request.json").read_text(
            encoding="utf-8"
        )
    )
    assert request["issue_dir"] == str((workflow_root / issue_dir).resolve())
    assert request["iteration_dir"] == str((workflow_root / iteration_dir).resolve())

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
    assert installed_file.read_text(encoding="utf-8") == "installed"
    assert not shadow_marker.exists()


def test_monitor_exit_before_handle_returns_specific_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExitedMonitor:
        pid = 999_999_999

        @staticmethod
        def poll() -> int:
            return 17

    monkeypatch.setattr(
        operation_helper.subprocess, "Popen", lambda *args, **kwargs: ExitedMonitor()
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "monitor-launch-failure"
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=["true"],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="monitor_launch_failure_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert launched.started is False
    assert launched.operation.state == LongRunningOperationState.FAILED
    assert launched.operation.reason == "operation_monitor_launch_failed"
    assert launched.operation.exit_code == 17
    assert (
        get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        ).reason
        == "operation_monitor_launch_failed"
    )


def test_monitor_handshake_timeout_terminates_monitor_and_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HungMonitor:
        pid = 424_242
        waited = False

        @staticmethod
        def poll() -> None:
            return None

        def wait(self, timeout: float) -> int:
            assert timeout == 3
            self.waited = True
            return -int(signal.SIGTERM)

    monitor = HungMonitor()
    terminated = []
    monkeypatch.setattr(operation_helper.subprocess, "Popen", lambda *args, **kwargs: monitor)
    monkeypatch.setattr(operation_helper, "OPERATION_MONITOR_HANDSHAKE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(
        operation_helper, "_terminate_process_group", lambda pid: terminated.append(pid)
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "monitor-handshake-timeout"
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=["true"],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="monitor_handshake_timeout_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert terminated == [monitor.pid]
    assert monitor.waited is True
    assert launched.started is False
    assert launched.operation.state == LongRunningOperationState.FAILED
    assert launched.operation.reason == "operation_monitor_handshake_timeout"
    claim_fd = operation_helper._acquire_operation_claim(iteration_dir)
    operation_helper._release_operation_claim(iteration_dir, claim_fd)


def test_request_write_failure_returns_terminal_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write_json = operation_helper._write_json

    def fail_request_write(path: Path, data: dict) -> None:
        if path.name == "operation_monitor_request.json":
            raise PermissionError("fixture denied request write")
        original_write_json(path, data)

    monkeypatch.setattr(operation_helper, "_write_json", fail_request_write)
    issue_dir = tmp_path / ".cafe" / "issues" / "request-write-failure"
    iteration_dir = issue_dir / "develop" / "iteration_001"

    launched = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=["true"],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="request_write_failure_probe",
        **_LOW_OPERATION_DECISION,
    )

    assert launched.started is False
    assert launched.operation.state == LongRunningOperationState.FAILED
    assert launched.operation.reason == "operation_monitor_request_write_failed:PermissionError"
    assert not launched.handle_path.exists()
    claim_fd = operation_helper._acquire_operation_claim(iteration_dir)
    operation_helper._release_operation_claim(iteration_dir, claim_fd)
    assert (
        get_operation_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            playbook=_PLAYBOOK,
        ).state
        == LongRunningOperationState.FAILED
    )


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_operation_claim_rejects_links_without_modifying_target(
    tmp_path: Path, link_kind: str
) -> None:
    iteration_dir = tmp_path / link_kind / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    victim = tmp_path / f"{link_kind}-victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    claim_path = iteration_dir / "operation.claim.lock"
    if link_kind == "symlink":
        claim_path.symlink_to(victim)
    else:
        os.link(victim, claim_path)

    with pytest.raises((OSError, ValueError)):
        operation_helper._acquire_operation_claim(iteration_dir)

    assert victim.read_text(encoding="utf-8") == "preserve me"
    with victim.open("r+") as stream:
        operation_helper.fcntl.flock(
            stream.fileno(), operation_helper.fcntl.LOCK_EX | operation_helper.fcntl.LOCK_NB
        )


def test_launcher_crash_releases_claim_for_status_and_duplicate_run(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "launcher-crash"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    child_pid = os.fork()
    if child_pid == 0:
        operation_helper._launch_operation_monitor = lambda **_kwargs: os._exit(23)
        run_operation_command(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            command=["true"],
            cwd=tmp_path,
            playbook=_PLAYBOOK,
            reason="launcher_crash_probe",
            **_LOW_OPERATION_DECISION,
        )
        os._exit(24)

    _, wait_status = os.waitpid(child_pid, 0)
    assert os.waitstatus_to_exitcode(wait_status) == 23
    assert (iteration_dir / "operation.claim.lock").exists()
    assert not (iteration_dir / "operation_handle.json").exists()
    assert not (iteration_dir / "operation_receipt.json").exists()

    status = get_operation_status(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        playbook=_PLAYBOOK,
    )
    assert status.state == LongRunningOperationState.LOST
    assert status.reason == "operation_handle_missing"

    started_at = time.monotonic()
    duplicate = run_operation_command(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        command=["true"],
        cwd=tmp_path,
        playbook=_PLAYBOOK,
        reason="launcher_crash_duplicate_probe",
        **_LOW_OPERATION_DECISION,
    )
    assert time.monotonic() - started_at < 1
    assert duplicate.started is False
    assert duplicate.operation.state == LongRunningOperationState.LOST
    assert duplicate.operation.operation_id == status.operation_id


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


def test_low_risk_silent_single_launch_journey(
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
            **_LOW_OPERATION_DECISION,
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
    persisted = json.loads((iteration_dir / "operation.json").read_text())
    assert persisted["state"] == "running"
    assert persisted["correlation_id"]
    assert len(persisted["command_fingerprint"]) == 64
    assert persisted["effective_boundary"]["cwd"] == str(tmp_path.resolve())
    assert (persisted["risk"], persisted["monitoring"], persisted["log_policy"]) == (
        "low",
        "final-only",
        "summary-only",
    )

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
            **_LOW_OPERATION_DECISION,
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
                **_LOW_OPERATION_DECISION,
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
            **_LOW_OPERATION_DECISION,
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
    assert all(
        result.handle_path.exists()
        or result.operation.state is not LongRunningOperationState.RUNNING
        for result in results
    )


def test_status_waits_for_in_progress_launch_handshake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "status-launch-race"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    release_file = tmp_path / "release-status-race"
    script = tmp_path / "tracked_status_race_wait.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "while not Path(sys.argv[1]).exists():\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    launch_entered = threading.Event()
    allow_launch = threading.Event()
    original_launch = operation_helper._launch_operation_monitor
    launch_results = []
    status_results = []
    errors = []

    def delayed_launch(**kwargs: object) -> object:
        launch_entered.set()
        assert allow_launch.wait(timeout=5)
        return original_launch(**kwargs)

    def run_operation() -> None:
        try:
            launch_results.append(
                run_operation_command(
                    issue_dir=issue_dir,
                    step="develop",
                    iteration_dir=iteration_dir,
                    command=[sys.executable, str(script), str(release_file)],
                    cwd=tmp_path,
                    playbook=_PLAYBOOK,
                    reason="status_launch_race_probe",
                    **_LOW_OPERATION_DECISION,
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def inspect_status() -> None:
        try:
            status_results.append(
                get_operation_status(
                    issue_dir=issue_dir,
                    step="develop",
                    iteration_dir=iteration_dir,
                    playbook=_PLAYBOOK,
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    monkeypatch.setattr(operation_helper, "_launch_operation_monitor", delayed_launch)
    launch_thread = threading.Thread(target=run_operation)
    launch_thread.start()
    assert launch_entered.wait(timeout=5)
    status_thread = threading.Thread(target=inspect_status)
    status_thread.start()
    time.sleep(0.05)
    assert status_thread.is_alive()
    allow_launch.set()
    launch_thread.join(timeout=5)
    status_thread.join(timeout=5)

    try:
        assert not launch_thread.is_alive()
        assert not status_thread.is_alive()
        assert not errors
        assert len(launch_results) == 1
        assert len(status_results) == 1
        assert status_results[0].state == LongRunningOperationState.RUNNING
        assert status_results[0].reason != "operation_handle_missing"
    finally:
        release_file.write_text("go", encoding="utf-8")
        deadline = time.time() + 5
        while time.time() < deadline:
            terminal = get_operation_status(
                issue_dir=issue_dir,
                step="develop",
                iteration_dir=iteration_dir,
                playbook=_PLAYBOOK,
            )
            if terminal.state is not LongRunningOperationState.RUNNING:
                break
            time.sleep(0.05)


def test_medium_risk_replayable_journey_preserves_policy_and_single_launch(
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
        risk=OperationRisk.MEDIUM,
        monitoring=OperationMonitoring.PERIODIC,
        log_policy=OperationLogPolicy.INCREMENTAL_TAIL,
        stop_condition="stop if the integration fixture reports failure",
        recovery="inspect the same operation id before retrying",
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
    assert status.execution_class == "sandbox"
    assert status.trust_source == "workflow"
    assert status.effective_boundary["writable_roots"] == [str(tmp_path.resolve())]
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
        **_LOW_OPERATION_DECISION,
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


def test_high_risk_explicit_stop_and_recovery_journey_preserves_policy(
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
        risk=OperationRisk.HIGH,
        monitoring=OperationMonitoring.ACTIVE,
        log_policy=OperationLogPolicy.FILTERED_STREAM,
        stop_condition="stop if the fake high-risk operation cannot be verified",
        recovery="inspect the same operation id before recovery",
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

    # The monitor allows its child the full two-second graceful shutdown window
    # before escalating to SIGKILL, so the observer must wait longer than that.
    deadline = time.time() + 5
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
    persisted = json.loads((iteration_dir / "operation.json").read_text())
    assert persisted["state"] == "lost"
    assert (persisted["risk"], persisted["monitoring"], persisted["log_policy"]) == (
        "high",
        "active",
        "filtered-stream",
    )
    assert persisted["stop_condition"] == "stop if the fake high-risk operation cannot be verified"
    assert persisted["recovery"] == "inspect the same operation id before recovery"


def test_agent_timeout_without_a_launch_decision_creates_no_operation(
    tmp_path: Path,
) -> None:
    """A timeout cannot create a low-risk policy or an operation identity."""
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

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=crashing_executor
    ).run(start_step="develop", single_step=True)

    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert result.final_status_code.startswith("INTERRUPTED")
    assert not (iteration_dir / "operation.json").exists()


def test_timeout_does_not_block_a_later_explicit_operation_launch(
    tmp_path: Path,
) -> None:
    """A later agent invocation remains responsible for any new launch decision."""
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

    def explicit_operation_executor(
        step_name: str, step_def: dict, state: object
    ) -> StepExecutionResult:
        return StepExecutionResult(response="completed", artifacts={}, status_code="confirmed")

    second_run = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=_PLAYBOOK, executor=explicit_operation_executor
    )
    second_result = second_run.run(start_step="develop", single_step=True)

    assert second_result.final_status_code == "confirmed"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    assert not (iteration_dir / "operation.json").exists()
