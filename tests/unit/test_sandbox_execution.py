import json
import os
import subprocess
from pathlib import Path

from cafe.core.execution_boundary import (
    EffectiveBoundary,
    ExecutionClass,
    ScriptLaunchRequest,
    TrustSource,
)
from cafe.core.sandbox_execution import SandboxExecutor, sandbox_command


def _request(tmp_path: Path) -> ScriptLaunchRequest:
    script = tmp_path / "hook.sh"
    script.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8")
    script.chmod(0o700)
    return ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        script=script,
        boundary=EffectiveBoundary(cwd=tmp_path, readable_roots=(tmp_path,), writable_roots=(tmp_path,), environment={"PATH": os.environ.get("PATH", "")}),
    )


def test_sandbox_backend_constructs_explicit_command_and_environment(tmp_path: Path) -> None:
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok", "")
    result = SandboxExecutor(codex_path="/usr/bin/codex", runner=run).run(_request(tmp_path))
    command, kwargs = calls[0]
    assert command[:2] == ["/usr/bin/codex", "sandbox"]
    assert "--sandbox-state-disable-network" in command
    state = json.loads(command[command.index("--sandbox-state-json") + 1])
    entries = state["permissionProfile"]["file_system"]["entries"]
    assert {
        (entry["path"]["path"], entry["access"])
        for entry in entries
        if entry["path"]["type"] == "path"
    } >= {(str(tmp_path.resolve()), "write")}
    assert kwargs["env"] == {"PATH": os.environ.get("PATH", "")}
    assert result.receipt.outcome == "success"


def test_sandbox_command_preserves_declared_write_scope(tmp_path: Path, monkeypatch) -> None:
    writable = tmp_path / "allowed"
    writable.mkdir()
    boundary = _request(tmp_path).boundary.model_copy(
        update={"writable_roots": (writable,)}
    )
    monkeypatch.setattr("cafe.core.sandbox_execution.shutil.which", lambda _name: "/usr/bin/codex")

    command = sandbox_command(["/bin/true"], boundary=boundary)

    state = json.loads(command[command.index("--sandbox-state-json") + 1])
    entries = state["permissionProfile"]["file_system"]["entries"]
    writes = {
        entry["path"]["path"]
        for entry in entries
        if entry["access"] == "write"
    }
    assert writes == {str(writable.resolve())}


def test_sandbox_backend_fails_closed_when_unavailable(tmp_path: Path) -> None:
    called = False
    def run(*_args, **_kwargs):
        nonlocal called
        called = True
    result = SandboxExecutor(codex_path=None, runner=run).run(_request(tmp_path))
    assert called is False
    assert result.receipt.outcome == "denied"
    assert result.returncode is None
