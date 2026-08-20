"""Security invariants for exact-request capability approvals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cafe.core.capabilities import CapabilityManifest, ExecutionRequest, PolicyDecision
from cafe.core.capability_approvals import (
    CapabilityApprovalError,
    CapabilityApprovalService,
)


def _manifest(**overrides: object) -> CapabilityManifest:
    data: dict[str, object] = {
        "id": "demo.mutate",
        "version": 1,
        "implementation": "open_current_pr",
        "arguments": {
            "required": ["target_ref"],
            "properties": {"target_ref": {"type": "string", "enum": ["current_pr"]}},
        },
        "outputs": {"required": [], "properties": {}},
        "effects": {
            "writes": ["artifact.json"],
            "network_destinations": ["api.example.test"],
            "browser_open": [],
        },
        "credentials": ["example-token"],
        "permissions": {"network": ["api.example.test"]},
        "idempotency": "unsafe",
        "risk": "high",
        "approval": "required",
        "policy": "allow",
    }
    data.update(overrides)
    return CapabilityManifest.model_validate(data)


def _request() -> ExecutionRequest:
    return ExecutionRequest.model_validate(
        {
            "capability": "demo.mutate",
            "args": {"target_ref": "current_pr"},
            "effects": {
                "writes": ["artifact.json"],
                "network_destinations": ["api.example.test"],
                "browser_open": [],
            },
            "credentials": ["example-token"],
            "permissions": {"network": ["api.example.test"]},
        }
    )


def _service(tmp_path: Path) -> CapabilityApprovalService:
    return CapabilityApprovalService(
        issue_dir=tmp_path / "issue",
        workflow_id="workflow-one",
        step="publish",
        iteration=1,
    )


def test_task_snapshots_exact_request_and_material_effects(tmp_path: Path) -> None:
    """Test List unit 1/8: task is capability-specific and exposes reviewed effects."""
    service = _service(tmp_path)
    task = service.request_approval(request=_request(), manifest=_manifest())

    approval = task.capability_approval
    assert approval is not None
    assert approval["request"]["capability"] == "demo.mutate"
    assert approval["risk"] == "high"
    assert approval["effects"]["writes"] == ["artifact.json"]
    assert approval["effects"]["network_destinations"] == ["api.example.test"]
    assert approval["credentials"] == ["example-token"]
    assert approval["permissions"] == {"network": ["api.example.test"]}
    assert approval["expected_outputs"] == []
    assert task.trigger == "capability_approval"
    assert task.policy_id == "capability-approval"


def test_same_pending_request_deduplicates_but_changed_request_does_not(tmp_path: Path) -> None:
    """Test List unit 2: only one task represents an exact pending request."""
    service = _service(tmp_path)
    first = service.request_approval(request=_request(), manifest=_manifest())
    duplicate = service.request_approval(request=_request(), manifest=_manifest())
    flexible = _manifest(
        arguments={
            "required": ["target_ref"],
            "properties": {
                "target_ref": {"type": "string", "enum": ["current_pr", "other"]}
            },
        }
    )
    first = service.request_approval(request=_request(), manifest=flexible)
    duplicate = service.request_approval(request=_request(), manifest=flexible)
    changed = _request().model_copy(update={"args": {"target_ref": "other"}})
    replacement = service.request_approval(request=changed, manifest=flexible)

    assert duplicate.id == first.id
    assert replacement.id != first.id
    assert replacement.capability_approval is not None
    assert replacement.capability_approval["fingerprint"] != first.capability_approval[  # type: ignore[index]
        "fingerprint"
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "yes",
        {},
        {"decision": "approve"},
        {"decision": "approve", "task_id": "other", "request_fingerprint": "wrong"},
    ],
)
def test_decision_requires_structured_exact_correlation(
    tmp_path: Path, payload: object
) -> None:
    """Test List unit 3/8: generic consent and other approval domains fail closed."""
    service = _service(tmp_path)
    task = service.request_approval(request=_request(), manifest=_manifest())

    with pytest.raises(CapabilityApprovalError):
        service.record_decision(task.id, payload)

    assert service.inspect(task.id)["state"] == "pending"


def test_denial_is_terminal_and_cannot_be_reopened(tmp_path: Path) -> None:
    """Test List unit 4: the first exact denial remains authoritative."""
    service = _service(tmp_path)
    task = service.request_approval(request=_request(), manifest=_manifest())
    fingerprint = task.capability_approval["fingerprint"]  # type: ignore[index]
    payload = {
        "decision": "deny",
        "workflow_id": "workflow-one",
        "task_id": task.id,
        "request_fingerprint": fingerprint,
    }

    denied = service.record_decision(task.id, payload)
    repeated = service.record_decision(task.id, {**payload, "decision": "approve"})

    assert denied["state"] == "denied"
    assert repeated == denied
    assert service.inspect(task.id)["state"] == "denied"


def test_cancel_and_request_declared_expiry_are_terminal(tmp_path: Path) -> None:
    """Test List unit 4: cancellation/expiry release the wait without dispatch."""
    service = _service(tmp_path)
    cancelled_task = service.request_approval(request=_request(), manifest=_manifest())

    cancelled = service.cancel(cancelled_task.id, reason="operator cancelled")

    assert cancelled["state"] == "cancelled"
    assert service.store.get_wait_state(cancelled_task.id).released_at is not None

    expired_task = service.request_approval(
        request=_request().model_copy(update={"args": {"target_ref": "current_pr"}}),
        manifest=_manifest(version=2),
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    expired = service.inspect(expired_task.id)

    assert expired["state"] == "expired"
    assert service.store.get_wait_state(expired_task.id).released_at is not None


def test_backward_generic_task_records_remain_readable(tmp_path: Path) -> None:
    """Test List unit 1: optional capability metadata preserves generic records."""
    from cafe.core.human_task_records import HumanTaskRecordStore

    store = HumanTaskRecordStore(tmp_path / "issue")
    task = store.materialize(
        workflow_id="workflow-one",
        step="develop",
        iteration=1,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Clarify.",
        expected_result={"input_schema": "feedback"},
        continuations={"submit": "develop"},
        assignee_type="user",
    )

    assert HumanTaskRecordStore(tmp_path / "issue").get_task(task.id).capability_approval is None
    assert PolicyDecision.REQUIRE_APPROVAL.value == "require_approval"
