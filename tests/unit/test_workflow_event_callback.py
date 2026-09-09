"""Tests for the generic, fail-open workflow event callback seam."""

from __future__ import annotations

import json
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

    assert captured.items() >= {
        "workflow_id": runtime.blackboard.workflow_id,
        "issue": "event-envelope",
        "event_type": "phase_terminal",
        "step": "spec",
        "status_code": "ok",
    }.items()
    assert set(captured) == {
        "workflow_id",
        "issue",
        "event_type",
        "event_id",
        "sequence",
        "occurred_at",
        "step",
        "status_code",
    }

    persisted = json.loads((runtime.issue_dir / "blackboard.json").read_text())
    durable = next(
        item
        for item in persisted["events"]
        if item["event_type"] == "workflow_event_callback_enqueued"
    )
    assert captured["event_id"] == durable["data"]["event_id"]
    assert captured["sequence"] == durable["data"]["sequence"] == 1
    assert captured["occurred_at"] == durable["timestamp"]


def test_callback_event_identity_is_monotonic_and_reused_on_replay(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    runtime = BlackboardWorkflowRuntime(
        issue_dir=tmp_path / ".cafe" / "issues" / "event-replay",
        playbook={"playbook": {"id": "test"}, "steps": {"spec": {}}},
        executor=lambda *_args: None,
        workflow_event_callback=lambda event: events.append(dict(event)),
    )

    runtime._dispatch_workflow_event("phase_terminal", {"step": "spec", "status_code": "one"})
    runtime._dispatch_workflow_event("phase_terminal", {"step": "spec", "status_code": "two"})
    runtime._dispatch_workflow_event("phase_terminal", events[0])

    assert [event["sequence"] for event in events] == [1, 2, 1]
    assert events[2]["event_id"] == events[0]["event_id"]
    persisted = json.loads((runtime.issue_dir / "blackboard.json").read_text())
    assert sum(
        item["event_type"] == "workflow_event_callback_enqueued"
        for item in persisted["events"]
    ) == 2


def test_only_builtin_skill_callbacks_are_trusted(tmp_path: Path) -> None:
    with pytest.raises(WorkflowEventCallbackError, match="builtin"):
        resolve_builtin_workflow_event_callback("/tmp/untrusted.py", project_root=tmp_path)
