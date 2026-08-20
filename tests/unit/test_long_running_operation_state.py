"""Tests for the strict four-state long-running operation model (issue #386).

Covers Test List items 8-12 from the development plan: the operation state
enum stays strict with no aliases, the artifact parser stays a small direct
parser, reason/exit_code stay explanatory only, and the fixed one-per-
iteration path is published through existing metadata artifact/event
helpers rather than a new registry.
"""

import hashlib
import json
from dataclasses import MISSING
from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactKind,
    BlackboardStore,
    LongRunningOperationArtifact,
    LongRunningOperationState,
    OperationLogPolicy,
    OperationMonitoring,
    OperationRisk,
    operation_artifact_path,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

_OPERATION_DECISION = {
    "risk": "low",
    "monitoring": "final-only",
    "log_policy": "summary-only",
    "stop_condition": "operation reaches a terminal state",
    "recovery": "inspect the same operation id",
}


@pytest.mark.parametrize(
    ("risk", "monitoring", "log_policy"),
    [
        (OperationRisk.LOW, OperationMonitoring.FINAL_ONLY, OperationLogPolicy.SUMMARY_ONLY),
        (OperationRisk.MEDIUM, OperationMonitoring.PERIODIC, OperationLogPolicy.INCREMENTAL_TAIL),
        (OperationRisk.HIGH, OperationMonitoring.ACTIVE, OperationLogPolicy.FILTERED_STREAM),
    ],
)
def test_operation_decision_accepts_each_risk_policy(
    risk: OperationRisk,
    monitoring: OperationMonitoring,
    log_policy: OperationLogPolicy,
) -> None:
    """UT-008: every documented risk level has one compatible policy."""
    restored = LongRunningOperationArtifact.from_dict(
        LongRunningOperationArtifact(
            operation_id="op-123",
            state=LongRunningOperationState.RUNNING,
            risk=risk,
            monitoring=monitoring,
            log_policy=log_policy,
            stop_condition="stop at the declared safety boundary",
            recovery="inspect the same operation id",
        ).to_dict()
    )

    assert (restored.risk, restored.monitoring, restored.log_policy) == (
        risk,
        monitoring,
        log_policy,
    )


def test_operation_decision_requires_bounded_text_and_matching_policy() -> None:
    """UT-008: persisted operation decisions are complete and risk-driven."""
    payload = LongRunningOperationArtifact(
        operation_id="op-123",
        state=LongRunningOperationState.RUNNING,
        risk=OperationRisk.HIGH,
        monitoring=OperationMonitoring.ACTIVE,
        log_policy=OperationLogPolicy.FILTERED_STREAM,
        stop_condition="stop external mutation",
        recovery="restore from backup",
    ).to_dict()
    assert LongRunningOperationArtifact.from_dict(payload).risk == OperationRisk.HIGH

    payload["stop_condition"] = ""
    with pytest.raises(ValueError, match="stop_condition"):
        LongRunningOperationArtifact.from_dict(payload)

    payload["stop_condition"] = "stop external mutation"
    payload["monitoring"] = "periodic"
    with pytest.raises(ValueError, match="monitoring"):
        LongRunningOperationArtifact.from_dict(payload)

    payload["monitoring"] = "active"
    payload.pop("recovery")
    with pytest.raises(ValueError, match="recovery"):
        LongRunningOperationArtifact.from_dict(payload)


def test_operation_artifact_has_no_implicit_risk_policy() -> None:
    """UT-007: callers must provide every agent-owned operation decision field."""
    for name in ("risk", "monitoring", "log_policy", "stop_condition", "recovery"):
        assert LongRunningOperationArtifact.__dataclass_fields__[name].default is MISSING
    with pytest.raises(TypeError):
        LongRunningOperationArtifact(state=LongRunningOperationState.RUNNING)  # type: ignore[call-arg]


class TestLongRunningOperationStateEnum:
    """Test List item 8: the enum accepts only the four documented values."""

    @pytest.mark.parametrize("value", ["running", "succeeded", "failed", "lost"])
    def test_accepts_documented_states(self, value: str) -> None:
        assert LongRunningOperationState(value).value == value

    @pytest.mark.parametrize(
        "value",
        ["pending", "complete", "unknown", "chat_handoff", "RUNNING", "", "in_progress"],
    )
    def test_rejects_unknown_or_malformed_values(self, value: str) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationState(value)


