from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cafe.core.blackboard import (
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
    LongRunningOperationArtifact,
    LongRunningOperationState,
    operation_receipt_path,
)
from cafe.core.long_running_operation_helper import get_operation_status
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.ui.cli import app


_PLAYBOOK = {
    "playbook": {"id": "default"},
    "steps": {"develop": {"skill": "develop", "role": "developer", "on": {"confirmed": "_done"}}},
}
_RUNNER = CliRunner()


def _write_running_operation(
    issue_dir: Path, iteration_dir: Path, operation_id: str = "op-1"
) -> LongRunningOperationArtifact:
    iteration_dir.mkdir(parents=True, exist_ok=True)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    return store.write_operation_artifact(
        state,
        step="develop",
        iteration_dir=iteration_dir,
        artifact=LongRunningOperationArtifact(
            operation_id=operation_id,
            state=LongRunningOperationState.RUNNING,
            reason="agent_timeout",
        ),
    )


def _setup_interrupted_step(issue_dir: Path, step: str = "develop") -> None:
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": step,
                "playbook_id": "default",
                "artifacts": {},
                "events": [
                    {
                        "timestamp": "2026-08-04T00:00:00+00:00",
                        "step": step,
                        "event_type": "step_interrupted",
                        "message": "{}",
                        "data": {"step": step, "reason": "agent_idle_timeout"},
                    }
                ],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )


def _write_iteration_evidence(issue_dir: Path, step: str = "develop") -> Path:
    iteration_dir = issue_dir / step / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    (iteration_dir / "iteration.json").write_text(json.dumps({"iteration": 1}), encoding="utf-8")
    (iteration_dir / "output.md").write_text("# done\n", encoding="utf-8")
    (iteration_dir / "checklist.md").write_text("- [x] done\n", encoding="utf-8")
    return iteration_dir


def _write_downstream_baton(issue_dir: Path) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.update_handoff_contract(
        state,
        from_step="develop",
        to_owner=HandoffOwner.AGENT,
        to_step="review",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test",
    )


def _run_resume(issue_dir: Path) -> object:
    playbook = {
        "playbook": {"id": "default"},
        "steps": {
            "develop": {"skill": "develop", "role": "developer", "on": {"await_agent": "review"}},
            "review": {"skill": "review", "role": "developer", "on": {"confirmed": "_done"}},
        },
    }

    def executor(step_name: str, step_def: dict, state_obj: object) -> StepExecutionResult:
        raise AssertionError(f"executor must not be invoked for {step_name}")

    return BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(max_transitions=5)


