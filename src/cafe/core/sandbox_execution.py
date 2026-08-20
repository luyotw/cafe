"""Direct command execution through the active Codex sandbox."""

from __future__ import annotations

import shutil
import subprocess
import uuid
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cafe.core.execution_boundary import ExecutionClass, ExecutionReceipt, ScriptLaunchRequest, snapshot_script

MIGRATION_GUIDANCE = (
    "Keep compatible hooks in sandbox execution, create explicit user lifecycle trust for narrow "
    "prepare/close automation, or request a registered capability for privileged effects."
)


@dataclass(frozen=True)
class SandboxRunResult:
    returncode: int | None
    stdout: str
    stderr: str
    receipt: ExecutionReceipt


class SandboxExecutor:
    def __init__(self, *, codex_path: str | None = "auto", runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.codex_path = shutil.which("codex") if codex_path == "auto" else codex_path
        self.runner = runner

    def run(self, request: ScriptLaunchRequest) -> SandboxRunResult:
        correlation_id = uuid.uuid4().hex
        if request.execution_class not in {ExecutionClass.SANDBOX, ExecutionClass.LIFECYCLE}:
            return self._denied(request, correlation_id, "capability_requires_registered_adapter")
        if not self.codex_path:
            return self._denied(request, correlation_id, "sandbox_backend_unavailable")
        try:
            snapshot = snapshot_script(request.script, allowed_root=request.script.parent)
        except (OSError, ValueError) as exc:
            return self._denied(request, correlation_id, "script_identity_invalid", str(exc))
        state = {"permissionProfile": {}, "sandboxCwd": request.boundary.cwd.resolve().as_uri()}
        command = [self.codex_path, "sandbox", "--sandbox-state-json", json.dumps(state), "--sandbox-state-disable-network"]
        for root in request.boundary.readable_roots:
            command.extend(["--sandbox-state-readable-root", str(root.resolve())])
        command.extend([str(snapshot.path), *request.args])
        try:
            completed = self.runner(command, cwd=str(request.boundary.cwd.resolve()), env=dict(request.boundary.environment), capture_output=True, text=True, check=False, timeout=request.timeout_seconds)
            outcome = "success" if completed.returncode == 0 else "failed"
            receipt = ExecutionReceipt(correlation_id=correlation_id, execution_class=request.execution_class, trust_source=request.trust_source, outcome=outcome, boundary=request.boundary, canonical_identity=snapshot.digest, details={"returncode": completed.returncode})
            return SandboxRunResult(completed.returncode, completed.stdout or "", completed.stderr or "", receipt)
        except subprocess.TimeoutExpired as exc:
            receipt = ExecutionReceipt(correlation_id=correlation_id, execution_class=request.execution_class, trust_source=request.trust_source, outcome="timeout", boundary=request.boundary, canonical_identity=snapshot.digest)
            return SandboxRunResult(None, _output(exc.stdout), _output(exc.stderr), receipt)
        finally:
            snapshot.cleanup()

    @staticmethod
    def _denied(request: ScriptLaunchRequest, correlation_id: str, reason: str, detail: str = "") -> SandboxRunResult:
        receipt = ExecutionReceipt(correlation_id=correlation_id, execution_class=request.execution_class, trust_source=request.trust_source, outcome="denied", boundary=request.boundary, details={"reason": reason, "detail": detail, "migration": MIGRATION_GUIDANCE})
        return SandboxRunResult(None, "", reason, receipt)


def _output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def sandbox_command(command: list[str], *, cwd: Path) -> list[str]:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("sandbox_backend_unavailable")
    state = {"permissionProfile": {}, "sandboxCwd": cwd.resolve().as_uri()}
    return [codex, "sandbox", "--sandbox-state-json", json.dumps(state), "--sandbox-state-readable-root", str(cwd.resolve()), "--sandbox-state-disable-network", *command]
