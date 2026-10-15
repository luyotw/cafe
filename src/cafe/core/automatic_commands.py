"""Sandboxed argv steps. An interrupted command is observed, never replayed."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from cafe.core.automatic_steps import AutomaticExecutionContext, AutomaticExecutionResult
from cafe.core.execution_boundary import EffectiveBoundary
from cafe.core.packet_io import atomic_write_bytes, canonical_json
from cafe.core.sandbox_execution import preflight_sandbox, sandbox_command
from cafe.utils.issue_config import read_issue_config_strict


class CommandInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[StrictStr | dict[str, StrictStr]] = Field(min_length=1, max_length=128)
    cwd: str = "."
    timeout: int = Field(default=60, ge=1, le=3600)
    success: str = "await_agent"


def validate_command_inputs(inputs: Mapping[str, Any]) -> None:
    command = CommandInputs.model_validate(inputs)
    for arg in command.argv:
        if isinstance(arg, dict) and (set(arg) != {"config"} or len(arg["config"].split(".")) != 2):
            raise ValueError("argv references must name one declared step.config field")
        if isinstance(arg, str) and "\0" in arg:
            raise ValueError("argv cannot contain NUL")
    if not isinstance(command.argv[0], str) or not command.argv[0]:
        raise ValueError("command executable must be a literal")


def execute_command(inputs: Mapping[str, Any], *, context: AutomaticExecutionContext):
    validate_command_inputs(inputs)
    command = CommandInputs.model_validate(inputs)
    root = context.project_root
    cwd = (root / command.cwd).resolve(strict=True)
    if not cwd.is_dir() or not cwd.is_relative_to(root):
        raise ValueError("command cwd must remain inside the project")
    config = read_issue_config_strict(context.issue_dir / "issue.yaml")
    argv = []
    for arg in command.argv:
        if isinstance(arg, dict):
            section, key = arg["config"].split(".")
            value = config.get(section, {}).get(key)
            if not isinstance(value, (str, bool, int)):
                raise ValueError(f"missing scalar command input {arg['config']!r}")
            arg = str(value)
        if "\0" in arg:
            raise ValueError("argv cannot contain NUL")
        argv.append(arg)
    boundary = EffectiveBoundary(
        cwd=cwd, readable_roots=(root,), writable_roots=(cwd,), environment=os.environ
    )
    identity = {
        "workflow_id": context.workflow_id,
        "step": context.step,
        "execution_id": context.execution_id,
        "argv": argv,
        "boundary": boundary.model_dump(mode="json"),
        "timeout": command.timeout,
        "success": command.success,
    }
    fingerprint = hashlib.sha256(canonical_json(identity)).hexdigest()
    record = context.issue_dir / context.step / "systematic" / (context.execution_id + ".json")
    if record.exists():
        if record.is_symlink() or record.stat().st_size > 128 * 1024:
            raise ValueError("invalid command execution record")
        saved = json.loads(record.read_text())
        if saved.get("fingerprint") != fingerprint:
            raise ValueError("command inputs changed during the same execution")
        if saved.get("state") != "succeeded":
            raise ValueError(
                f"command {saved.get('state', 'unknown')}; inspect its execution record before recovery"
            )
        return AutomaticExecutionResult(
            command.success,
            events=[
                {
                    "type": "systematic_command_result",
                    "execution_id": context.execution_id,
                    "receipt": str(record),
                    "reused": True,
                }
            ],
        )
    # Refuse before reserving a launch if the existing sandbox is unavailable.
    check = preflight_sandbox(boundary)
    if not check.available:
        raise ValueError(f"{check.reason}: {check.guidance}")
    saved = {**identity, "fingerprint": fingerprint, "state": "unknown", "returncode": None}
    atomic_write_bytes(record, canonical_json(saved))
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(
            sandbox_command(argv, boundary=boundary),
            cwd=cwd,
            env=dict(boundary.environment),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            child.wait(timeout=command.timeout)
        except BaseException:
            _stop(child)
            raise
        finally:
            # Descendants may outlive the command's own successful exit.
            _stop(child)
        for name, handle in (("stdout", stdout), ("stderr", stderr)):
            handle.seek(0)
            saved[name] = handle.read(16384).decode("utf-8", errors="replace")
        saved.update(
            state="succeeded" if child.returncode == 0 else "failed", returncode=child.returncode
        )
        atomic_write_bytes(record, canonical_json(saved))
    if child.returncode:
        raise ValueError(f"command exited {child.returncode}; evidence: {record}")
    return AutomaticExecutionResult(
        command.success,
        events=[
            {
                "type": "systematic_command_result",
                "execution_id": context.execution_id,
                "receipt": str(record),
                "reused": False,
            }
        ],
    )


def _stop(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)
