"""Bounded host execution and fallback selection for registered capabilities."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from typing import Any, BinaryIO, Callable, Literal, Protocol

from cafe.agents.capabilities.contracts import (
    CapabilityFallback,
    CapabilityProvider,
    CapabilityRequest,
    CapabilitySelection,
    CapabilityTelemetry,
)
from cafe.agents.capabilities.registry import CapabilityRegistry
from cafe.agents.diagnostics import sanitize_error_excerpt

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class CapabilityOutputLimitError(RuntimeError):
    """Raised after a child is stopped for exceeding a capture limit."""

    def __init__(self, stream_name: str, limit: int) -> None:
        super().__init__(f"{stream_name} exceeded the {limit}-byte output limit")
        self.stream_name = stream_name
        self.limit = limit


class _ProcessJob(Protocol):
    def assign(self, process: subprocess.Popen[bytes]) -> None:
        """Assign the launched process to the owned tree."""

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        """Resume the process only after tree ownership is established."""

    def terminate(self) -> None:
        """Terminate every process in the owned tree."""

    def close(self) -> None:
        """Release the tree handle, killing remaining descendants when configured."""


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJob:
    """Own a Windows process tree with kill-on-close semantics."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, handle: Any, kernel32: Any, ntdll: Any) -> None:
        self.handle = handle
        self.kernel32 = kernel32
        self.ntdll = ntdll

    @classmethod
    def create(cls) -> "_WindowsJob":
        windll = getattr(ctypes, "WinDLL", None)
        if windll is None:  # pragma: no cover - defensive outside Windows.
            raise OSError("Windows Job Objects are unavailable")
        kernel32 = windll("kernel32", use_last_error=True)
        ntdll = windll("ntdll")
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = wintypes.LONG
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _windows_api_error("CreateJobObjectW")

        information = _JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle,
            cls._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not configured:
            kernel32.CloseHandle(handle)
            raise _windows_api_error("SetInformationJobObject")
        return cls(handle, kernel32, ntdll)

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not self.kernel32.AssignProcessToJobObject(
            self.handle,
            process_handle,
        ):
            raise _windows_api_error("AssignProcessToJobObject")

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise OSError("suspended Windows process handle is unavailable")
        status = self.ntdll.NtResumeProcess(process_handle)
        if status != 0:
            raise OSError(int(status), "NtResumeProcess failed")

    def terminate(self) -> None:
        if self.handle and not self.kernel32.TerminateJobObject(self.handle, 1):
            raise _windows_api_error("TerminateJobObject")

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel32.CloseHandle(handle):
                raise _windows_api_error("CloseHandle")


def _windows_api_error(operation: str) -> OSError:
    get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
    return OSError(get_last_error(), f"{operation} failed")


def _create_process_job() -> _ProcessJob | None:
    return _WindowsJob.create() if os.name == "nt" else None


