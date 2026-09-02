"""Durable guidance for the existing HumanTask notification boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cafe.core.blackboard import BlackboardStore


def _human_task_delivery_available(*, repository_root: Path) -> bool:
    from cafe.core.human_task_notifications import (
        SlackNotificationError,
        load_human_task_notification_settings,
        load_slack_webhook_url,
    )

    if not load_human_task_notification_settings().enabled:
        return False
    try:
        load_slack_webhook_url(repository_root=repository_root)
    except SlackNotificationError:
        return False
    return True


def record_notification_guidance(
    issue_dir: Path,
    *,
    repository_root: Path,
    human_task_delivery_available: bool | None = None,
) -> dict[str, Any]:
    """Record honest inspection guidance without creating delivery authority."""
    available = (
        _human_task_delivery_available(repository_root=repository_root)
        if human_task_delivery_available is None
        else human_task_delivery_available
    )
    guidance = {
        "proactive_events": ["human_task"] if available else [],
        "inspection_available": True,
        "inspection_command": "cafe status",
    }
    store = BlackboardStore(Path(issue_dir))
    state = store.load_or_create("spec")
    with store.driver_transaction(state) as persisted:
        persisted.driver_state["notification_guidance"] = guidance
        persisted.driver_state.pop("notification_receipts", None)
    return guidance
