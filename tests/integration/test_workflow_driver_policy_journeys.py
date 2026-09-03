"""Production-seam journeys for outer driver orchestration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.agents.executor import AgentExecutor
from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, AgentResponse, TokenUsage
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.orchestration.delegated_controller import DelegatedWorkflowController
from cafe.orchestration.driver_policy import DriverPolicyContract, extract_driver_policy
from cafe.orchestration.driver_runtime import DriverCoordinator, DriverDecision
from cafe.orchestration.driver_transport import (
    DRIVER_AGENT_NAME,
    BlackboardDriverSessionStore,
    DelegatedDriverTransport,
)
from cafe.services.summary_display import SummaryDisplay
from cafe.services.summary_service import SummaryService
from cafe.ui.cli import app


def _init_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "develop"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "cafe-test@local.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "CAFE Test"], cwd=path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "Initial"], cwd=path, check=True)


def _policy(mode: str = "delegated") -> DriverPolicyContract:
    driver: dict[str, object] = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 10
    elif mode == "delegated":
        driver.update({"cli": "codex", "model": "exact-driver-model"})
    return DriverPolicyContract.model_validate({"contract_version": 2, "driver": driver})


def _decision(packet) -> DriverDecision:
    return DriverDecision(
        workflow_id=packet.workflow_id,
        sequence=packet.sequence,
        requested_action=packet.requested_action,
        completed_phase=packet.completed_phase,
        boundary_id=packet.boundary_id,
        contract_version=packet.contract_version,
        driver_cli=packet.driver_cli,
        driver_model=packet.driver_model,
        action="advance",
    )


def _two_step_playbook() -> dict:
    return {
        "playbook": {"id": "outer-driver-journey"},
        "steps": {
            "spec": {
                "skill": "spec_first",
                "role": "pm",
                "valid_intents": ["await_agent"],
                "on": {"await_agent": "plan"},
            },
            "plan": {
                "skill": "spec_first",
                "role": "developer",
                "valid_intents": ["workflow_complete"],
                "on": {"workflow_complete": "_done"},
            },
        },
    }


def test_supported_app_prepares_updates_and_corrects_policy_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    _init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    prepared = runner.invoke(
        app,
        [
            "prepare",
            "journey",
            "--base",
            "develop",
            "--no-check",
            "--driver-contract-version",
            "2",
            "--driver-mode",
            "unattended",
        ],
    )

    assert prepared.exit_code == 0, (prepared.stdout, prepared.exception)
    config_path = tmp_path / ".cafe" / "issues" / "journey" / "issue.yaml"
    assert extract_driver_policy(yaml.safe_load(config_path.read_text(encoding="utf-8"))) == _policy(
        "unattended"
    )

    updated = runner.invoke(
        app,
        [
            "update-driver-policy",
            "journey",
            "--contract-version",
            "2",
            "--driver-mode",
            "delegated",
            "--delegated-cli",
            "codex",
            "--delegated-model",
            "exact-driver-model",
        ],
    )
    assert updated.exit_code == 0, (updated.stdout, updated.exception)
    assert extract_driver_policy(yaml.safe_load(config_path.read_text(encoding="utf-8"))) == _policy()


@pytest.mark.parametrize("cli", ["claude", "codex", "gemini", "copilot", "cursor-agent"])
def test_delegated_transport_uses_one_exact_durable_session_identity(
    tmp_path: Path, cli: str
) -> None:
    store = BlackboardStore(tmp_path)
    state = store.load_or_create("spec")
    policy = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "delegated", "cli": cli, "model": "exact-model"},
        }
    )
    packet = DriverCoordinator(store, state).open_boundary(
        completed_phase="spec", requested_action="plan", policy=policy
    )
    sessions = BlackboardDriverSessionStore(
        store, state, acquisition_sequence=packet.sequence, requested_model="exact-model"
    )
    transport = DelegatedDriverTransport(policy, sessions)
    attempted: list[tuple[str | None, str | None]] = []

    def execute(executor, *_args, **_kwargs):
        attempted.append((executor.config.session_id, executor.config.model))
        return AgentResponse(
            response=json.dumps(_decision(packet).model_dump(mode="json")),
            token_usage=TokenUsage(),
            cli=AgentCLI(cli),
            session_id=f"driver-{cli}",
        )

    with patch.object(AgentExecutor, "execute", execute):
        assert transport.request_decision(packet).action == "advance"
        assert transport.request_decision(packet).action == "advance"

    assert attempted == [(None, "exact-model"), (f"driver-{cli}", "exact-model")]
    assert sessions.load_session(DRIVER_AGENT_NAME, AgentCLI(cli)).session_id == f"driver-{cli}"


def test_transition_replay_rebuilds_the_delegated_gate_from_durable_identity(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "transition-pointer-crash"
    executed: list[str] = []

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        executed.append(step_name)
        status = "await_agent" if step_name == "spec" else "workflow_complete"
        return StepExecutionResult(response=status, artifacts={}, status_code=status)

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_two_step_playbook(),
        executor=executor,
    )
    publish_current_step = interrupted.blackboard_store.set_current_step

    def crash_before_pointer(state, step: str) -> None:
        if step == "plan":
            raise RuntimeError("interrupted before current-step publication")
        publish_current_step(state, step)

    with patch.object(interrupted.blackboard_store, "set_current_step", side_effect=crash_before_pointer):
        with pytest.raises(RuntimeError, match="current-step publication"):
            interrupted.run(start_step="spec")

    crashed = BlackboardStore(issue_dir).load_or_create("spec")
    transition = next(event for event in crashed.events if event.event_type == "transition")
    assert crashed.current_step == "spec"
    assert transition.data["transition_id"]

    resumed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_two_step_playbook(),
        executor=executor,
    )
    packets = []
    result = DelegatedWorkflowController(
        resumed,
        _policy(),
        delegated_decision_provider=lambda packet: packets.append(packet) or _decision(packet),
    ).run()

    assert result.completed is True
    assert executed == ["spec", "plan"]
    assert [packet.boundary_id for packet in packets] == [
        f"transition:{transition.data['transition_id']}:plan"
    ]


def test_transition_replay_repairs_stale_baton_after_pointer_publication(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "transition-baton-crash"
    executed: list[str] = []

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        executed.append(step_name)
        status = "await_agent" if step_name == "spec" else "workflow_complete"
        return StepExecutionResult(response=status, artifacts={}, status_code=status)

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_two_step_playbook(),
        executor=executor,
    )
    publish_handoff = interrupted.blackboard_store.update_handoff_contract

    def crash_after_pointer(state, **kwargs) -> None:
        if kwargs.get("to_step") == "plan":
            raise RuntimeError("interrupted after pointer publication")
        publish_handoff(state, **kwargs)

    with patch.object(
        interrupted.blackboard_store,
        "update_handoff_contract",
        side_effect=crash_after_pointer,
    ):
        with pytest.raises(RuntimeError, match="pointer publication"):
            interrupted.run(start_step="spec")

    crashed = BlackboardStore(issue_dir).load_or_create("spec")
    transition = next(event for event in crashed.events if event.event_type == "transition")
    assert crashed.current_step == "plan"
    assert crashed.handoff_contract.to_step == "spec"

    resumed = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_two_step_playbook(),
        executor=executor,
    )
    packets = []
    result = DelegatedWorkflowController(
        resumed,
        _policy(),
        delegated_decision_provider=lambda packet: packets.append(packet) or _decision(packet),
    ).run()

    assert result.completed is True
    assert executed == ["spec", "plan"]
    assert [packet.boundary_id for packet in packets] == [
        f"transition:{transition.data['transition_id']}:plan"
    ]


def test_lifecycle_status_remains_session_safe(tmp_path: Path) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    issue_dir = issues_root / "lifecycle-inspection"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(_policy().model_dump(mode="json")), encoding="utf-8"
    )
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("review")
    with store.driver_transaction(state) as persisted:
        persisted.driver_state["session"] = {"session_id": "secret-session"}
    DriverCoordinator(store, state).record_lifecycle("permission", reason="operator approval required")

    status = SummaryService(issues_root=issues_root).load_driver_status(issue_dir.name)
    rendered = SummaryDisplay().format_driver_status(status)

    assert status["reason"] == "operator approval required"
    assert "secret-session" not in json.dumps(status)
    assert "Reason: operator approval required" in rendered
