"""Integration coverage for explicit terminal operation recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cafe.core import packet_io
from cafe.core.blackboard import (
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
    LongRunningOperationArtifact,
    LongRunningOperationState,
    OperationLogPolicy,
    OperationMonitoring,
    OperationRecoveryAction,
    OperationRecoveryActor,
    OperationRecoveryAuthorization,
    OperationRisk,
)
from cafe.core.long_running_operation_helper import (
    get_operation_recovery_status,
    recover_operation,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

_PLAYBOOK = {
    "playbook": {"id": "default"},
    "steps": {
        "develop": {
            "skill": "develop",
            "role": "developer",
            "on": {"await_agent": "_done"},
        }
    },
}


def _terminal_operation(
    tmp_path: Path, *, state: LongRunningOperationState = LongRunningOperationState.LOST
) -> tuple[Path, Path, LongRunningOperationArtifact]:
    issue_dir = tmp_path / ".cafe" / "issues" / f"recover-{state.value}"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(json.dumps({"iteration": 1}), encoding="utf-8")
    (iteration_dir / "output.md").write_text("# interrupted\n", encoding="utf-8")
    (iteration_dir / "checklist.md").write_text("- [ ] retry required\n", encoding="utf-8")

    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.AGENT,
        to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
        source="workflow.interrupted_step",
    )
    operation = store.write_operation_artifact(
        blackboard,
        step="develop",
        iteration_dir=iteration_dir,
        artifact=LongRunningOperationArtifact(
            state=state,
            reason=f"fixture_{state.value}",
            risk=OperationRisk.LOW,
            monitoring=OperationMonitoring.FINAL_ONLY,
            log_policy=OperationLogPolicy.SUMMARY_ONLY,
            stop_condition="stop at fixture boundary",
            recovery="inspect exact operation before recovery",
        ),
    )
    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda *_args, **_kwargs: StepExecutionResult(response="unused", artifacts={}),
    ).record_long_running_operation_receipt(
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        state=state,
        reason=f"fixture_{state.value}",
    )
    refreshed = store.load_or_create("develop")
    store.record_event(
        refreshed,
        "step_interrupted",
        {"step": "develop", "reason": "operation_terminal"},
    )
    return issue_dir, iteration_dir, operation


def _authorization_for(
    iteration_dir: Path,
    operation: LongRunningOperationArtifact,
    *,
    authorized_by: OperationRecoveryActor,
    reason: str,
) -> OperationRecoveryAuthorization:
    return OperationRecoveryAuthorization(
        operation_id=operation.operation_id,
        operation_sha256=hashlib.sha256(
            (iteration_dir / "operation.json").read_bytes()
        ).hexdigest(),
        receipt_sha256=hashlib.sha256(
            (iteration_dir / "operation_receipt.json").read_bytes()
        ).hexdigest(),
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=authorized_by,
        reason=reason,
    )


@pytest.mark.parametrize(
    "terminal_state", [LongRunningOperationState.FAILED, LongRunningOperationState.LOST]
)
def test_explicit_recovery_preserves_evidence_and_allows_new_iteration(
    tmp_path: Path, terminal_state: LongRunningOperationState
) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path, state=terminal_state)
    operation_before = (iteration_dir / "operation.json").read_text(encoding="utf-8")
    receipt_before = (iteration_dir / "operation_receipt.json").read_text(encoding="utf-8")

    recovery = recover_operation(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=OperationRecoveryActor.DRIVER,
        reason="User authorized one new step iteration",
        playbook=_PLAYBOOK,
    )
    duplicate = recover_operation(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=OperationRecoveryActor.DRIVER,
        reason="User authorized one new step iteration",
        playbook=_PLAYBOOK,
    )

    assert recovery.created is True
    assert duplicate.created is False
    assert duplicate.authorization == recovery.authorization
    assert (iteration_dir / "operation.json").read_text(encoding="utf-8") == operation_before
    assert (iteration_dir / "operation_receipt.json").read_text(encoding="utf-8") == receipt_before
    assert (
        get_operation_recovery_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
        )
        == recovery.authorization
    )

    calls = []

    def executor(step_name: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        calls.append(step_name)
        next_iteration = issue_dir / step_name / "iteration_002"
        next_iteration.mkdir()
        (next_iteration / "iteration.json").write_text(
            json.dumps({"iteration": 2}), encoding="utf-8"
        )
        return StepExecutionResult(response="retried", artifacts={}, status_code="confirmed")

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=executor,
    ).run(start_step="develop", single_step=True)

    assert calls == ["develop"]
    assert (issue_dir / "develop" / "iteration_002" / "iteration.json").exists()
    assert result.final_status_code == "confirmed"

    # A later operation may replace the per-step metadata pointers, but the
    # original exact authorization remains an idempotent historical request.
    state = BlackboardStore(issue_dir).load_or_create("develop")
    BlackboardStore(issue_dir).write_operation_artifact(
        state,
        step="develop",
        iteration_dir=issue_dir / "develop" / "iteration_002",
        artifact=LongRunningOperationArtifact(
            state=LongRunningOperationState.RUNNING,
            risk=OperationRisk.LOW,
            monitoring=OperationMonitoring.FINAL_ONLY,
            log_policy=OperationLogPolicy.SUMMARY_ONLY,
            stop_condition="stop at new operation boundary",
            recovery="inspect the new operation",
        ),
    )
    assert (
        recover_operation(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            action=OperationRecoveryAction.RETRY_STEP,
            authorized_by=OperationRecoveryActor.DRIVER,
            reason="User authorized one new step iteration",
            playbook=_PLAYBOOK,
        ).created
        is False
    )


def test_recovery_rejects_running_mismatch_and_conflict(tmp_path: Path) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    common = {
        "issue_dir": issue_dir,
        "step": "develop",
        "iteration_dir": iteration_dir,
        "action": OperationRecoveryAction.RETRY_STEP,
        "authorized_by": OperationRecoveryActor.HUMAN,
        "reason": "Retry after inspection",
        "playbook": _PLAYBOOK,
    }
    with pytest.raises(ValueError, match="operation_id mismatch"):
        recover_operation(operation_id="wrong-operation", **common)

    recover_operation(operation_id=operation.operation_id, **common)
    with pytest.raises(ValueError, match="conflicting recovery"):
        recover_operation(
            operation_id=operation.operation_id,
            **{**common, "reason": "Different authorization"},
        )

    running_dir = tmp_path / ".cafe" / "issues" / "running"
    running_iteration = running_dir / "develop" / "iteration_001"
    running_store = BlackboardStore(running_dir)
    running_blackboard = running_store.load_or_create("develop")
    running = running_store.write_operation_artifact(
        running_blackboard,
        step="develop",
        iteration_dir=running_iteration,
        artifact=LongRunningOperationArtifact(
            state=LongRunningOperationState.RUNNING,
            risk=OperationRisk.LOW,
            monitoring=OperationMonitoring.FINAL_ONLY,
            log_policy=OperationLogPolicy.SUMMARY_ONLY,
            stop_condition="stop at fixture boundary",
            recovery="inspect exact operation before recovery",
        ),
    )
    with pytest.raises(ValueError, match="only failed or lost"):
        recover_operation(
            issue_dir=running_dir,
            step="develop",
            iteration_dir=running_iteration,
            operation_id=running.operation_id,
            action=OperationRecoveryAction.RETRY_STEP,
            authorized_by=OperationRecoveryActor.HUMAN,
            reason="Must not recover running work",
            playbook=_PLAYBOOK,
        )


def test_forged_recovery_file_and_event_do_not_bypass_terminal_gate(tmp_path: Path) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    forged = _authorization_for(
        iteration_dir,
        operation,
        authorized_by=OperationRecoveryActor.DRIVER,
        reason="forged",
    )
    recovery_path = iteration_dir / "operation_recovery.json"
    recovery_path.write_text(json.dumps(forged.to_dict()), encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop")
    store.record_event(
        blackboard,
        "operation_recovery_authorized",
        {
            "step": "develop",
            "operation_id": operation.operation_id,
            "action": "retry-step",
            "authorized_by": "driver",
            "reason": "forged",
            "path": str(recovery_path),
            "authorization_summary": forged.summary,
        },
    )
    calls = []

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda step, *_args, **_kwargs: calls.append(step),
    ).run(start_step="develop", single_step=True)

    assert calls == []
    assert result.final_status_code == "OPERATION_LOST"
    with pytest.raises(ValueError, match="not trusted"):
        get_operation_recovery_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
        )
    with pytest.raises(ValueError, match="not trusted"):
        recover_operation(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            action=OperationRecoveryAction.RETRY_STEP,
            authorized_by=OperationRecoveryActor.DRIVER,
            reason="forged",
            playbook=_PLAYBOOK,
        )


def test_identical_recovery_repairs_an_interrupted_registration(tmp_path: Path) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    authorization = _authorization_for(
        iteration_dir,
        operation,
        authorized_by=OperationRecoveryActor.HUMAN,
        reason="Retry after inspecting the lost operation",
    )
    (iteration_dir / "operation_recovery.json").write_text(
        json.dumps(authorization.to_dict()), encoding="utf-8"
    )

    result = recover_operation(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        action=authorization.action,
        authorized_by=authorization.authorized_by,
        reason=authorization.reason,
        playbook=_PLAYBOOK,
    )

    assert result.created is False
    state = BlackboardStore(issue_dir).load_or_create("develop")
    assert state.artifacts["develop_operation_recovery"].summary == authorization.summary
    assert sum(event.event_type == "operation_recovery_authorized" for event in state.events) == 1


def test_recovery_requires_a_trusted_terminal_receipt(tmp_path: Path) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    (iteration_dir / "operation_receipt.json").unlink()

    with pytest.raises(ValueError, match="terminal operation receipt is missing"):
        recover_operation(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            action=OperationRecoveryAction.RETRY_STEP,
            authorized_by=OperationRecoveryActor.DRIVER,
            reason="Must retain terminal proof",
            playbook=_PLAYBOOK,
        )


@pytest.mark.parametrize("evidence_name", ["operation.json", "operation_receipt.json"])
def test_authorized_recovery_fails_closed_if_evidence_changes(
    tmp_path: Path, evidence_name: str
) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    recover_operation(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=OperationRecoveryActor.DRIVER,
        reason="Authorize only the inspected evidence",
        playbook=_PLAYBOOK,
    )
    evidence_path = iteration_dir / evidence_name
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    calls: list[str] = []

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda step, *_args, **_kwargs: calls.append(step),
    ).run(start_step="develop", single_step=True)

    assert calls == []
    assert result.final_status_code in {"OPERATION_FAILED", "OPERATION_LOST"}


def test_recovery_blackboard_write_is_atomic_and_identical_retry_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    real_replace = packet_io.os.replace
    replace_count = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count == 3:
            raise OSError("simulated blackboard replace interruption")
        real_replace(source, target)

    with monkeypatch.context() as context:
        context.setattr(packet_io.os, "replace", fail_second_replace)
        with pytest.raises(OSError, match="simulated blackboard replace interruption"):
            recover_operation(
                issue_dir=issue_dir,
                step="develop",
                iteration_dir=iteration_dir,
                operation_id=operation.operation_id,
                action=OperationRecoveryAction.RETRY_STEP,
                authorized_by=OperationRecoveryActor.HUMAN,
                reason="Retry after an atomic persistence interruption",
                playbook=_PLAYBOOK,
            )

    blackboard_after = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert "develop_operation_recovery" not in blackboard_after["artifacts"]
    assert not any(
        event["event_type"] == "operation_recovery_authorized"
        for event in blackboard_after["events"]
    )
    repaired = recover_operation(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        operation_id=operation.operation_id,
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=OperationRecoveryActor.HUMAN,
        reason="Retry after an atomic persistence interruption",
        playbook=_PLAYBOOK,
    )
    assert repaired.created is False
    assert (
        get_operation_recovery_status(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
        )
        == repaired.authorization
    )


def test_recovery_rejects_an_operation_outside_the_active_step(tmp_path: Path) -> None:
    issue_dir, iteration_dir, operation = _terminal_operation(tmp_path)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.set_current_step(state, "review")

    with pytest.raises(ValueError, match="not the active workflow step"):
        recover_operation(
            issue_dir=issue_dir,
            step="develop",
            iteration_dir=iteration_dir,
            operation_id=operation.operation_id,
            action=OperationRecoveryAction.RETRY_STEP,
            authorized_by=OperationRecoveryActor.DRIVER,
            reason="Must not rewind an inactive step",
            playbook={
                **_PLAYBOOK,
                "steps": {
                    **_PLAYBOOK["steps"],
                    "review": {"skill": "review", "role": "reviewer"},
                },
            },
        )


def test_recovery_schema_rejects_extra_or_non_string_fields() -> None:
    valid = OperationRecoveryAuthorization(
        operation_id="op-1",
        operation_sha256="a" * 64,
        receipt_sha256="b" * 64,
        action=OperationRecoveryAction.RETRY_STEP,
        authorized_by=OperationRecoveryActor.HUMAN,
        reason="Inspected by a human",
    ).to_dict()

    with pytest.raises(ValueError, match="unsupported fields"):
        OperationRecoveryAuthorization.from_dict({**valid, "permission": "host"})
    with pytest.raises(ValueError, match="fields must be strings"):
        OperationRecoveryAuthorization.from_dict({**valid, "authorized_by": None})
