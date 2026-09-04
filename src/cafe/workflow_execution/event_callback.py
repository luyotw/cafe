"""Trusted, asynchronous callbacks for durable workflow events."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from cafe.skills.loader import SkillLoader
from cafe.workflow_execution.worker_launch import detached_child_environment


_CALLBACK_ID = re.compile(r"^builtin:([a-z0-9][a-z0-9-]*):([a-z][a-z0-9_]*)$")


@dataclass(frozen=True)
class ResolvedWorkflowEventCallback:
    """One trusted builtin callback resolved before a background worker launches."""

    callback_id: str
    script: Path


class WorkflowEventCallbackError(ValueError):
    """The requested callback is not a trusted builtin event callback."""


def resolve_builtin_workflow_event_callback(
    callback_id: str,
    *,
    project_root: Path | None = None,
    loader_factory: Callable[..., SkillLoader] = SkillLoader,
) -> ResolvedWorkflowEventCallback:
    """Resolve an opaque builtin callback ID without accepting a command or path."""
    matched = _CALLBACK_ID.fullmatch(callback_id)
    if matched is None:
        raise WorkflowEventCallbackError("callback must use builtin:<skill>:<callback> form")
    skill_name, callback_name = matched.groups()
    loader = loader_factory(project_root=project_root) if project_root else loader_factory()
    try:
        entry = loader.get_skill_entry(skill_name)
    except Exception as exc:
        raise WorkflowEventCallbackError("builtin workflow event callback is unavailable") from exc
    if entry.source != "builtin":
        raise WorkflowEventCallbackError(
            "workflow event callback must resolve from the builtin catalog"
        )
    skill_dir = entry.directory.resolve()
    script = skill_dir / "scripts" / f"{callback_name}.py"
    try:
        resolved_script = script.resolve(strict=True)
        resolved_script.relative_to(skill_dir)
    except (OSError, ValueError) as exc:
        raise WorkflowEventCallbackError(
            "workflow event callback script escapes its builtin skill"
        ) from exc
    if script.is_symlink() or not resolved_script.is_file():
        raise WorkflowEventCallbackError(
            "workflow event callback script must be a regular builtin file"
        )
    return ResolvedWorkflowEventCallback(callback_id=callback_id, script=resolved_script)


def dispatch_workflow_event_callback(
    callback: ResolvedWorkflowEventCallback,
    event: Mapping[str, Any],
    *,
    cwd: Path,
    popen_factory: Callable[..., Any] | None = None,
) -> None:
    """Detach one callback after a durable event; its result is never observed."""
    encoded_event = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"))
    arguments = [sys.executable, str(callback.script), "--workflow-event", encoded_event]
    kwargs = {
        "cwd": str(Path(cwd).resolve()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
        "close_fds": True,
        "env": detached_child_environment(),
    }
    if popen_factory is not None:
        popen_factory(arguments, **kwargs)
    else:
        subprocess.Popen(arguments, **kwargs)
