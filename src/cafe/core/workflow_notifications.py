"""Typed, deduplicated notifications for substantive workflow lifecycle events."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, field_validator

from cafe.core.blackboard import BlackboardStore

SUBSTANTIVE_WORKFLOW_EVENTS = frozenset(
    {"phase_boundary", "human_task", "error", "permission", "completion"}
)


class WorkflowNotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workflow_id: str
    event_id: str
    event_type: Literal[
        "phase_boundary",
        "human_task",
        "error",
        "permission",
        "completion",
        "transport_yield",
        "poll",
    ]
    step: str

    @field_validator("workflow_id", "event_id", "step")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow notification identifiers must not be empty")
        return value


class WorkflowNotifier:
    """Dispatch configured lifecycle events once without owning workflow truth."""

    def __init__(
        self,
        issue_dir: Path,
        *,
        configured: bool | None = None,
        dispatcher: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self.issue_dir = Path(issue_dir)
        self.store = BlackboardStore(self.issue_dir)
        self.state = self.store.load_or_create("spec")
        self.configured = self._transport_available() if configured is None else configured
        self.dispatcher = dispatcher or self._dispatch_capability
        self._record_guidance()

    def notify(self, event: WorkflowNotificationEvent) -> dict[str, Any] | None:
        if event.event_type not in SUBSTANTIVE_WORKFLOW_EVENTS:
            return None
        with self.store.driver_transaction(self.state) as state:
            receipts = state.driver_state.setdefault("notification_receipts", {})
            existing = receipts.get(event.event_id)
            if isinstance(existing, dict):
                return dict(existing)
            guidance = {
                "proactive": bool(self.configured),
                "inspection_available": True,
                "inspection_command": "cafe status",
            }
            state.driver_state["notification_guidance"] = guidance
            if not self.configured:
                receipt = {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "status": "not_configured",
                }
                receipts[event.event_id] = receipt
                return dict(receipt)
            receipts[event.event_id] = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "status": "pending",
            }

        request = self._capability_request(event)
        try:
            dispatch_receipt = dict(self.dispatcher(request))
            status = "delivered" if dispatch_receipt.get("success", True) else "delivery_failed"
        except Exception:
            dispatch_receipt = {}
            status = "delivery_failed"
        receipt = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "status": status,
            "capability_receipt": dispatch_receipt,
        }
        with self.store.driver_transaction(self.state) as state:
            receipts = state.driver_state.setdefault("notification_receipts", {})
            receipts[event.event_id] = receipt
        return dict(receipt)

    def _capability_request(self, event: WorkflowNotificationEvent) -> dict[str, Any]:
        return {
            "capability": "cafe.slack.workflow_event",
            "args": {
                "repository": self._repository_root().name,
                "workflow_id": event.workflow_id,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "step": event.step,
            },
            "effects": {
                "writes": [],
                "network_destinations": ["hooks.slack.com"],
                "browser_open": [],
            },
            "credentials": ["slack_human_task_webhook"],
            "permissions": {"network": ["hooks.slack.com"]},
        }

    def _record_guidance(self) -> None:
        with self.store.driver_transaction(self.state) as state:
            state.driver_state["notification_guidance"] = {
                "proactive": bool(self.configured),
                "inspection_available": True,
                "inspection_command": "cafe status",
            }

    def _dispatch_capability(self, request: dict[str, Any]) -> Mapping[str, Any]:
        from cafe.core.capabilities import (
            default_capability_definition_dirs,
            load_capability_registry,
            run_capability_request,
        )

        registry = load_capability_registry(default_capability_definition_dirs())
        run = run_capability_request(
            repo_root=self._repository_root(),
            registry=registry,
            capability_request=request,
            output_file=self.issue_dir / "blackboard.json",
            timeout_sec=5.0,
            trusted_workflow_notification=True,
        )
        return dict(run.receipt)

    @staticmethod
    def _transport_available() -> bool:
        from cafe.core.human_task_notifications import (
            SlackNotificationError,
            load_slack_webhook_url,
        )

        try:
            load_slack_webhook_url()
        except SlackNotificationError:
            return False
        return True

    def _repository_root(self) -> Path:
        for parent in self.issue_dir.resolve().parents:
            if parent.name == ".cafe":
                return parent.parent
        return Path.cwd().resolve()