class TestLongRunningOperationArtifactParser:
    """Test List item 9: the parser stays a small direct parser, no alias map."""

    def test_round_trips_minimal_running_artifact(self) -> None:
        artifact = LongRunningOperationArtifact(
            state=LongRunningOperationState.RUNNING,
            risk=OperationRisk.LOW,
            monitoring=OperationMonitoring.FINAL_ONLY,
            log_policy=OperationLogPolicy.SUMMARY_ONLY,
            stop_condition="test operation reaches a terminal state",
            recovery="inspect the same operation id",
        )
        restored = LongRunningOperationArtifact.from_dict(artifact.to_dict())
        assert restored.state == LongRunningOperationState.RUNNING
        assert restored.reason == ""
        assert restored.exit_code is None

    def test_missing_state_field_is_a_schema_error(self) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationArtifact.from_dict({"reason": "no state here"})

    def test_unknown_state_value_is_a_schema_error_no_alias(self) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationArtifact.from_dict({"state": "pending"})

    def test_chat_handoff_is_not_a_valid_operation_state(self) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationArtifact.from_dict({"state": "chat_handoff"})

    def test_non_object_payload_is_a_schema_error(self) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationArtifact.from_dict("running")  # type: ignore[arg-type]

    def test_exit_code_must_be_integer_when_present(self) -> None:
        with pytest.raises(ValueError):
            LongRunningOperationArtifact.from_dict({"state": "failed", "exit_code": "1"})

    def test_no_generic_alias_map_or_migration_helper_exists(self) -> None:
        """The parser does not grow alias/migration machinery over time."""
        assert not hasattr(LongRunningOperationState, "_alias_map")
        assert not hasattr(LongRunningOperationState, "from_legacy")
        assert not hasattr(LongRunningOperationArtifact, "from_legacy")
        assert not hasattr(LongRunningOperationArtifact, "ALIASES")


class TestReasonAndExitCodeAreExplanatoryOnly:
    """Test List item 10: reason/exit_code never change which state is in effect."""

    def test_reason_and_exit_code_do_not_change_failed_state(self) -> None:
        artifact = LongRunningOperationArtifact.from_dict(
            {
                "operation_id": "op-123",
                "state": "failed",
                "reason": "process killed",
                "exit_code": 137,
                **_OPERATION_DECISION,
            }
        )
        assert artifact.state == LongRunningOperationState.FAILED
        assert artifact.reason == "process killed"
        assert artifact.exit_code == 137

    def test_zero_exit_code_with_failed_state_stays_failed(self) -> None:
        """exit_code alone must never be reinterpreted as a different state."""
        artifact = LongRunningOperationArtifact.from_dict(
            {
                "operation_id": "op-123",
                "state": "failed",
                "exit_code": 0,
                "reason": "reported failure despite exit 0",
                **_OPERATION_DECISION,
            }
        )
        assert artifact.state == LongRunningOperationState.FAILED

    def test_nonzero_exit_code_with_succeeded_state_stays_succeeded(self) -> None:
        artifact = LongRunningOperationArtifact.from_dict(
            {
                "operation_id": "op-123",
                "state": "succeeded",
                "exit_code": 1,
                "reason": "non-zero but reported success",
                **_OPERATION_DECISION,
            }
        )
        assert artifact.state == LongRunningOperationState.SUCCEEDED

    def test_reason_defaults_to_empty_string(self) -> None:
        artifact = LongRunningOperationArtifact.from_dict(
            {"operation_id": "op-123", "state": "running", **_OPERATION_DECISION}
        )
        assert artifact.reason == ""

    def test_exit_code_defaults_to_none(self) -> None:
        artifact = LongRunningOperationArtifact.from_dict(
            {"operation_id": "op-123", "state": "running", **_OPERATION_DECISION}
        )
        assert artifact.exit_code is None


class TestOperationArtifactPath:
    """Test List item 11: fixed one-per-iteration path, no registry."""

    def test_path_is_fixed_operation_json_in_iteration_dir(self, tmp_path: Path) -> None:
        iteration_dir = tmp_path / "develop" / "iteration_003"
        path = operation_artifact_path(iteration_dir)
        assert path == iteration_dir / "operation.json"

    def test_path_is_deterministic_for_same_iteration_dir(self, tmp_path: Path) -> None:
        iteration_dir = tmp_path / "develop" / "iteration_003"
        assert operation_artifact_path(iteration_dir) == operation_artifact_path(iteration_dir)


class TestOperationArtifactPersistence:
    """Test List item 12: operation state is published as blackboard metadata."""

    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "op-issue"
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        iteration_dir = issue_dir / "develop" / "iteration_001"
        iteration_dir.mkdir(parents=True)

        store.write_operation_artifact(
            state,
            step="develop",
            iteration_dir=iteration_dir,
            artifact=LongRunningOperationArtifact(
                state=LongRunningOperationState.RUNNING,
                reason="tool_timeout",
                risk=OperationRisk.LOW,
                monitoring=OperationMonitoring.FINAL_ONLY,
                log_policy=OperationLogPolicy.SUMMARY_ONLY,
                stop_condition="test operation reaches a terminal state",
                recovery="inspect the same operation id",
            ),
        )

        on_disk = json.loads((iteration_dir / "operation.json").read_text(encoding="utf-8"))
        assert on_disk["state"] == "running"

        loaded = store.read_operation_artifact(iteration_dir)
        assert loaded is not None
        assert loaded.state == LongRunningOperationState.RUNNING
        assert loaded.reason == "tool_timeout"

    def test_read_missing_artifact_returns_none(self, tmp_path: Path) -> None:
        store = BlackboardStore(tmp_path / ".cafe" / "issues" / "op-issue-2")
        iteration_dir = tmp_path / "no" / "such" / "iteration_001"
        assert store.read_operation_artifact(iteration_dir) is None

    def test_read_malformed_artifact_raises_schema_error(self, tmp_path: Path) -> None:
        store = BlackboardStore(tmp_path / ".cafe" / "issues" / "op-issue-3")
        iteration_dir = tmp_path / "develop" / "iteration_001"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "operation.json").write_text(
            json.dumps({"state": "pending"}), encoding="utf-8"
        )

        with pytest.raises(ValueError):
            store.read_operation_artifact(iteration_dir)


