"""Tests for the generic, fail-open workflow event callback seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.workflow_execution.event_callback import (
    ResolvedWorkflowEventCallback,
    WorkflowEventCallbackError,
    dispatch_workflow_event_callback,
    resolve_builtin_workflow_event_callback,
)


def test_dispatch_detaches_one_opaque_durable_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_REMOTE_PAYLOAD", "host-bootstrap")
    monkeypatch.setenv("CODEX_SESSION_ID", "host-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "host-thread")
    captured: dict[str, object] = {}

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    callback = ResolvedWorkflowEventCallback("builtin:test:callback", tmp_path / "callback.py")
    dispatch_workflow_event_callback(
        callback,
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
    assert kwargs["stdin"] is not None
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert all(
        key not in kwargs["env"]
        for key in ("CODEX_REMOTE_PAYLOAD", "CODEX_SESSION_ID", "CODEX_THREAD_ID")
    )


def test_callback_event_envelope_is_one_way_and_bounded(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def callback(event: dict[str, object]) -> None:
        captured.update(event)

    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    runtime = BlackboardWorkflowRuntime(
        issue_dir=tmp_path / ".cafe" / "issues" / "event-envelope",
        playbook={"playbook": {"id": "test"}, "steps": {"spec": {}}},
        executor=lambda *_args: None,
        workflow_event_callback=callback,
    )

    runtime._dispatch_workflow_event(
        "phase_terminal",
        {"step": "spec", "status_code": "ok", "unbounded": {"secret": "nope"}},
    )

    assert captured == {
        "workflow_id": runtime.blackboard.workflow_id,
        "issue": "event-envelope",
        "event_type": "phase_terminal",
        "step": "spec",
        "status_code": "ok",
    }


def test_only_builtin_skill_callbacks_are_trusted(tmp_path: Path) -> None:
    with pytest.raises(WorkflowEventCallbackError, match="builtin"):
        resolve_builtin_workflow_event_callback("/tmp/untrusted.py", project_root=tmp_path)
