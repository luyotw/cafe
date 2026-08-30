"""Generic native capability routing and bounded fallback behavior."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest

import cafe.agents.capabilities.runner as capability_runner_module
from cafe.agents.capabilities import (
    CapabilityFallback,
    CapabilityRegistry,
    CapabilityRequest,
    CapabilityResolver,
)
from cafe.core.types import AgentCLI


class RecordingRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        return self.results.pop(0)


def completed(command: list[str], *, stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


@dataclass(frozen=True)
class ExampleProvider:
    capability_id: str = "example.inspect"
    provider_id: str = "example-native"
    cli: AgentCLI = AgentCLI.CODEX

    def probe_command(self, request: CapabilityRequest) -> Sequence[str]:
        del request
        return ("example", "--help")

    def accepts_probe(self, result: subprocess.CompletedProcess[str]) -> bool:
        return result.returncode == 0 and "native example" in (result.stdout or "")

    def build_command(self, request: CapabilityRequest) -> Sequence[str]:
        return ("example", request.require_parameter("target"))

    def build_environment(self, request: CapabilityRequest) -> Mapping[str, str]:
        del request
        return {"SAFE_CHILD": "1"}

    def normalize_output(self, output: str) -> str:
        return output.upper()


def request(tmp_path: Path, *, cli: AgentCLI = AgentCLI.CODEX) -> CapabilityRequest:
    return CapabilityRequest(
        capability_id="example.inspect",
        cli=cli,
        project_root=tmp_path,
        label="example capability",
        parameters={"target": "change"},
    )


def fallback() -> CapabilityFallback:
    return CapabilityFallback(provider_id="example-skill", invocation="$example-skill")


def test_registered_provider_runs_through_bounded_host_contract(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["example"], stdout="native example"),
            completed(["example"], stdout="abcdef"),
        ]
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([ExampleProvider()]),
        runner=runner,
        probe_timeout_seconds=3,
        execution_timeout_seconds=7,
        max_output_bytes=64,
    )

    result = resolver.select(request(tmp_path), fallback())

    assert result.mode == "native_command"
    assert result.provider_id == "example-native"
    assert result.output == "ABCDEF"
    assert result.telemetry is not None
    assert result.telemetry.capability_id == "example.inspect"
    assert result.telemetry.outcome == "completed"
    assert runner.calls[0][1]["timeout"] == 3
    assert runner.calls[0][1]["env"] == {"SAFE_CHILD": "1"}
    assert runner.calls[1][1]["timeout"] == 7
    assert runner.calls[1][1]["env"] == {"SAFE_CHILD": "1"}


def test_injected_runner_output_overflow_fails_closed(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["example"], stdout="native example"),
            completed(["example"], stdout="x" * 65),
        ]
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([ExampleProvider()]),
        runner=runner,
        max_output_bytes=64,
    )

    result = resolver.select(request(tmp_path), fallback())

    assert result.mode == "fallback_skill"
    assert "stdout exceeded the 64-byte output limit" in (result.fallback_reason or "")
    assert result.telemetry is not None
    assert result.telemetry.outcome == "failed"


def test_missing_native_provider_selects_prepared_skill_without_execution(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner([])
    resolver = CapabilityResolver(CapabilityRegistry(), runner=runner)

    result = resolver.select(request(tmp_path, cli=AgentCLI.GEMINI), fallback())

    assert result.mode == "fallback_skill"
    assert result.provider_id == "example-skill"
    assert result.fallback_invocation == "$example-skill"
    assert "no compatible native example capability" in (result.fallback_reason or "")
    assert runner.calls == []


def test_failed_native_execution_selects_fallback_with_telemetry(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            completed(["example"], stdout="native example"),
            completed(["example"], stderr="provider unavailable", code=1),
        ]
    )
    resolver = CapabilityResolver(CapabilityRegistry([ExampleProvider()]), runner=runner)

    result = resolver.select(request(tmp_path), fallback())

    assert result.mode == "fallback_skill"
    assert "provider unavailable" in (result.fallback_reason or "")
    assert result.telemetry is not None
    assert result.telemetry.provider_id == "example-native"
    assert result.telemetry.outcome == "failed"


def test_native_execution_can_be_disabled_without_probing(tmp_path: Path) -> None:
    runner = RecordingRunner([])
    resolver = CapabilityResolver(CapabilityRegistry([ExampleProvider()]), runner=runner)

    result = resolver.select(request(tmp_path), fallback(), enable_native=False)

    assert result.mode == "fallback_skill"
    assert "native execution is disabled" in (result.fallback_reason or "")
    assert runner.calls == []


def test_registry_rejects_ambiguous_provider_routing() -> None:
    registry = CapabilityRegistry([ExampleProvider()])

    with pytest.raises(ValueError, match="duplicate provider"):
        registry.register(ExampleProvider(provider_id="another-native"))


@dataclass(frozen=True)
class ProcessProvider(ExampleProvider):
    probe_script: str = "print('ok')"
    execution_script: str = "print('complete')"

    def probe_command(self, request: CapabilityRequest) -> Sequence[str]:
        del request
        return (sys.executable, "-c", self.probe_script)

    def accepts_probe(self, result: subprocess.CompletedProcess[str]) -> bool:
        return result.returncode == 0 and (result.stdout or "").strip() == "ok"

    def build_command(self, request: CapabilityRequest) -> Sequence[str]:
        del request
        return (sys.executable, "-c", self.execution_script)


class RecordingProcessJob:
    def __init__(self) -> None:
        self.assigned_processes: list[subprocess.Popen[bytes]] = []
        self.terminate_calls = 0
        self.close_calls = 0
        self.lifecycle: list[str] = []

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        self.assigned_processes.append(process)
        self.lifecycle.append("assign")

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        assert process is self.assigned_processes[-1]
        self.lifecycle.append("resume")

    def terminate(self) -> None:
        self.terminate_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class FakeWindowsFunction:
    def __init__(self, implementation) -> None:
        self.implementation = implementation
        self.calls: list[tuple[object, ...]] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.implementation(*args)


def test_real_probe_capture_stops_output_flood_and_selects_fallback(tmp_path: Path) -> None:
    provider = ProcessProvider(
        probe_script=(
            "import sys,time; sys.stdout.write('x' * 129); "
            "sys.stdout.flush(); time.sleep(30)"
        )
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([provider]),
        probe_timeout_seconds=20,
        max_output_bytes=128,
    )

    started_at = time.monotonic()
    result = resolver.select(request(tmp_path), fallback())

    assert time.monotonic() - started_at < 4
    assert result.mode == "fallback_skill"
    assert "probe output was rejected" in (result.fallback_reason or "")
    assert "128-byte output limit" in (result.fallback_reason or "")
    assert result.telemetry is None


def test_real_execution_capture_stops_utf8_flood_and_records_failure(
    tmp_path: Path,
) -> None:
    provider = ProcessProvider(
        execution_script=(
            "import sys,time; sys.stdout.write('€' * 43); "
            "sys.stdout.flush(); time.sleep(30)"
        ),
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([provider]),
        execution_timeout_seconds=20,
        max_output_bytes=128,
    )

    started_at = time.monotonic()
    result = resolver.select(request(tmp_path), fallback())

    assert time.monotonic() - started_at < 4
    assert result.mode == "fallback_skill"
    assert "output was rejected" in (result.fallback_reason or "")
    assert "128-byte output limit" in (result.fallback_reason or "")
    assert result.output is None
    assert result.telemetry is not None
    assert result.telemetry.outcome == "failed"


def test_real_capture_preserves_complete_utf8_at_exact_byte_limit(tmp_path: Path) -> None:
    provider = ProcessProvider(
        execution_script="import sys; sys.stdout.write('€' * 4)",
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([provider]),
        max_output_bytes=12,
    )

    result = resolver.select(request(tmp_path), fallback())

    assert result.mode == "native_command"
    assert result.output == "€" * 4


def test_process_job_is_assigned_and_closed_around_native_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_job = RecordingProcessJob()
    monkeypatch.setattr(
        capability_runner_module,
        "_create_process_job",
        lambda: process_job,
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([ProcessProvider()]),
        max_output_bytes=128,
    )

    result = resolver.select(request(tmp_path), fallback())

    assert result.mode == "native_command"
    assert len(process_job.assigned_processes) == 2
    assert process_job.close_calls == 2
    assert process_job.lifecycle == ["assign", "resume", "assign", "resume"]


def test_process_job_terminates_tree_even_after_parent_exit() -> None:
    process_job = RecordingProcessJob()

    class ExitedProcess:
        pid = 123

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def kill() -> None:
            raise AssertionError("an exited parent is not the owned process tree")

    capability_runner_module._kill_process_group(  # type: ignore[arg-type]
        ExitedProcess(),
        process_job=process_job,
    )

    assert process_job.terminate_calls == 1
    assert process_job.close_calls == 1


def test_windows_job_configures_kill_on_close_and_owns_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def set_information(_handle, _kind, pointer, _size):
        information = pointer._obj
        assert (
            information.BasicLimitInformation.LimitFlags
            == capability_runner_module._WindowsJob._KILL_ON_JOB_CLOSE
        )
        return 1

    kernel32 = SimpleNamespace(
        CreateJobObjectW=FakeWindowsFunction(lambda *_args: 91),
        SetInformationJobObject=FakeWindowsFunction(set_information),
        AssignProcessToJobObject=FakeWindowsFunction(lambda *_args: 1),
        TerminateJobObject=FakeWindowsFunction(lambda *_args: 1),
        CloseHandle=FakeWindowsFunction(lambda *_args: 1),
    )
    ntdll = SimpleNamespace(
        NtResumeProcess=FakeWindowsFunction(lambda *_args: 0),
    )
    monkeypatch.setattr(
        capability_runner_module.ctypes,
        "WinDLL",
        lambda name, **_kwargs: kernel32 if name == "kernel32" else ntdll,
        raising=False,
    )
    job = capability_runner_module._WindowsJob.create()
    process = SimpleNamespace(_handle=73)

    job.assign(process)  # type: ignore[arg-type]
    job.resume(process)  # type: ignore[arg-type]
    job.terminate()
    job.close()

    assert kernel32.AssignProcessToJobObject.calls == [(91, 73)]
    assert ntdll.NtResumeProcess.calls == [(73,)]
    assert kernel32.TerminateJobObject.calls == [(91, 1)]
    assert kernel32.CloseHandle.calls == [(91,)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle contract")
def test_parent_exit_with_descendant_pipe_is_killed_at_capability_timeout(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "child.pid"
    provider = ProcessProvider(
        execution_script=(
            "import pathlib,subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
            "print('parent complete')"
        ),
    )
    resolver = CapabilityResolver(
        CapabilityRegistry([provider]),
        execution_timeout_seconds=1,
        max_output_bytes=128,
    )

    started_at = time.monotonic()
    result = resolver.select(request(tmp_path), fallback())

    assert time.monotonic() - started_at < 4
    assert result.mode == "fallback_skill"
    assert "timed out" in (result.fallback_reason or "")
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while _process_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _process_is_running(child_pid)


def _process_is_running(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            fields = stat_path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return False
        return len(fields) > 2 and fields[2] != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
