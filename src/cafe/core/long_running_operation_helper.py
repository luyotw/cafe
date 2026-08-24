"""Controlled helper for one long-running workflow operation.

This module intentionally implements a narrow lifecycle, not a queue:
one command for one phase iteration, one ``operation.json``, one
``operation_handle.json``, and one terminal ``operation_receipt.json``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import stat
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
    OperationLogPolicy,
    OperationMonitoring,
    OperationRisk,
    validate_operation_decision,
)
from cafe.core.execution_boundary import EffectiveBoundary
from cafe.core.sandbox_execution import sandbox_command
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

OPERATION_HANDLE_FILENAME = "operation_handle.json"
OPERATION_MONITOR_REQUEST_FILENAME = "operation_monitor_request.json"
OPERATION_MONITOR_STDERR_FILENAME = "operation_monitor.stderr.log"
OPERATION_CLAIM_LOCK_FILENAME = "operation.claim.lock"
OPERATION_MONITOR_HANDSHAKE_TIMEOUT_SECONDS = 2.0


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
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(
        json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary_path, path)


def _read_json(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return raw


def _operation_handle_ready(path: Path, *, operation_id: str) -> bool:
    """Return whether the monitor has durably registered the command process."""
    try:
        handle = _read_json(path)
        monitor_pid = int(handle.get("monitor_pid") or 0)
        command_pid = int(handle.get("command_pid") or 0)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return False
    return handle.get("operation_id") == operation_id and monitor_pid > 0 and command_pid > 0


def _read_launch_receipt(
    store: BlackboardStore, iteration_dir: Path
) -> Optional[LongRunningOperationArtifact]:
    """Tolerate observing the receipt while the monitor is still persisting it."""
    try:
        return store.read_operation_receipt(iteration_dir)
    except (OSError, ValueError):
        return None


def _acquire_operation_claim(iteration_dir: Path) -> int:
    """Claim the fixed operation slot with a process-lifetime advisory lock."""
    lock_path = _claim_lock_path(iteration_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    deadline = time.time() + 5
    try:
        file_info = os.fstat(fd)
        if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1:
            raise ValueError("operation claim lock must be a single-link regular file")
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError("timed out waiting for operation claim lock")
                time.sleep(0.01)
                continue
            return fd
    except BaseException:
        os.close(fd)
        raise


def _release_operation_claim(_iteration_dir: Path, fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def _launch_operation_monitor(
    *,
    store: BlackboardStore,
    operation: LongRunningOperationArtifact,
    issue_dir: Path,
    step: str,
    iteration_dir: Path,
    command: Sequence[str],
    playbook: Dict[str, Any],
    cwd_path: Path,
    read_paths: Sequence[Path],
    write_paths: Sequence[Path],
) -> OperationLaunchResult:
    request = {
        "issue_dir": str(issue_dir),
        "step": step,
        "iteration_dir": str(iteration_dir),
        "operation_id": operation.operation_id,
        "command": list(command),
        "cwd": str(cwd_path),
        "playbook": playbook,
        "created_at": _now_iso(),
        "execution_class": "sandbox",
        "trust_source": "workflow",
        "readable_roots": [str(root) for root in read_paths],
        "writable_roots": [str(root) for root in write_paths],
    }
    request_file = _request_path(iteration_dir)
    try:
        _write_json(request_file, request)
    except (OSError, TypeError, ValueError) as exc:
        failed = _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.FAILED,
            reason=f"operation_monitor_request_write_failed:{exc.__class__.__name__}",
        )
        return OperationLaunchResult(
            operation=failed,
            started=False,
            handle_path=operation_handle_path(iteration_dir),
        )

    monitor_stderr_path = iteration_dir / OPERATION_MONITOR_STDERR_FILENAME
    trusted_import_root = Path(__file__).resolve().parents[2]
    monitor_environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    try:
        with monitor_stderr_path.open("ab") as monitor_stderr:
            monitor_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cafe.core.long_running_operation_helper",
                    "monitor",
                    str(request_file),
                ],
                cwd=str(trusted_import_root),
                env=monitor_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=monitor_stderr,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        failed = _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.FAILED,
            reason=f"operation_monitor_launch_failed:{exc.__class__.__name__}",
        )
        return OperationLaunchResult(
            operation=failed,
            started=False,
            handle_path=operation_handle_path(iteration_dir),
        )

    handle_path = operation_handle_path(iteration_dir)
    deadline = time.time() + OPERATION_MONITOR_HANDSHAKE_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _operation_handle_ready(handle_path, operation_id=operation.operation_id):
            return OperationLaunchResult(
                operation=operation,
                started=True,
                handle_path=handle_path,
            )
        monitor_exit_code = monitor_process.poll()
        if monitor_exit_code is not None:
            receipt = _read_launch_receipt(store, iteration_dir)
            if receipt is None:
                receipt = _record_terminal_receipt(
                    issue_dir=issue_dir,
                    playbook=playbook,
                    step=step,
                    iteration_dir=iteration_dir,
                    operation_id=operation.operation_id,
                    state=LongRunningOperationState.FAILED,
                    reason="operation_monitor_launch_failed",
                    exit_code=monitor_exit_code,
                )
            return OperationLaunchResult(
                operation=receipt,
                started=False,
                handle_path=handle_path,
            )
        time.sleep(0.02)

    _terminate_process_group(monitor_process.pid)
    monitor_process.wait(timeout=3)
    receipt = _read_launch_receipt(store, iteration_dir)
    if receipt is None:
        receipt = _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            state=LongRunningOperationState.FAILED,
            reason="operation_monitor_handshake_timeout",
        )
    return OperationLaunchResult(
        operation=receipt,
        started=False,
        handle_path=handle_path,
    )


def run_operation_command(
    *,
    issue_dir: Path,
    step: str,
    iteration_dir: Path,
    command: Sequence[str],
    playbook: Dict[str, Any],
    risk: OperationRisk,
    monitoring: OperationMonitoring,
    log_policy: OperationLogPolicy,
    stop_condition: str,
    recovery: str,
    cwd: Optional[Path] = None,
    readable_roots: Optional[Sequence[Path]] = None,
    writable_roots: Optional[Sequence[Path]] = None,
    reason: str = "operation_helper_launch",
) -> OperationLaunchResult:
    """Launch one supervised command for one workflow iteration.

    If the iteration already has a running operation, this returns that same
    operation identity and does not launch a duplicate command.
    """
    if not command:
        raise ValueError("operation command must not be empty")
    validate_operation_decision(
        risk=risk,
        monitoring=monitoring,
        log_policy=log_policy,
        stop_condition=stop_condition,
        recovery=recovery,
    )
    issue_dir = Path(issue_dir).resolve()
    iteration_dir = Path(iteration_dir).resolve()
    cwd_path = (Path(cwd) if cwd is not None else Path.cwd()).resolve()
    read_paths = tuple(Path(root).resolve() for root in (readable_roots or (cwd_path,)))
    write_paths = tuple(Path(root).resolve() for root in (writable_roots or (cwd_path,)))
    canonical_command = json.dumps(
        {"command": list(command), "cwd": str(cwd_path.resolve())},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    command_fingerprint = hashlib.sha256(canonical_command.encode("utf-8")).hexdigest()

    store = BlackboardStore(issue_dir)
    claim_fd = _acquire_operation_claim(iteration_dir)
    try:
        state = store.load_or_create(step)
        existing = store.read_operation_artifact(iteration_dir)
        if existing is not None:
            receipt = store.read_operation_receipt(iteration_dir)
            return OperationLaunchResult(
                operation=receipt or existing,
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
                risk=risk,
                monitoring=monitoring,
                log_policy=log_policy,
                stop_condition=stop_condition,
                recovery=recovery,
                effective_boundary={
                    "cwd": str(cwd_path.resolve()),
                    "readable_roots": [str(root) for root in read_paths],
                    "writable_roots": [str(root) for root in write_paths],
                    "network_destinations": [],
                    "environment_keys": sorted(
                        EffectiveBoundary(
                            cwd=cwd_path,
                            readable_roots=read_paths,
                            writable_roots=write_paths,
                            environment=os.environ,
                        ).environment
                    ),
                },
                command_fingerprint=command_fingerprint,
            ),
        )
        return _launch_operation_monitor(
            store=store,
            operation=operation,
            issue_dir=issue_dir,
            step=step,
            iteration_dir=iteration_dir,
            command=command,
            playbook=playbook,
            cwd_path=cwd_path,
            read_paths=read_paths,
            write_paths=write_paths,
        )
    finally:
        _release_operation_claim(iteration_dir, claim_fd)


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
        try:
            claim_fd = _acquire_operation_claim(iteration_dir)
        except TimeoutError:
            return operation
        try:
            receipt = store.read_operation_receipt(iteration_dir)
            if receipt is not None:
                return receipt
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
        finally:
            _release_operation_claim(iteration_dir, claim_fd)

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
    if (
        monitor_pid > 0
        and _pid_alive(monitor_pid)
        and (
            recorded_start is None
            or current_start is None
            or str(recorded_start) == str(current_start)
        )
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
    boundary = EffectiveBoundary(
        cwd=cwd,
        readable_roots=tuple(Path(root) for root in request["readable_roots"]),
        writable_roots=tuple(Path(root) for root in request["writable_roots"]),
        network_destinations=(),
        environment=os.environ,
    )

    handle_path = operation_handle_path(iteration_dir)
    handle = {
        "operation_id": operation_id,
        "monitor_pid": os.getpid(),
        "monitor_start_time": _pid_start_time(os.getpid()),
        "command_pid": None,
        "command_start_time": None,
        "command": command,
        "cwd": str(cwd),
        "created_at": _now_iso(),
    }
    _write_json(handle_path, handle)

    try:
        command = sandbox_command(command, boundary=boundary)
    except RuntimeError:
        _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation_id,
            state=LongRunningOperationState.FAILED,
            reason="sandbox_backend_unavailable",
        )
        return 1

    stdout_path = iteration_dir / "operation.stdout.log"
    stderr_path = iteration_dir / "operation.stderr.log"
    stdout = None
    stderr = None
    try:
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
    except OSError as exc:
        for stream in (stdout, stderr):
            if stream is not None:
                stream.close()
        _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation_id,
            state=LongRunningOperationState.FAILED,
            reason=f"operation_command_log_open_failed:{exc.__class__.__name__}",
        )
        return 1

    process: Optional[subprocess.Popen[bytes]] = None
    previous_handlers = {}

    def exit_after_cleanup(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous_handlers[signum] = signal.signal(signum, exit_after_cleanup)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(boundary.environment),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
        handle.update(
            {
                "command_pid": process.pid,
                "command_start_time": _pid_start_time(process.pid),
                "command": command,
            }
        )
        _write_json(handle_path, handle)
        exit_code = process.wait()
    except OSError as exc:
        _record_terminal_receipt(
            issue_dir=issue_dir,
            playbook=playbook,
            step=step,
            iteration_dir=iteration_dir,
            operation_id=operation_id,
            state=LongRunningOperationState.FAILED,
            reason=f"operation_command_launch_failed:{exc.__class__.__name__}",
        )
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if process is not None and process.poll() is None:
            _terminate_process_group(process.pid)
        stdout.close()
        stderr.close()

    terminal_state = (
        LongRunningOperationState.SUCCEEDED if exit_code == 0 else LongRunningOperationState.FAILED
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
        print(
            "usage: python -m cafe.core.long_running_operation_helper monitor REQUEST_JSON",
            file=sys.stderr,
        )
        return 2
    try:
        return _monitor(Path(args[1]))
    except KeyboardInterrupt:
        return int(signal.SIGINT)


if __name__ == "__main__":
    raise SystemExit(main())
