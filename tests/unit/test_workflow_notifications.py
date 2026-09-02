"""Notification guidance without a generic workflow-event authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore
from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
from cafe.core.workflow_notifications import record_notification_guidance


@pytest.mark.parametrize("human_task_delivery_available", [False, True])
def test_guidance_describes_only_the_existing_human_task_boundary(
    tmp_path: Path, human_task_delivery_available: bool
) -> None:
    guidance = record_notification_guidance(
        tmp_path,
        human_task_delivery_available=human_task_delivery_available,
    )

    assert guidance["inspection_available"] is True
    assert guidance["inspection_command"] == "cafe status"
    assert guidance["proactive_events"] == (["human_task"] if human_task_delivery_available else [])
    state = BlackboardStore(tmp_path).load_or_create("spec")
    assert state.driver_state["notification_guidance"] == guidance
    assert "notification_receipts" not in state.driver_state


def test_generic_workflow_event_capability_is_not_registered(tmp_path: Path) -> None:
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))

    assert "cafe.slack.workflow_event" not in registry
