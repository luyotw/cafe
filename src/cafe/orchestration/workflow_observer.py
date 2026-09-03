"""Trusted, best-effort callbacks for durable workflow boundaries."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from cafe.skills.loader import SkillLoader


_OBSERVER_ID = re.compile(r"^builtin:([a-z0-9][a-z0-9-]*):([a-z][a-z0-9_]*)$")


@dataclass(frozen=True)
class WorkflowObserverBinding:
    """One builtin callback resolved before a background worker launches."""

    observer_id: str
    script: Path


class WorkflowObserverError(ValueError):
    """The requested observer is not a trusted builtin callback."""


def resolve_builtin_observer(
    observer_id: str,
    *,
    project_root: Path | None = None,
    loader_factory: Callable[..., SkillLoader] = SkillLoader,
) -> WorkflowObserverBinding:
    """Resolve an opaque builtin observer ID without accepting a command or path."""
    matched = _OBSERVER_ID.fullmatch(observer_id)
    if matched is None:
        raise WorkflowObserverError("observer must use builtin:<skill>:<callback> form")
    skill_name, callback_name = matched.groups()
    loader = loader_factory(project_root=project_root) if project_root else loader_factory()
    try:
        entry = loader.get_skill_entry(skill_name)
    except Exception as exc:
        raise WorkflowObserverError("builtin workflow observer is unavailable") from exc
    if entry.source != "builtin":
        raise WorkflowObserverError("workflow observer must resolve from the builtin catalog")
    skill_dir = entry.directory.resolve()
    script = skill_dir / "scripts" / f"{callback_name}.py"
    try:
        resolved_script = script.resolve(strict=True)
        resolved_script.relative_to(skill_dir)
    except (OSError, ValueError) as exc:
        raise WorkflowObserverError("workflow observer script escapes its builtin skill") from exc
    if script.is_symlink() or not resolved_script.is_file():
        raise WorkflowObserverError("workflow observer script must be a regular builtin file")
    return WorkflowObserverBinding(observer_id=observer_id, script=resolved_script)


def dispatch_workflow_observer(
    binding: WorkflowObserverBinding,
    event: Mapping[str, Any],
    *,
    cwd: Path,
    popen_factory: Callable[..., Any] | None = None,
) -> None:
    """Detach one observer callback. Its result is deliberately unobserved."""
    encoded_event = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"))
    arguments = [sys.executable, str(binding.script), "--workflow-event", encoded_event]
    kwargs = {
        "cwd": str(Path(cwd).resolve()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
        "close_fds": True,
    }
    if popen_factory is not None:
        popen_factory(arguments, **kwargs)
    else:
        subprocess.Popen(arguments, **kwargs)