@pytest.mark.parametrize(
    "terminal_state",
    [
        LongRunningOperationState.SUCCEEDED,
        LongRunningOperationState.FAILED,
        LongRunningOperationState.LOST,
    ],
)
def test_terminal_operation_receipt_preserves_correlation_command_and_boundary(
    tmp_path: Path, terminal_state: LongRunningOperationState
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "audit"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    command = ["python", "-m", "demo"]
    fingerprint = hashlib.sha256(
        json.dumps(
            {"command": command, "cwd": str(tmp_path.resolve())},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.write_operation_artifact(
        state,
        step="develop",
        iteration_dir=iteration_dir,
        artifact=LongRunningOperationArtifact(
            operation_id="operation-1",
            correlation_id="correlation-1",
            command_fingerprint=fingerprint,
            state=LongRunningOperationState.RUNNING,
            effective_boundary={"cwd": str(tmp_path), "network_destinations": []},
            risk=OperationRisk.LOW,
            monitoring=OperationMonitoring.FINAL_ONLY,
            log_policy=OperationLogPolicy.SUMMARY_ONLY,
            stop_condition="operation terminates",
            recovery="inspect receipt",
        ),
    )
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook={
            "playbook": {"id": "default"},
            "steps": {"develop": {"skill": "develop", "role": "developer", "on": {}}},
        },
        executor=lambda *_args: StepExecutionResult(response="", artifacts={}),
    )

    receipt = runtime.record_long_running_operation_receipt(
        step="develop",
        iteration_dir=iteration_dir,
        operation_id="operation-1",
        state=terminal_state,
    )

    assert receipt.correlation_id == "correlation-1"
    assert receipt.command_fingerprint == fingerprint
    assert receipt.effective_boundary["cwd"] == str(tmp_path)

    def test_write_publishes_metadata_artifact_and_event_not_a_new_collection(
        self, tmp_path: Path
    ) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "op-issue-4"
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        iteration_dir = issue_dir / "develop" / "iteration_001"
        iteration_dir.mkdir(parents=True)

        store.write_operation_artifact(
            state,
            step="develop",
            iteration_dir=iteration_dir,
            artifact=LongRunningOperationArtifact(
                state=LongRunningOperationState.RUNNING,
                risk=OperationRisk.LOW,
                monitoring=OperationMonitoring.FINAL_ONLY,
                log_policy=OperationLogPolicy.SUMMARY_ONLY,
                stop_condition="test operation reaches a terminal state",
                recovery="inspect the same operation id",
            ),
        )

        # Reuses ArtifactEntry/ArtifactKind.METADATA + record_event; no new
        # BlackboardState collection (e.g. "long_running_operations") exists.
        assert not hasattr(state, "long_running_operations")
        assert not hasattr(state, "operations")
        artifact_entry = state.artifacts["develop_operation"]
        assert artifact_entry.kind == ArtifactKind.METADATA
        assert any(event.event_type == "long_running_operation" for event in state.events)

    def test_at_most_one_operation_reuses_same_path_on_second_write(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "op-issue-5"
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        iteration_dir = issue_dir / "develop" / "iteration_001"
        iteration_dir.mkdir(parents=True)

        store.write_operation_artifact(
            state,
            step="develop",
            iteration_dir=iteration_dir,
            artifact=LongRunningOperationArtifact(
                state=LongRunningOperationState.RUNNING,
                risk=OperationRisk.LOW,
                monitoring=OperationMonitoring.FINAL_ONLY,
                log_policy=OperationLogPolicy.SUMMARY_ONLY,
                stop_condition="test operation reaches a terminal state",
                recovery="inspect the same operation id",
            ),
        )
        store.write_operation_artifact(
            state,
            step="develop",
            iteration_dir=iteration_dir,
            artifact=LongRunningOperationArtifact(
                state=LongRunningOperationState.SUCCEEDED,
                risk=OperationRisk.LOW,
                monitoring=OperationMonitoring.FINAL_ONLY,
                log_policy=OperationLogPolicy.SUMMARY_ONLY,
                stop_condition="test operation reaches a terminal state",
                recovery="inspect the same operation id",
            ),
        )

        # Still exactly one operation.json for this iteration, now updated.
        matches = list(iteration_dir.glob("operation*.json"))
        assert len(matches) == 1
        loaded = store.read_operation_artifact(iteration_dir)
        assert loaded is not None
        assert loaded.state == LongRunningOperationState.SUCCEEDED
