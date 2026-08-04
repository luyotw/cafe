"""Controlled helper for one long-running workflow operation.

This module intentionally implements a narrow lifecycle, not a queue:
one command for one phase iteration, one ``operation.json``, one
``operation_handle.json``, and one terminal ``operation_receipt.json``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from cafe.core.blackboard import (
    BlackboardStore,
    LongRunningOperationArtifact,
    LongRunningOperationState,
    operation_receipt_path,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

OPERATION_HANDLE_FILENAME = "operation_handle.json"
OPERATION_MONITOR_REQUEST_FILENAME = "operation_monitor_request.json"
OPERATION_CLAIM_LOCK_FILENAME = "operation.claim.lock"


@dataclass(frozen=True)
class OperationLaunchResult:
    operation: LongRunningOperationArtifact
    started: bool
    handle_path: Path


def operation_handle_path(iteration_dir: Path) -> Path:
    return Path(iteration_dir) / OPERATION_HANDLE_FILENAME


def _request_path(iteration_dir: Path) -> Path:
    return Path(iteration_dir) / OPERATION_MONITOR_REQUEST_FILENAME


def _claim_lock_path(iteration_dir: Path) -> Path:
    return Path(iteration_dir) / OPERATION_CLAIM_LOCK_FILENAME


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _pid_start_time(pid: int) -> Optional[str]:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(") ", 1)[1].split()[19]
    except IndexError:
        return None


def _pid_state(pid: int) -> Optional[str]:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(") ", 1)[1].split()[0]
    except IndexError:
        return None


def _pid_alive(pid: int) -> bool:
    if _pid_state(pid) == "Z":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 2
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.02)
    if not _pid_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return raw


def _acquire_operation_claim(iteration_dir: Path) -> int:
    """Atomically claim the fixed operation slot for this iteration."""
    lock_path = _claim_lock_path(iteration_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() >= deadline:
                raise TimeoutError("timed out waiting for operation claim lock")
            time.sleep(0.01)
            continue
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        return fd


def _release_operation_claim(iteration_dir: Path, fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            _claim_lock_path(iteration_dir).unlink()
        except FileNotFoundError:
            pass


def _unused_executor(*_args: object, **_kwargs: object) -> StepExecutionResult:
    raise RuntimeError("operation helper receipt recording must not execute workflow steps")


def _record_terminal_receipt(
    *,
    issue_dir: Path,
    playbook: Dict[str, Any],
    step: str,
    iteration_dir: Path,
    operation_id: str,
    state: LongRunningOperationState,
    reason: str,
    exit_code: Optional[int] = None,
) -> LongRunningOperationArtifact:
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=_unused_executor,
    )
    return runtime.record_long_running_operation_receipt(
        step=step,
        iteration_dir=iteration_dir,
        operation_id=operation_id,
        state=state,
        reason=reason,
        exit_code=exit_code,
    )


def run_operation_command(
    *,
    issue_dir: Path,
    step: str,
    iteration_dir: Path,
    command: Sequence[str],
    cwd: Optional[Path] = None,
    playbook: Dict[str, Any],
    reason: str = "operation_helper_launch",
) -> OperationLaunchResult:
    """Launch one supervised command for one workflow iteration.

    If the iteration already has a running operation, this returns that same
    operation identity and does not launch a duplicate command.
    """
    if not command:
        raise ValueError("operation command must not be empty")
    issue_dir = Path(issue_dir)
    iteration_dir = Path(iteration_dir)
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()

    store = BlackboardStore(issue_dir)
    claim_fd = _acquire_operation_claim(iteration_dir)
    try:
        state = store.load_or_create(step)
        existing = store.read_operation_artifact(iteration_dir)
        if existing is not None:
            return OperationLaunchResult(
                operation=existing,
                started=False,
                handle_path=operation_handle_path(iteration_dir),
            )

        operation = store.write_operation_artifact(
            state,
            step=step,
            iteration_dir=iteration_dir,
            artifact=LongRunningOperationArtifact(
                state=LongRunningOperationState.RUNNING,
                reason=reason,
            ),
        )
    finally:
        _release_operation_claim(iteration_dir, claim_fd)

    request = {
        "issue_dir": str(issue_dir),
        "step": step,
        "iteration_dir": str(iteration_dir),
        "operation_id": operation.operation_id,
        "command": list(command),
        "cwd": str(cwd_path),
        "playbook": playbook,
        "created_at": _now_iso(),
    }
    request_file = _request_path(iteration_dir)
    _write_json(request_file, request)

    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cafe.core.long_running_operation_helper",
            "monitor",
            str(request_file),
        ],
        cwd=str(cwd_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    handle_path = operation_handle_path(iteration_dir)
    deadline = time.time() + 2
    while not handle_path.exists() and time.time() < deadline:
        time.sleep(0.02)

    return OperationLaunchResult(operation=operation, started=True, handle_path=handle_path)


def get_operation_status(
    *,
    issue_dir: Path,
    step: str,
    iteration_dir: Path,
    playbook: Dict[str, Any],
) -> LongRunningOperationArtifact:
    """Return status for the existing operation without launching anything."""
    issue_dir = Path(issue_dir)
    iteration_dir = Path(iteration_dir)
    store = BlackboardStore(issue_dir)
    operation = store.read_operation_artifact(iteration_dir)
    if operation is None:
        raise ValueError("operation artifact is missing")

    receipt = store.read_operation_receipt(iteration_dir)
    if receipt is not None:
        return receipt

    handle_path = operation_handle_path(iteration_dir)
    if not handle_path.exists():
        return _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.LOST,
            reason="operation_handle_missing",
        )

    try:
        handle = _read_json(handle_path)
    except (json.JSONDecodeError, ValueError, OSError):
        return _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.LOST,
            reason="operation_handle_invalid",
        )

    if handle.get("operation_id") != operation.operation_id:
        return _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.LOST,
            reason="operation_handle_identity_mismatch",
        )

    try:
        monitor_pid = int(handle.get("monitor_pid") or 0)
    except (TypeError, ValueError):
        return _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.LOST,
            reason="operation_handle_invalid",
        )
    recorded_start = handle.get("monitor_start_time")
    current_start = _pid_start_time(monitor_pid)
    if monitor_pid > 0 and _pid_alive(monitor_pid) and (
        recorded_start is None or current_start is None or str(recorded_start) == str(current_start)
    ):
        return operation

    receipt = store.read_operation_receipt(iteration_dir)
    if receipt is not None:
        return receipt
    return _record_terminal_receipt(
        issue_dir=issue_dir,
        playbook=playbook,
        step=step,
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        state=LongRunningOperationState.LOST,
        reason="operation_helper_unverifiable",
    )


def _monitor(request_file: Path) -> int:
    request = _read_json(request_file)
    issue_dir = Path(str(request["issue_dir"]))
    step = str(request["step"])
    iteration_dir = Path(str(request["iteration_dir"]))
    operation_id = str(request["operation_id"])
    command = [str(part) for part in request["command"]]
    cwd = Path(str(request["cwd"]))
    playbook = dict(request["playbook"])

    stdout_path = iteration_dir / "operation.stdout.log"
    stderr_path = iteration_dir / "operation.stderr.log"
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
        previous_handlers = {}

        def exit_after_cleanup(signum: int, _frame: object) -> None:
            raise SystemExit(128 + signum)

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous_handlers[signum] = signal.signal(signum, exit_after_cleanup)
        try:
            _write_json(
                operation_handle_path(iteration_dir),
                {
                    "operation_id": operation_id,
                    "monitor_pid": os.getpid(),
                    "monitor_start_time": _pid_start_time(os.getpid()),
                    "command_pid": process.pid,
                    "command_start_time": _pid_start_time(process.pid),
                    "command": command,
                    "cwd": str(cwd),
                    "created_at": _now_iso(),
                },
            )
            exit_code = process.wait()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            if process.poll() is None:
                _terminate_process_group(process.pid)

    terminal_state = (
        LongRunningOperationState.SUCCEEDED
        if exit_code == 0
        else LongRunningOperationState.FAILED
    )
    _record_terminal_receipt(
        issue_dir=issue_dir,
        playbook=playbook,
        step=step,
        iteration_dir=iteration_dir,
        operation_id=operation_id,
        state=terminal_state,
        reason="operation_helper_exit",
        exit_code=exit_code,
    )
    return exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2 or args[0] != "monitor":
        print("usage: python -m cafe.core.long_running_operation_helper monitor REQUEST_JSON", file=sys.stderr)
        return 2
    try:
        return _monitor(Path(args[1]))
    except KeyboardInterrupt:
        return int(signal.SIGINT)


if __name__ == "__main__":
    raise SystemExit(main())