def test_malformed_operation_receipt_is_schema_invalid_evidence(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text("{not-json", encoding="utf-8")

    store = BlackboardStore(issue_dir)

    try:
        store.read_operation_receipt(iteration_dir)
    except ValueError as exc:
        assert "operation_receipt.json" in str(exc)
    else:
        raise AssertionError("malformed operation receipt must not be treated as missing/running")


def test_unknown_operation_receipt_state_is_schema_invalid_evidence(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text(
        json.dumps(
            {
                "operation_id": "op-1",
                "step": "develop",
                "state": "executing",
                "reason": "not-a-supported-state",
                "exit_code": None,
            }
        ),
        encoding="utf-8",
    )

    store = BlackboardStore(issue_dir)

    try:
        store.read_operation_receipt(iteration_dir)
    except ValueError as exc:
        assert "state" in str(exc)
    else:
        raise AssertionError("unknown operation receipt state must not be treated as running")


def test_runtime_blocks_malformed_operation_receipt_as_schema_invalid(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "runtime-malformed-receipt"
    _setup_interrupted_step(issue_dir)
    iteration_dir = _write_iteration_evidence(issue_dir)
    _write_downstream_baton(issue_dir)
    _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text("{not-json", encoding="utf-8")

    result = _run_resume(issue_dir)

    assert result.final_status_code == "OPERATION_SCHEMA_INVALID"
    bb = BlackboardStore(issue_dir).load_or_create("develop")
    assert any(
        e.event_type == "operation_blocked" and e.data.get("outcome") == "schema_invalid"
        for e in bb.events
    )


def test_runtime_blocks_unknown_operation_receipt_state_as_schema_invalid(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "runtime-unknown-receipt"
    _setup_interrupted_step(issue_dir)
    iteration_dir = _write_iteration_evidence(issue_dir)
    _write_downstream_baton(issue_dir)
    _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text(
        json.dumps(
            {
                "operation_id": "op-1",
                "state": "executing",
                "reason": "not-a-supported-state",
                "exit_code": None,
            }
        ),
        encoding="utf-8",
    )

    result = _run_resume(issue_dir)

    assert result.final_status_code == "OPERATION_SCHEMA_INVALID"


def test_runtime_blocks_untrusted_operation_receipt_metadata(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "runtime-untrusted-receipt"
    _setup_interrupted_step(issue_dir)
    iteration_dir = _write_iteration_evidence(issue_dir)
    _write_downstream_baton(issue_dir)
    operation = _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text(
        json.dumps(
            LongRunningOperationArtifact(
                operation_id=operation.operation_id,
                state=LongRunningOperationState.SUCCEEDED,
                reason="completed",
                exit_code=0,
            ).to_dict()
        ),
        encoding="utf-8",
    )

    result = _run_resume(issue_dir)

    assert result.final_status_code == "OPERATION_UNTRUSTED"
    bb = BlackboardStore(issue_dir).load_or_create("develop")
    assert any(
        e.event_type == "operation_blocked" and e.data.get("outcome") == "untrusted"
        for e in bb.events
    )


def test_runtime_blocks_operation_receipt_id_mismatch_as_untrusted(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "runtime-receipt-id-mismatch"
    _setup_interrupted_step(issue_dir)
    iteration_dir = _write_iteration_evidence(issue_dir)
    _write_downstream_baton(issue_dir)
    _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text(
        json.dumps(
            LongRunningOperationArtifact(
                operation_id="op-other",
                state=LongRunningOperationState.FAILED,
                reason="wrong-operation",
                exit_code=1,
            ).to_dict()
        ),
        encoding="utf-8",
    )

    result = _run_resume(issue_dir)

    assert result.final_status_code == "OPERATION_UNTRUSTED"


def test_terminal_receipt_without_blackboard_metadata_is_untrusted(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    operation = _write_running_operation(issue_dir, iteration_dir)
    operation_receipt_path(iteration_dir).write_text(
        json.dumps(
            {
                "operation_id": "op-1",
                "step": "develop",
                "state": LongRunningOperationState.SUCCEEDED.value,
                "reason": "completed",
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )

    store = BlackboardStore(issue_dir)
    receipt = store.read_operation_receipt(iteration_dir)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda *_args, **_kwargs: None,
    )

    assert isinstance(receipt, LongRunningOperationArtifact)
    assert not runtime._operation_receipt_trusted(
        current_step="develop",
        iteration_dir=iteration_dir,
        operation=operation,
        receipt=receipt,
    )


def test_terminal_receipt_with_mismatched_blackboard_metadata_is_untrusted(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    operation = _write_running_operation(issue_dir, iteration_dir)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.write_operation_receipt(
        state,
        step="develop",
        operation_id="op-1",
        iteration_dir=iteration_dir,
        artifact=LongRunningOperationArtifact(
            operation_id="op-1",
            state=LongRunningOperationState.SUCCEEDED,
            reason="completed",
            exit_code=0,
        ),
    )
    artifact_entry = state.artifacts["develop_operation_receipt"]
    artifact_entry.summary = "long_running_operation_receipt:op-other:succeeded"
    store.save(state)
    receipt = store.read_operation_receipt(iteration_dir)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda *_args, **_kwargs: None,
    )

    assert isinstance(receipt, LongRunningOperationArtifact)
    assert not runtime._operation_receipt_trusted(
        current_step="develop",
        iteration_dir=iteration_dir,
        operation=operation,
        receipt=receipt,
    )


def test_terminal_receipt_with_wrong_blackboard_path_is_untrusted(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    operation = _write_running_operation(issue_dir, iteration_dir)
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.write_operation_receipt(
        state,
        step="develop",
        operation_id="op-1",
        iteration_dir=iteration_dir,
        artifact=LongRunningOperationArtifact(
            operation_id="op-1",
            state=LongRunningOperationState.SUCCEEDED,
            reason="completed",
            exit_code=0,
        ),
    )
    state.artifacts["develop_operation_receipt"].path = str(
        iteration_dir / "other_operation_receipt.json"
    )
    store.save(state)
    receipt = store.read_operation_receipt(iteration_dir)
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_PLAYBOOK,
        executor=lambda *_args, **_kwargs: None,
    )

    assert isinstance(receipt, LongRunningOperationArtifact)
    assert not runtime._operation_receipt_trusted(
        current_step="develop",
        iteration_dir=iteration_dir,
        operation=operation,
        receipt=receipt,
    )


def test_get_operation_status_invalid_handle_field_types_return_lost(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    _write_running_operation(issue_dir, iteration_dir)
    (iteration_dir / "operation_handle.json").write_text(
        json.dumps(
            {
                "operation_id": "op-1",
                "monitor_pid": ["x"],
                "monitor_pid_start_time": "123",
                "started_at": "2026-08-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    status = get_operation_status(
        issue_dir=issue_dir,
        step="develop",
        iteration_dir=iteration_dir,
        playbook=_PLAYBOOK,
    )

    assert status.state == LongRunningOperationState.LOST
    assert status.reason == "operation_handle_invalid"


def test_operation_status_cli_invalid_handle_field_types_returns_lost(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-cli"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    _write_running_operation(issue_dir, iteration_dir)
    (iteration_dir / "operation_handle.json").write_text(
        json.dumps(
            {
                "operation_id": "op-1",
                "monitor_pid": ["x"],
                "monitor_start_time": "123",
                "started_at": "2026-08-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = _RUNNER.invoke(
        app,
        [
            "operation",
            "status",
            "--issue-dir",
            str(issue_dir),
            "--step",
            "develop",
            "--iteration-dir",
            str(iteration_dir),
        ],
    )

    assert result.exit_code == 0
    assert '"state": "lost"' in result.stdout
    assert "operation_handle_invalid" in result.stdout
