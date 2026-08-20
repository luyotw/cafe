"""Durable approval lifecycle for one exact trusted-host capability request."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from cafe.core.capabilities import (
    CapabilityManifest,
    ExecutionRequest,
    PolicyDecision,
    canonical_request_fingerprint,
    evaluate_capability_request,
)
from cafe.core.human_task_records import HumanTask, HumanTaskRecordStore, HumanTaskStatus

CAPABILITY_APPROVAL_TRIGGER = "capability_approval"
CAPABILITY_APPROVAL_POLICY_ID = "capability-approval"
TERMINAL_STATES = {
    "denied",
    "cancelled",
    "expired",
    "tampered",
    "policy_rejected",
    "succeeded",
    "failed",
    "uncertain",
}


class CapabilityApprovalError(ValueError):
    """An approval input cannot safely advance its exact request."""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


class CapabilityApprovalService:
    """Coordinate durable human authorization for an immutable execution request."""

    def __init__(
        self,
        *,
        issue_dir: Path,
        workflow_id: str,
        step: str,
        iteration: int,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.store = HumanTaskRecordStore(issue_dir)
        self.workflow_id = workflow_id
        self.step = step
        self.iteration = iteration
        self._now = now or (lambda: datetime.now(timezone.utc))

    def request_approval(
        self,
        *,
        request: ExecutionRequest,
        manifest: CapabilityManifest,
        expires_at: Optional[str] = None,
    ) -> HumanTask:
        evaluation = evaluate_capability_request({manifest.id: manifest}, request.model_dump())
        if evaluation.decision is not PolicyDecision.REQUIRE_APPROVAL:
            raise CapabilityApprovalError(
                f"current policy does not require approval: {evaluation.reason_code}"
            )
        fingerprint = canonical_request_fingerprint(request)
        snapshot = {
            "kind": CAPABILITY_APPROVAL_TRIGGER,
            "state": "pending",
            "workflow_id": self.workflow_id,
            "fingerprint": fingerprint,
            "request": _json(request),
            "manifest": _json(manifest),
            "manifest_digest": _manifest_digest(manifest),
            "capability": request.capability,
            "risk": manifest.risk,
            "argument_summary": _json(request.args),
            "effects": _json(request.effects),
            "credentials": list(request.credentials),
            "permissions": _json(request.permissions),
            "expected_outputs": list(manifest.outputs.required),
            "expires_at": expires_at,
            "decision": None,
            "revalidation": None,
            "attempt": None,
            "receipt": None,
        }
        prompt = (
            f"Capability approval: {request.capability} ({manifest.risk} risk). "
            "Review the exact request and material effects before deciding."
        )
        return self.store.materialize(
            workflow_id=self.workflow_id,
            step=self.step,
            iteration=self.iteration,
            trigger=CAPABILITY_APPROVAL_TRIGGER,
            policy_id=CAPABILITY_APPROVAL_POLICY_ID,
            prompt=prompt,
            expected_result={
                "input_schema": "capability_approval",
                "required": [
                    "decision",
                    "workflow_id",
                    "task_id",
                    "request_fingerprint",
                ],
                "decisions": ["approve", "deny"],
                "request_fingerprint": fingerprint,
            },
            continuations={"approve": self.step, "deny": self.step},
            assignee_type="user",
            capability_approval=snapshot,
            handoff_key=(
                f"capability:{self.workflow_id}:{self.step}:{request.capability}:{fingerprint}"
            ),
        )

    def inspect(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task.capability_approval is None:
            raise CapabilityApprovalError("task is not a capability approval")
        current = dict(task.capability_approval)
        if current["state"] in {"pending", "approved"} and self._is_expired(current):
            current["state"] = "expired"
            current["receipt"] = self._terminal_receipt(
                task_id=task_id,
                current=current,
                outcome="expired",
                recovery="Create a new capability request and approval task.",
            )
            self.store.transition_capability_approval(
                workflow_id=self.workflow_id,
                task_id=task_id,
                metadata=current,
                event_type="capability_approval_expired",
                terminal_status=HumanTaskStatus.CANCELLED,
            )
        return current

    def record_decision(self, task_id: str, payload: object) -> dict[str, Any]:
        with self.store.transaction():
            current = self.inspect(task_id)
            if current["state"] != "pending":
                return current
            if not isinstance(payload, Mapping):
                raise CapabilityApprovalError("capability approval must be structured JSON")
            required = {
                "decision",
                "workflow_id",
                "task_id",
                "request_fingerprint",
            }
            if not required.issubset(payload):
                raise CapabilityApprovalError("capability approval correlation is incomplete")
            if payload.get("decision") not in {"approve", "deny"}:
                raise CapabilityApprovalError("decision must be approve or deny")
            if (
                payload.get("workflow_id") != self.workflow_id
                or payload.get("task_id") != task_id
                or payload.get("request_fingerprint") != current["fingerprint"]
            ):
                raise CapabilityApprovalError(
                    "capability approval does not match the exact request"
                )
            now = self._now().astimezone().isoformat()
            current["state"] = (
                "approved" if payload["decision"] == "approve" else "denied"
            )
            current["decision"] = {
                "outcome": payload["decision"],
                "source": "capability_approval",
                "recorded_at": now,
            }
            self.store.transition_capability_approval(
                workflow_id=self.workflow_id,
                task_id=task_id,
                metadata=current,
                event_type="capability_approval_decided",
                terminal_status=HumanTaskStatus.COMPLETED,
                result_payload=dict(payload),
            )
            return current

    def cancel(self, task_id: str, *, reason: str) -> dict[str, Any]:
        current = self.inspect(task_id)
        if current["state"] != "pending":
            return current
        current["state"] = "cancelled"
        current["decision"] = {
            "outcome": "cancel",
            "source": "capability_approval",
            "reason": str(reason),
            "recorded_at": self._now().astimezone().isoformat(),
        }
        current["receipt"] = self._terminal_receipt(
            task_id=task_id,
            current=current,
            outcome="cancelled",
            recovery="Create a new capability request if execution is still required.",
        )
        self.store.transition_capability_approval(
            workflow_id=self.workflow_id,
            task_id=task_id,
            metadata=current,
            event_type="capability_approval_cancelled",
            terminal_status=HumanTaskStatus.CANCELLED,
        )
        return current

    def _is_expired(self, current: Mapping[str, Any]) -> bool:
        raw = current.get("expires_at")
        if not isinstance(raw, str) or not raw.strip():
            return False
        try:
            expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CapabilityApprovalError("approval expiry is invalid") from exc
        if expires_at.tzinfo is None:
            raise CapabilityApprovalError("approval expiry must include a timezone")
        return self._now().astimezone(timezone.utc) >= expires_at.astimezone(timezone.utc)

    def _terminal_receipt(
        self,
        *,
        task_id: str,
        current: Mapping[str, Any],
        outcome: str,
        recovery: str,
    ) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "task_id": task_id,
            "request_fingerprint": current["fingerprint"],
            "request": current["request"],
            "approval": current.get("decision"),
            "revalidation": current.get("revalidation"),
            "attempt": current.get("attempt"),
            "outcome": outcome,
            "executed": False,
            "recovery": recovery,
            "recorded_at": self._now().astimezone().isoformat(),
        }


def _manifest_digest(manifest: CapabilityManifest) -> str:
    import hashlib
    import json

    payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