class CapabilityResolver:
    """Prefer a compatible registered native provider, then select a skill fallback."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        runner: CommandRunner | None = None,
        probe_timeout_seconds: int = 5,
        execution_timeout_seconds: int = 10 * 60,
        max_output_bytes: int = 64 * 1024,
    ) -> None:
        if probe_timeout_seconds <= 0 or execution_timeout_seconds <= 0:
            raise ValueError("capability timeouts must be positive")
        if max_output_bytes <= 0:
            raise ValueError("capability output limit must be positive")
        self.registry = registry
        # A supplied runner is an injection seam for deterministic tests. Production
        # uses the streaming implementation below so output is bounded while it is read.
        self.runner = runner
        self.probe_timeout_seconds = probe_timeout_seconds
        self.execution_timeout_seconds = execution_timeout_seconds
        self.max_output_bytes = max_output_bytes

    def select(
        self,
        request: CapabilityRequest,
        fallback: CapabilityFallback,
        *,
        enable_native: bool = True,
    ) -> CapabilitySelection:
        """Run the registered native provider or return the prepared fallback."""
        if not enable_native:
            return self._fallback(
                request,
                fallback,
                "native execution is disabled for mock or synthetic agents",
            )

        provider = self.registry.resolve(request.capability_id, request.cli)
        if provider is None:
            return self._fallback(
                request,
                fallback,
                f"{request.cli.value} has no compatible native {request.label}",
            )

        try:
            environment = self._validated_environment(provider.build_environment(request))
        except (TypeError, ValueError) as exc:
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} provided an invalid child environment: "
                f"{sanitize_error_excerpt(exc)}",
            )

        probe_error = self._probe(provider, request, environment)
        if probe_error is not None:
            return self._fallback(request, fallback, probe_error)

        started_at = time.monotonic()
        try:
            result = self._run_command(
                provider.build_command(request),
                cwd=request.project_root,
                environment=environment,
                timeout_seconds=self.execution_timeout_seconds,
            )
        except CapabilityOutputLimitError as exc:
            telemetry = self._telemetry(provider, started_at, outcome="failed")
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} output was rejected: {exc}",
                telemetry=telemetry,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            telemetry = self._telemetry(provider, started_at, outcome="failed")
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} failed: {sanitize_error_excerpt(exc)}",
                telemetry=telemetry,
            )

        output = (result.stdout or "").strip()
        if result.returncode != 0 or not output:
            detail = (result.stderr or output or f"exit code {result.returncode}").strip()
            telemetry = self._telemetry(provider, started_at, outcome="failed")
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} was unusable: "
                f"{sanitize_error_excerpt(RuntimeError(detail))}",
                telemetry=telemetry,
            )
        try:
            output = provider.normalize_output(output)
            self._validate_output_size(output, "normalized stdout")
        except CapabilityOutputLimitError as exc:
            telemetry = self._telemetry(provider, started_at, outcome="failed")
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} output was rejected: {exc}",
                telemetry=telemetry,
            )
        except (ValueError, UnicodeError) as exc:
            telemetry = self._telemetry(provider, started_at, outcome="failed")
            return self._fallback(
                request,
                fallback,
                f"{provider.provider_id} returned incompatible output: "
                f"{sanitize_error_excerpt(exc)}",
                telemetry=telemetry,
            )

        return CapabilitySelection(
            capability_id=request.capability_id,
            provider_id=provider.provider_id,
            mode="native_command",
            output=output,
            telemetry=self._telemetry(provider, started_at, outcome="completed"),
        )

    def _probe(
        self,
        provider: CapabilityProvider,
        request: CapabilityRequest,
        environment: dict[str, str],
    ) -> str | None:
        try:
            result = self._run_command(
                provider.probe_command(request),
                cwd=request.project_root,
                environment=environment,
                timeout_seconds=self.probe_timeout_seconds,
            )
        except CapabilityOutputLimitError as exc:
            return f"{provider.provider_id} probe output was rejected: {exc}"
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return f"{provider.provider_id} probe failed: {sanitize_error_excerpt(exc)}"
        if not provider.accepts_probe(result):
            return f"{provider.provider_id} did not satisfy its compatibility probe"
        return None

    def _run_command(
        self,
        command: Sequence[str],
        *,
        cwd: os.PathLike[str],
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        if self.runner is None:
            return _run_bounded_process(
                command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                max_output_bytes=self.max_output_bytes,
            )
        result = self.runner(
            list(command),
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
        self._validate_output_size(result.stdout or "", "stdout")
        self._validate_output_size(result.stderr or "", "stderr")
        return result

    @staticmethod
    def _validated_environment(raw: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(raw, Mapping):
            raise TypeError("provider environment must be a mapping")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
            raise TypeError("provider environment keys and values must be strings")
        return dict(raw)

    @staticmethod
    def _fallback(
        request: CapabilityRequest,
        fallback: CapabilityFallback,
        reason: str,
        *,
        telemetry: CapabilityTelemetry | None = None,
    ) -> CapabilitySelection:
        return CapabilitySelection(
            capability_id=request.capability_id,
            provider_id=fallback.provider_id,
            mode="fallback_skill",
            fallback_invocation=fallback.invocation,
            fallback_reason=reason,
            telemetry=telemetry,
        )

    @staticmethod
    def _telemetry(
        provider: CapabilityProvider,
        started_at: float,
        *,
        outcome: Literal["completed", "failed"],
    ) -> CapabilityTelemetry:
        return CapabilityTelemetry(
            capability_id=provider.capability_id,
            provider_id=provider.provider_id,
            cli=provider.cli,
            outcome=outcome,
            duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
        )

    def _validate_output_size(self, output: str, stream_name: str) -> None:
        if len(output.encode("utf-8")) > self.max_output_bytes:
            raise CapabilityOutputLimitError(stream_name, self.max_output_bytes)


def _run_bounded_process(
    command: Sequence[str],
    *,
    cwd: os.PathLike[str],
    environment: dict[str, str],
    timeout_seconds: int,
    max_output_bytes: int,
) -> subprocess.CompletedProcess[str]:
    """Capture a child incrementally and stop it before buffers can grow unbounded."""
    process_job = _create_process_job()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            start_new_session=os.name == "posix",
            creationflags=(
                (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
                )
                if os.name == "nt"
                else 0
            ),
        )
        if process_job is not None:
            process_job.assign(process)
            process_job.resume(process)
    except Exception:
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        if process_job is not None:
            process_job.close()
        raise

    try:
        return _capture_bounded_process(
            process,
            command=command,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            process_job=process_job,
        )
    finally:
        if process_job is not None:
            process_job.close()


def _capture_bounded_process(
    process: subprocess.Popen[bytes],
    *,
    command: Sequence[str],
    timeout_seconds: int,
    max_output_bytes: int,
    process_job: _ProcessJob | None,
) -> subprocess.CompletedProcess[str]:
    assert process.stdout is not None
    assert process.stderr is not None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    overflow_stream: list[str] = []
    capture_errors: list[OSError] = []
    lock = threading.Lock()

    def capture(stream_name: str, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = os.read(stream.fileno(), 8192)
                if not chunk:
                    return
                with lock:
                    remaining = max_output_bytes - len(buffers[stream_name])
                    buffers[stream_name].extend(chunk[: max(remaining, 0)])
                    if len(chunk) > remaining:
                        if not overflow_stream:
                            overflow_stream.append(stream_name)
                        overflow.set()
                        return
        except OSError as exc:
            capture_errors.append(exc)

    threads = [
        threading.Thread(target=capture, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=capture, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    capture_failed = False
    while process.poll() is None or any(thread.is_alive() for thread in threads):
        if overflow.wait(timeout=0.01):
            break
        if capture_errors:
            capture_failed = True
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break

    if overflow.is_set() or timed_out or capture_failed:
        _kill_process_group(process, process_job=process_job)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process_group(process, process_job=process_job)
            process.wait(timeout=2)

    for thread in threads:
        thread.join(timeout=2)
    for stream, thread in zip((process.stdout, process.stderr), threads):
        if not thread.is_alive():
            stream.close()

    if overflow.is_set():
        raise CapabilityOutputLimitError(
            overflow_stream[0] if overflow_stream else "output",
            max_output_bytes,
        )
    if timed_out:
        raise subprocess.TimeoutExpired(list(command), timeout_seconds)
    if capture_errors:
        raise capture_errors[0]

    stdout = bytes(buffers["stdout"]).decode("utf-8", errors="replace")
    stderr = bytes(buffers["stderr"]).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def _kill_process_group(
    process: subprocess.Popen[bytes],
    *,
    process_job: _ProcessJob | None = None,
) -> None:
    """Kill the isolated child group even when its original parent already exited."""
    try:
        if process_job is not None:
            try:
                process_job.terminate()
            finally:
                process_job.close()
        elif os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:  # pragma: no cover - exercised on Windows CI.
            process.kill()
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
