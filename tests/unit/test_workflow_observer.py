"""Tests for the generic, fail-open workflow observer seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.orchestration.workflow_observer import (
    WorkflowObserverBinding,
    WorkflowObserverError,
    dispatch_workflow_observer,
    resolve_builtin_observer,
)


def test_dispatch_detaches_one_opaque_durable_event(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    binding = WorkflowObserverBinding("builtin:test:callback", tmp_path / "callback.py")
    dispatch_workflow_observer(
        binding,
        {"workflow_id": "workflow", "issue": "issue", "event_type": "phase_terminal"},
        cwd=tmp_path,
        popen_factory=popen,
    )

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert command[-2] == "--workflow-event"
    assert '"event_type":"phase_terminal"' in command[-1]
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


def test_only_builtin_skill_observers_are_trusted(tmp_path: Path) -> None:
    with pytest.raises(WorkflowObserverError, match="builtin"):
        resolve_builtin_observer("/tmp/untrusted.py", project_root=tmp_path)
