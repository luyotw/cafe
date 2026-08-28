"""Direct command execution through the active Codex sandbox."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from cafe.core.execution_boundary import (
    EffectiveBoundary,
    ExecutionClass,
    ExecutionReceipt,
    ScriptLaunchRequest,
    ScriptSnapshot,
    snapshot_script,
)

MIGRATION_GUIDANCE = (
    "Keep compatible hooks in sandbox execution, create explicit user lifecycle trust for narrow "
    "prepare/close automation, or request a registered capability for privileged effects."
)
SANDBOX_USER_NAMESPACE_FAILURES = (
    "loopback: Failed RTM_NEWADDR",
    "loopback: Failed RTM_NEWLINK",
    "setting up uid map: Permission denied",
    "No permissions to create a new namespace",
)
SANDBOX_USER_NAMESPACE_GUIDANCE = (
    "Codex's Linux sandbox could not create its required user/network namespace. "
    "Install and load the distribution bwrap AppArmor profile; do not disable the "
    "sandbox or relaunch the child command automatically."
)
SANDBOX_PREFLIGHT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SandboxRunResult:
    returncode: int | None
    stdout: str
    stderr: str
    receipt: ExecutionReceipt


@dataclass(frozen=True)
class SandboxPreflightResult:
    available: bool
    reason: str = ""
    detail: str = ""
    guidance: str = ""


def preflight_sandbox(
    boundary: EffectiveBoundary,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: float = SANDBOX_PREFLIGHT_TIMEOUT_SECONDS,
) -> SandboxPreflightResult:
    """Verify the exact managed boundary before claiming a child command started."""
    try:
        command = sandbox_command(["/bin/true"], boundary=boundary)
    except RuntimeError:
        return SandboxPreflightResult(
            available=False,
            reason="sandbox_backend_unavailable",
            guidance=MIGRATION_GUIDANCE,
        )
    try:
        completed = runner(
            command,
            cwd=str(boundary.cwd.resolve()),
            env=dict(boundary.environment),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return SandboxPreflightResult(
            available=False,
            reason="sandbox_preflight_timeout",
            guidance="Inspect the Codex sandbox backend before retrying the child command.",
        )
    except OSError as exc:
        return SandboxPreflightResult(
            available=False,
            reason="sandbox_backend_unavailable",
            detail=exc.__class__.__name__,
            guidance=MIGRATION_GUIDANCE,
        )
    if completed.returncode == 0:
        return SandboxPreflightResult(available=True)

    detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
    if any(marker in detail for marker in SANDBOX_USER_NAMESPACE_FAILURES):
        return SandboxPreflightResult(
            available=False,
            reason="sandbox_user_namespace_unavailable",
            detail=detail,
            guidance=SANDBOX_USER_NAMESPACE_GUIDANCE,
        )
    return SandboxPreflightResult(
        available=False,
        reason="sandbox_preflight_failed",
        detail=detail,
        guidance="Inspect the Codex sandbox preflight result before retrying the child command.",
    )


class SandboxExecutor:
    def __init__(
        self,
        *,
        codex_path: str | None = "auto",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.codex_path = shutil.which("codex") if codex_path == "auto" else codex_path
        self.runner = runner

    def run(
        self,
        request: ScriptLaunchRequest,
        *,
        prepared_snapshot: ScriptSnapshot | None = None,
    ) -> SandboxRunResult:
        correlation_id = uuid.uuid4().hex
        owns_snapshot = prepared_snapshot is None
        if prepared_snapshot is None:
            try:
                snapshot = snapshot_script(request.script, allowed_root=request.script.parent)
            except (OSError, ValueError) as exc:
                return self._denied(request, correlation_id, "script_identity_invalid", str(exc))
        else:
            snapshot = prepared_snapshot
            if request.script != snapshot.path:
                return self._denied(
                    request,
                    correlation_id,
                    "script_identity_invalid",
                    "prepared snapshot does not match the launch request",
                )
        try:
            if request.execution_class not in {ExecutionClass.SANDBOX, ExecutionClass.LIFECYCLE}:
                return self._denied(
                    request,
                    correlation_id,
                    "capability_requires_registered_adapter",
                    canonical_identity=snapshot.digest,
                )
            if not self.codex_path:
                return self._denied(
                    request,
                    correlation_id,
                    "sandbox_backend_unavailable",
                    canonical_identity=snapshot.digest,
                )
            state = _sandbox_state(request.boundary, extra_readable=(snapshot.root,))
            command = [
                self.codex_path,
                "sandbox",
                "--sandbox-state-json",
                json.dumps(state),
                "--sandbox-state-disable-network",
            ]
            for root in request.boundary.readable_roots:
                command.extend(["--sandbox-state-readable-root", str(root.resolve())])
            command.extend([str(snapshot.path), *request.args])
            completed = self.runner(
                command,
                cwd=str(request.boundary.cwd.resolve()),
                env=dict(request.boundary.environment),
                capture_output=True,
                text=True,
                check=False,
                timeout=request.timeout_seconds,
            )
            outcome = "success" if completed.returncode == 0 else "failed"
            receipt = ExecutionReceipt(
                correlation_id=correlation_id,
                execution_class=request.execution_class,
                trust_source=request.trust_source,
                outcome=outcome,
                boundary=request.boundary,
                canonical_identity=snapshot.digest,
                details={"returncode": completed.returncode},
            )
            return SandboxRunResult(
                completed.returncode, completed.stdout or "", completed.stderr or "", receipt
            )
        except subprocess.TimeoutExpired as exc:
            receipt = ExecutionReceipt(
                correlation_id=correlation_id,
                execution_class=request.execution_class,
                trust_source=request.trust_source,
                outcome="timeout",
                boundary=request.boundary,
                canonical_identity=snapshot.digest,
            )
            return SandboxRunResult(None, _output(exc.stdout), _output(exc.stderr), receipt)
        finally:
            if owns_snapshot:
                snapshot.cleanup()

    @staticmethod
    def _denied(
        request: ScriptLaunchRequest,
        correlation_id: str,
        reason: str,
        detail: str = "",
        *,
        canonical_identity: str | None = None,
    ) -> SandboxRunResult:
        receipt = ExecutionReceipt(
            correlation_id=correlation_id,
            execution_class=request.execution_class,
            trust_source=request.trust_source,
            outcome="denied",
            boundary=request.boundary,
            canonical_identity=canonical_identity,
            details={"reason": reason, "detail": detail, "migration": MIGRATION_GUIDANCE},
        )
        return SandboxRunResult(None, "", reason, receipt)


def _output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _path_entry(path: Path, access: str) -> dict[str, object]:
    return {
        "path": {"type": "path", "path": str(path.resolve())},
        "access": access,
    }


def _sandbox_state(
    boundary: "EffectiveBoundary", *, extra_readable: Iterable[Path] = ()
) -> dict[str, object]:
    readable = dict.fromkeys(root.resolve() for root in (*boundary.readable_roots, *extra_readable))
    writable = dict.fromkeys(root.resolve() for root in boundary.writable_roots)
    entries = [_path_entry(root, "read") for root in readable]
    entries.extend(_path_entry(root, "write") for root in writable)
    return {
        "permissionProfile": {
            "type": "managed",
            "file_system": {"type": "restricted", "entries": entries},
            "network": "restricted",
        },
        "sandboxCwd": boundary.cwd.resolve().as_uri(),
    }


def sandbox_command(command: list[str], *, boundary: "EffectiveBoundary") -> list[str]:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("sandbox_backend_unavailable")
    state = _sandbox_state(boundary)
    result = [codex, "sandbox", "--sandbox-state-json", json.dumps(state)]
    for root in boundary.readable_roots:
        result.extend(["--sandbox-state-readable-root", str(root.resolve())])
    return [*result, "--sandbox-state-disable-network", *command]
