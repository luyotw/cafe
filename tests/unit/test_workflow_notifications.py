"""Substantive workflow lifecycle notification tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.capabilities import (
    default_capability_definition_dirs,
    load_capability_registry,
    run_capability_request,
)
from cafe.core.workflow_notifications import WorkflowNotificationEvent, WorkflowNotifier


def _event(event_type: str, event_id: str = "event-1") -> WorkflowNotificationEvent:
    return WorkflowNotificationEvent(
        workflow_id="workflow-1",
        event_id=event_id,
        event_type=event_type,
        step="develop",
    )


@pytest.mark.parametrize(
    "event_type", ["phase_boundary", "human_task", "error", "permission", "completion"]
)
def test_substantive_events_dispatch_once(tmp_path: Path, event_type: str) -> None:
    requests: list[dict] = []
    notifier = WorkflowNotifier(
        tmp_path,
        configured=True,
        dispatcher=lambda request: requests.append(request) or {"success": True},
    )
    event = _event(event_type)

    first = notifier.notify(event)
    second = notifier.notify(event)

    assert first["status"] == "delivered"
    assert second == first
    assert len(requests) == 1
    assert requests[0]["args"]["event_type"] == event_type


@pytest.mark.parametrize("event_type", ["transport_yield", "poll"])
def test_transport_activity_never_dispatches_or_creates_receipt(
    tmp_path: Path, event_type: str
) -> None:
    requests: list[dict] = []
    notifier = WorkflowNotifier(
        tmp_path,
        configured=True,
        dispatcher=lambda request: requests.append(request) or {"success": True},
    )

    assert notifier.notify(_event(event_type)) is None
    assert requests == []
    state = BlackboardStore(tmp_path).load_or_create("spec")
    assert state.driver_state.get("notification_receipts", {}) == {}


def test_missing_transport_records_inspection_guidance_without_delivery(tmp_path: Path) -> None:
    notifier = WorkflowNotifier(
        tmp_path,
        configured=False,
        dispatcher=lambda _request: pytest.fail("notification dispatched"),
    )

    receipt = notifier.notify(_event("phase_boundary"))

    assert receipt["status"] == "not_configured"
    state = BlackboardStore(tmp_path).load_or_create("spec")
    assert state.driver_state["notification_guidance"]["proactive"] is False
    assert state.driver_state["notification_guidance"]["inspection_available"] is True


def test_delivery_failure_does_not_change_workflow_truth(tmp_path: Path) -> None:
    store = BlackboardStore(tmp_path)
    state = store.load_or_create("review")
    state.artifacts["develop"] = ArtifactEntry(
        name="develop",
        kind=ArtifactKind.DOCUMENT,
        version=1,
        updated_by="develop",
        path="develop/output.md",
    )
    store.save(state)

    def fail(_request):
        raise RuntimeError("transport failed")

    receipt = WorkflowNotifier(tmp_path, configured=True, dispatcher=fail).notify(
        _event("error")
    )

    assert receipt["status"] == "delivery_failed"
    reloaded = store.load_or_create("review")
    assert reloaded.current_step == "review"
    assert reloaded.artifacts["develop"].path == "develop/output.md"


def test_workflow_notification_capability_is_runtime_owned(tmp_path: Path) -> None:
    requests: list[dict] = []
    notifier = WorkflowNotifier(
        tmp_path / ".cafe" / "issues" / "issue432",
        configured=True,
        dispatcher=lambda request: requests.append(request) or {"success": True},
    )
    notifier.notify(_event("completion"))
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    denied = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=requests[0],
        output_file=tmp_path / "blackboard.json",
    )

    assert denied.receipt["success"] is False
    assert denied.receipt["code"] == "workflow_notification_not_runtime_owned"


def test_trusted_workflow_notification_uses_fixed_slack_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    import cafe.core.human_task_notifications as notification_mod

    requests: list[dict] = []
    delivered = []
    notifier = WorkflowNotifier(
        tmp_path / ".cafe" / "issues" / "issue432",
        configured=True,
        dispatcher=lambda request: requests.append(request) or {"success": True},
    )
    notifier.notify(_event("phase_boundary"))
    monkeypatch.setattr(notification_mod, "load_slack_webhook_url", lambda: "fixed-secret")
    monkeypatch.setattr(
        notification_mod,
        "post_slack_notification",
        lambda webhook, message, *, timeout_sec: delivered.append(
            (webhook, message.event_type, timeout_sec)
        ),
    )
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    run = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=requests[0],
        output_file=tmp_path / "blackboard.json",
        trusted_workflow_notification=True,
    )

    assert run.receipt["success"] is True
    assert run.receipt["outputs"]["event_id"] == "event-1"
    assert delivered == [("fixed-secret", "phase_boundary", 600.0)]
