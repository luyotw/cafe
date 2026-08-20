"""Durable approval lifecycle for one exact trusted-host capability request."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from cafe.core.capabilities import (
    CapabilityEvaluation,
    CapabilityManifest,
    ExecutionRequest,
    PolicyDecision,
    canonical_request_fingerprint,
    dispatch_revalidated_capability_request,
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
        correlation_id: Optional[str] = None,
    ) -> HumanTask:
        evaluation = evaluate_capability_request({manifest.id: manifest}, request.model_dump())
        if evaluation.decision is not PolicyDecision.REQUIRE_APPROVAL:
            raise CapabilityApprovalError(
                f"current policy does not require approval: {evaluation.reason_code}"
            )
        fingerprint = canonical_request_fingerprint(request)
        existing = self._find_request(fingerprint, _manifest_digest(manifest))
        if existing is not None:
            return existing
        correlation_id = correlation_id or uuid.uuid4().hex[:20]
        snapshot = {
            "kind": CAPABILITY_APPROVAL_TRIGGER,
            "state": "pending",
            "workflow_id": self.workflow_id,
            "correlation_id": correlation_id,
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
                    "correlation_id",
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

    def _find_request(
        self,
        fingerprint: str,
        manifest_digest: Optional[str] = None,
        *,
        states: Optional[set[str]] = None,
    ) -> Optional[HumanTask]:
        if not self.store.exists:
            return None
        matches = [
            task
            for task in self.store.tasks()
            if task.workflow_id == self.workflow_id
            and task.capability_approval is not None
            and task.capability_approval.get("fingerprint") == fingerprint
            and (
                manifest_digest is None
                or task.capability_approval.get("manifest_digest") == manifest_digest
            )
            and (states is not None or task.capability_approval.get("state") != "policy_rejected")
            and (states is None or str(task.capability_approval.get("state")) in states)
        ]
        return max(matches, key=lambda task: task.created_at) if matches else None

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
            if not isinstance(payload, Mapping):
                raise CapabilityApprovalError("capability approval must be structured JSON")
            required = {
                "decision",
                "workflow_id",
                "task_id",
                "request_fingerprint",
                "correlation_id",
            }
            if not required.issubset(payload):
                raise CapabilityApprovalError("capability approval correlation is incomplete")
            if payload.get("decision") not in {"approve", "deny"}:
                raise CapabilityApprovalError("decision must be approve or deny")
            if (
                payload.get("workflow_id") != self.workflow_id
                or payload.get("task_id") != task_id
                or payload.get("request_fingerprint") != current["fingerprint"]
                or payload.get("correlation_id") != current["correlation_id"]
            ):
                raise CapabilityApprovalError(
                    "capability approval does not match the exact request"
                )
            if current["state"] != "pending":
                return current
            now = self._now().astimezone().isoformat()
            current["state"] = "approved" if payload["decision"] == "approve" else "denied"
            current["decision"] = {
                "outcome": payload["decision"],
                "source": "capability_approval",
                "correlation_id": current["correlation_id"],
                "recorded_at": now,
            }
            if current["state"] == "denied":
                current["receipt"] = self._terminal_receipt(
                    task_id=task_id,
                    current=current,
                    outcome="denied",
                    recovery="Create a new capability request if execution is still required.",
                )
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

    def terminalize_policy_failure(
        self,
        *,
        request: ExecutionRequest,
        reason_code: str,
        evidence: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Consume an approved exact request when current policy is unavailable."""
        with self.store.transaction():
            task = self._find_request(
                canonical_request_fingerprint(request),
                states={"approved", "policy_rejected"},
            )
            if task is None:
                return None
            current = self.inspect(task.id)
            if current["state"] == "policy_rejected":
                receipt = current.get("receipt")
                return dict(receipt) if isinstance(receipt, Mapping) else None
            if current["state"] != "approved":
                return None
            current["revalidation"] = {
                "outcome": "error",
                "reason_code": reason_code,
                "checked_at": self._now().astimezone().isoformat(),
                **_json(evidence),
            }
            current["state"] = "policy_rejected"
            return self._finish(
                task.id,
                current,
                outcome="policy_rejected",
                executed=False,
                recovery="Restore a verifiable policy and create a new approval task.",
                event_type="capability_policy_rejected",
            )

    def resume(
        self,
        task_id: str,
        *,
        correlation_id: str,
        request: ExecutionRequest,
        registry: Mapping[str, CapabilityManifest],
        repo_root: Path,
        output_file: Path,
        timeout_sec: float = 600.0,
    ) -> dict[str, Any]:
        """Resume only the approved unchanged request behind its one-attempt fence."""
        with self.store.transaction():
            return self._resume_locked(
                task_id,
                correlation_id=correlation_id,
                request=request,
                registry=registry,
                repo_root=repo_root,
                output_file=output_file,
                timeout_sec=timeout_sec,
            )

    def _resume_locked(
        self,
        task_id: str,
        *,
        correlation_id: str,
        request: ExecutionRequest,
        registry: Mapping[str, CapabilityManifest],
        repo_root: Path,
        output_file: Path,
        timeout_sec: float,
    ) -> dict[str, Any]:
        current = self.inspect(task_id)
        if correlation_id != current["correlation_id"]:
            raise CapabilityApprovalError("capability resume does not match the host request")
        if current["state"] in TERMINAL_STATES:
            receipt = current.get("receipt")
            if isinstance(receipt, Mapping):
                return dict(receipt)
            raise CapabilityApprovalError("terminal capability approval has no receipt")
        if current["state"] == "attempt_started":
            current["state"] = "uncertain"
            current["attempt"] = {
                **dict(current.get("attempt") or {}),
                "state": "uncertain",
                "finished_at": self._now().astimezone().isoformat(),
            }
            return self._finish(
                task_id,
                current,
                outcome="uncertain",
                executed=True,
                recovery=(
                    "Inspect the host system and reconcile manually; automatic retry is disabled."
                ),
                event_type="capability_attempt_uncertain",
            )
        if current["state"] != "approved":
            raise CapabilityApprovalError("matching capability approval is still required")

        fingerprint = canonical_request_fingerprint(request)
        if fingerprint != current["fingerprint"]:
            current["state"] = "tampered"
            return self._finish(
                task_id,
                current,
                outcome="tampered",
                executed=False,
                recovery="Evaluate the changed request and create a new approval task.",
                event_type="capability_request_tampered",
            )

        try:
            evaluation = evaluate_capability_request(registry, request.model_dump(mode="json"))
        except Exception as exc:
            current["revalidation"] = {
                "outcome": "error",
                "reason_code": type(exc).__name__,
                "checked_at": self._now().astimezone().isoformat(),
            }
            current["state"] = "policy_rejected"
            return self._finish(
                task_id,
                current,
                outcome="policy_rejected",
                executed=False,
                recovery="Restore a verifiable policy and create a new approval task.",
                event_type="capability_policy_rejected",
            )

        current["revalidation"] = self._revalidation_record(evaluation)
        if evaluation.decision is PolicyDecision.DENY or not self._same_reviewed_boundary(
            current, evaluation.manifest
        ):
            current["state"] = "policy_rejected"
            return self._finish(
                task_id,
                current,
                outcome="policy_rejected",
                executed=False,
                recovery="Evaluate the current request and policy through a new approval task.",
                event_type="capability_policy_rejected",
            )

        started_at = self._now().astimezone().isoformat()
        current["state"] = "attempt_started"
        current["attempt"] = {"state": "started", "started_at": started_at}
        persisted, transitioned = self.store.transition_capability_approval_if_state(
            workflow_id=self.workflow_id,
            task_id=task_id,
            expected_state="approved",
            metadata=current,
            event_type="capability_attempt_started",
        )
        if not transitioned:
            persisted_state = persisted.capability_approval
            if persisted_state is None:
                raise CapabilityApprovalError("task is not a capability approval")
            receipt = persisted_state.get("receipt")
            if isinstance(receipt, Mapping):
                return dict(receipt)
            raise CapabilityApprovalError("capability execution attempt is already in progress")

        run = dispatch_revalidated_capability_request(
            repo_root=repo_root,
            evaluation=evaluation,
            output_file=output_file,
            timeout_sec=timeout_sec,
            correlation_id=str(current["correlation_id"]),
        )
        succeeded = bool(run.receipt.get("success"))
        current["state"] = "succeeded" if succeeded else "failed"
        current["attempt"] = {
            **dict(current["attempt"]),
            "state": "finished",
            "finished_at": self._now().astimezone().isoformat(),
            "outcome": "succeeded" if succeeded else "failed",
        }
        return self._finish(
            task_id,
            current,
            outcome=current["state"],
            executed=True,
            recovery=(
                "No recovery is required."
                if succeeded
                else "Inspect the recorded failure; automatic retry is disabled."
            ),
            event_type="capability_attempt_finished",
            execution_receipt=run.receipt,
        )

    def _finish(
        self,
        task_id: str,
        current: dict[str, Any],
        *,
        outcome: str,
        executed: bool,
        recovery: str,
        event_type: str,
        execution_receipt: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        receipt = self._terminal_receipt(
            task_id=task_id,
            current=current,
            outcome=outcome,
            recovery=recovery,
        )
        receipt["executed"] = executed
        if execution_receipt is not None:
            receipt["execution"] = dict(execution_receipt)
        current["receipt"] = receipt
        self.store.update_capability_approval(
            workflow_id=self.workflow_id,
            task_id=task_id,
            metadata=current,
            event_type=event_type,
        )
        return receipt

    def _revalidation_record(self, evaluation: CapabilityEvaluation) -> dict[str, Any]:
        return {
            "outcome": evaluation.decision.value,
            "reason_code": evaluation.reason_code,
            "manifest_digest": _manifest_digest(evaluation.manifest),
            "checked_at": self._now().astimezone().isoformat(),
        }

    @staticmethod
    def _same_reviewed_boundary(current: Mapping[str, Any], manifest: CapabilityManifest) -> bool:
        reviewed = current.get("manifest")
        if not isinstance(reviewed, Mapping):
            return False
        live = manifest.model_dump(mode="json")
        security_fields = {
            "id",
            "version",
            "implementation",
            "arguments",
            "outputs",
            "effects",
            "credentials",
            "permissions",
            "idempotency",
            "risk",
        }
        return all(reviewed.get(field) == live.get(field) for field in security_fields)

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
            "correlation_id": current["correlation_id"],
            "task_id": task_id,
            "capability": current["capability"],
            "request_fingerprint": current["fingerprint"],
            "request": current["request"],
            "approval": current.get("decision"),
            "revalidation": current.get("revalidation"),
            "attempt": current.get("attempt"),
            "outcome": outcome,
            "success": outcome == "succeeded",
            "category": None if outcome == "succeeded" else "capability_approval",
            "code": None if outcome == "succeeded" else outcome,
            "executed": False,
            "recovery": recovery,
            "recorded_at": self._now().astimezone().isoformat(),
        }


def _manifest_digest(manifest: CapabilityManifest) -> str:
    import hashlib
    import json

    payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
