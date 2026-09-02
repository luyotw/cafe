"""Dedicated delegated-driver transport isolation tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from cafe.agents.cli.claude import ClaudeCLI
from cafe.agents.cli.codex import CodexCLI
from cafe.agents.cli.copilot import CopilotCLI
from cafe.agents.cli.cursor import CursorCLI
from cafe.agents.cli.gemini import GeminiCLI
from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
from cafe.core.blackboard import BlackboardStore
from cafe.orchestration.driver_policy import DriverPolicyContract
from cafe.orchestration.driver_runtime import (
    DriverCoordinator,
    DriverDecision,
    DriverModelMismatchError,
    DriverPacket,
    DriverUnavailableError,
)
from cafe.orchestration.driver_transport import (
    DRIVER_AGENT_NAME,
    BlackboardDriverSessionStore,
    DelegatedDriverTransport,
)
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, TokenUsage


def _policy(cli: str = "codex", model: str = "exact-driver-model") -> DriverPolicyContract:
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {
                "mode": "delegated",
                "cli": cli,
                "model": model,
            },
        }
    )


def _runtime(issue_dir: Path, cli: str = "codex"):
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    coordinator = DriverCoordinator(store, state)
    policy = _policy(cli)
    packet = coordinator.open_boundary(
        completed_phase="spec", requested_action="plan", policy=policy
    )
    sessions = BlackboardDriverSessionStore(
        store,
        state,
        acquisition_sequence=packet.sequence,
        requested_model=policy.driver.model,
    )
    transport = DelegatedDriverTransport(policy, sessions)
    return store, state, packet, sessions, transport


def _decision_payload(packet, *, action: str = "advance") -> dict:
    return {
        "workflow_id": packet.workflow_id,
        "sequence": packet.sequence,
        "requested_action": packet.requested_action,
        "completed_phase": packet.completed_phase,
        "boundary_id": packet.boundary_id,
        "contract_version": packet.contract_version,
        "driver_cli": packet.driver_cli,
        "driver_model": packet.driver_model,
        "action": action,
    }


@pytest.mark.parametrize("cli", ["claude", "codex", "gemini", "copilot", "cursor-agent"])
def test_each_cli_starts_sessionless_then_resumes_only_blackboard_pair(
    tmp_path: Path, cli: str
) -> None:
    _, _, packet, sessions, transport = _runtime(tmp_path / cli, cli)
    attempted_sessions: list[str | None] = []
    attempted_models: list[str | None] = []
    attempted_controls: list[AgentExecutionControl] = []
    cli_enum = AgentCLI(cli)

    def execute(executor, *_args, **kwargs):
        attempted_sessions.append(executor.config.session_id)
        attempted_models.append(executor.config.model)
        attempted_controls.append(kwargs["execution_control"])
        return AgentResponse(
            response=json.dumps(_decision_payload(packet)),
            token_usage=TokenUsage(),
            cli=cli_enum,
            session_id=f"driver-{cli}-session",
        )

    with patch.object(AgentExecutor, "execute", execute):
        decision = transport.request_decision(packet)

    assert decision.action == "advance"
    assert attempted_sessions == [None]
    assert attempted_models == ["exact-driver-model"]
    saved = sessions.load_session(DRIVER_AGENT_NAME, cli_enum)
    assert saved is not None
    assert saved.session_id == f"driver-{cli}-session"
    provenance = sessions.state.driver_state["session"]
    assert provenance["requested_model"] == "exact-driver-model"
    assert provenance["acquisition_sequence"] == packet.sequence
    assert provenance["namespace"] == "cafe.workflow.driver.v2"

    with patch.object(AgentExecutor, "execute", execute):
        transport.request_decision(packet)

    assert attempted_sessions[-1] == f"driver-{cli}-session"
    assert attempted_models[-1] == "exact-driver-model"
    assert len(attempted_controls) == 2
    assert attempted_controls[0] == attempted_controls[1]
    assert attempted_controls[0].working_directory is not None


@pytest.mark.parametrize(
    ("cli", "strategy"),
    [
        (AgentCLI.CLAUDE, ClaudeCLI),
        (AgentCLI.CODEX, CodexCLI),
        (AgentCLI.GEMINI, GeminiCLI),
        (AgentCLI.COPILOT, CopilotCLI),
        (AgentCLI.CURSOR, CursorCLI),
    ],
)
@pytest.mark.parametrize("session_id", [None, "blackboard-session"])
def test_each_adapter_receives_exact_model_on_acquisition_and_resume(
    cli: AgentCLI, strategy, session_id: str | None
) -> None:
    command = strategy(
        AgentConfig(
            name=DRIVER_AGENT_NAME,
            cli=cli,
            model="user-selected-exact-model",
            session_id=session_id,
        )
    ).build_command("driver packet")

    model_index = command.index("--model")
    assert command[model_index + 1] == "user-selected-exact-model"
    if session_id is not None:
        assert session_id in command


@pytest.mark.parametrize(
    ("cli", "forbidden_flag"),
    [
        (AgentCLI.COPILOT, "--allow-all-tools"),
        (AgentCLI.CURSOR, "--force"),
    ],
)
def test_decision_only_command_does_not_auto_approve_tools(
    tmp_path: Path, cli: AgentCLI, forbidden_flag: str
) -> None:
    workspace = tmp_path / cli.value
    command = AgentExecutor(
        AgentConfig(name=DRIVER_AGENT_NAME, cli=cli, model="exact-driver-model")
    ).preview_cli_command_args(
        "driver packet",
        allowed_tools=[],
        allowed_directories=[],
        execution_control=AgentExecutionControl(working_directory=workspace),
    )

    assert forbidden_flag not in command


@pytest.mark.parametrize("cli", [AgentCLI.CLAUDE, AgentCLI.CODEX, AgentCLI.GEMINI])
@pytest.mark.parametrize("session_id", [None, "blackboard-session"])
def test_decision_only_command_enforces_empty_tool_and_workspace_scope(
    tmp_path: Path, cli: AgentCLI, session_id: str | None
) -> None:
    workspace = tmp_path / cli.value
    workspace.mkdir()
    control = AgentExecutionControl(
        working_directory=workspace,
        max_duration_seconds=120,
        max_output_bytes=256 * 1024,
        max_output_lines=2048,
    )

    command = AgentExecutor(
        AgentConfig(
            name=DRIVER_AGENT_NAME,
            cli=cli,
            model="exact-driver-model",
            session_id=session_id,
        )
    ).preview_cli_command_args(
        "driver packet",
        allowed_tools=[],
        allowed_directories=[],
        execution_control=control,
    )

    if cli == AgentCLI.CLAUDE:
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command
    elif cli == AgentCLI.CODEX:
        assert command[command.index("-C") + 1] == str(workspace.resolve())
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        disabled = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--disable"
        ]
        assert "shell_tool" in disabled
        assert "unified_exec" in disabled
    else:
        policy_path = Path(command[command.index("--policy") + 1])
        assert policy_path.parent == workspace.resolve()
        policy = policy_path.read_text(encoding="utf-8")
        assert 'toolName = "*"' in policy
        assert 'decision = "deny"' in policy


def test_transport_forwards_explicit_empty_capability_scope(tmp_path: Path) -> None:
    _, _, packet, sessions, transport = _runtime(tmp_path)
    captured: dict[str, object] = {}

    class RecordingManager:
        def register_agent(self, config) -> None:
            captured["config"] = config

        def execute(self, agent_name, _prompt, **kwargs):
            captured["agent_name"] = agent_name
            captured.update(kwargs)
            sessions.save_session(
                DRIVER_AGENT_NAME,
                AgentCLI.CODEX,
                "driver-session",
            )
            return (
                json.dumps(_decision_payload(packet)),
                TokenUsage(),
                [],
                [],
                [],
                "exact-driver-model",
            )

    decision = transport.request_decision(packet, manager=RecordingManager())

    assert isinstance(decision, DriverDecision)
    assert captured["allowed_tools"] == []
    assert captured["allowed_directories"] == []
    control = captured["execution_control"]
    assert isinstance(control, AgentExecutionControl)
    assert control.working_directory is not None
    assert control.working_directory.is_dir()
    assert control.max_duration_seconds == 120
    assert control.max_output_bytes == 256 * 1024
    assert control.max_output_lines == 2048


def test_transport_rejects_packet_from_previous_exact_policy_before_execution(
    tmp_path: Path,
) -> None:
    store, state, packet, _, _ = _runtime(tmp_path, "codex")
    new_policy = _policy("codex", model="new-exact-model")
    sessions = BlackboardDriverSessionStore(
        store,
        state,
        acquisition_sequence=packet.sequence,
        requested_model=new_policy.driver.model,
    )
    transport = DelegatedDriverTransport(new_policy, sessions)

    class UnexpectedManager:
        def register_agent(self, _config) -> None:
            pytest.fail("stale packet reached agent registration")

    with pytest.raises(ValueError):
        transport.request_decision(packet, manager=UnexpectedManager())


def test_exact_policy_change_archives_old_session_before_new_acquisition(
    tmp_path: Path,
) -> None:
    store, state, packet, sessions, _ = _runtime(tmp_path, "codex")
    sessions.save_session(DRIVER_AGENT_NAME, AgentCLI.CODEX, "old-session")
    replacement = BlackboardDriverSessionStore(
        store,
        state,
        acquisition_sequence=packet.sequence + 1,
        requested_model="new-exact-model",
    )

    replacement.save_session(DRIVER_AGENT_NAME, AgentCLI.CODEX, "new-session")

    assert state.driver_state["session"]["requested_model"] == "new-exact-model"
    assert state.driver_state["session"]["session_id"] == "new-session"
    assert state.driver_state["session_history"][-1]["session_id"] == "old-session"


def test_normal_sessions_sticky_cli_and_cross_workflow_identity_are_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    normal_dir = tmp_path / ".cafe" / "issues" / "issue432"
    sessions_dir = normal_dir / "sessions"
    sessions_dir.mkdir(parents=True)
    normal_session = sessions_dir / f"{DRIVER_AGENT_NAME}_codex.json"
    normal_session.write_text('{"session_id":"injected-normal-session"}', encoding="utf-8")
    active_clis = normal_dir / "active_clis.json"
    active_clis.write_text(
        json.dumps({DRIVER_AGENT_NAME: {"cli": "gemini", "model": None}}),
        encoding="utf-8",
    )
    normal_bytes = normal_session.read_bytes()
    active_bytes = active_clis.read_bytes()
    _, _, packet_a, sessions_a, transport_a = _runtime(tmp_path / "workflow-a")
    _, _, _, sessions_b, _ = _runtime(tmp_path / "workflow-b")
    attempted_sessions: list[str | None] = []

    def execute(executor, *_args, **_kwargs):
        attempted_sessions.append(executor.config.session_id)
        return AgentResponse(
            response=json.dumps(_decision_payload(packet_a)),
            token_usage=TokenUsage(),
            cli=AgentCLI.CODEX,
            session_id="workflow-a-driver",
        )

    with patch.object(AgentExecutor, "execute", execute):
        transport_a.request_decision(packet_a)

    assert attempted_sessions == [None]
    assert sessions_b.load_session(DRIVER_AGENT_NAME, AgentCLI.CODEX) is None
    assert normal_session.read_bytes() == normal_bytes
    assert active_clis.read_bytes() == active_bytes


def test_delegated_transport_rejects_non_delegated_policy(tmp_path: Path) -> None:
    store = BlackboardStore(tmp_path)
    state = store.load_or_create("spec")
    sessions = BlackboardDriverSessionStore(
        store, state, acquisition_sequence=1, requested_model="unused"
    )
    unattended = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "unattended"},
        }
    )

    with pytest.raises(ValueError):
        DelegatedDriverTransport(unattended, sessions)


def test_policy_and_packet_cannot_inject_driver_session_identity() -> None:
    with pytest.raises(ValidationError):
        DriverPolicyContract.model_validate(
            {
                "contract_version": 2,
                "driver": {
                    "mode": "delegated",
                    "cli": "codex",
                    "model": "gpt-5.6-codex",
                    "session_id": "injected",
                },
            }
        )
    with pytest.raises(ValidationError):
        DriverPacket.model_validate(
            {
                "workflow_id": "workflow",
                "sequence": 1,
                "completed_phase": "spec",
                "requested_action": "plan",
                "boundary_id": "spec:plan",
                "session_id": "injected",
            }
        )


def test_only_exact_saved_cli_pair_can_resume(tmp_path: Path) -> None:
    _, _, _, sessions, _ = _runtime(tmp_path)
    sessions.save_session(DRIVER_AGENT_NAME, AgentCLI.CODEX, "codex-session")

    assert sessions.continuation(AgentCLI.CODEX).session_id == "codex-session"
    assert sessions.continuation(AgentCLI.GEMINI).session_id is None


def test_transport_normalizes_cli_unavailability(tmp_path: Path) -> None:
    _, _, packet, _, transport = _runtime(tmp_path)

    with (
        patch.object(
            AgentExecutor,
            "execute",
            side_effect=AgentExecutionError("missing", error_type="cli_not_found"),
        ),
        pytest.raises(DriverUnavailableError),
    ):
        transport.request_decision(packet)


def test_reported_model_mismatch_is_persisted_and_pauses_safely(tmp_path: Path) -> None:
    _, state, packet, _, transport = _runtime(tmp_path)

    def execute(_executor, *_args, **_kwargs):
        return AgentResponse(
            response=json.dumps(_decision_payload(packet)),
            token_usage=TokenUsage(),
            cli=AgentCLI.CODEX,
            session_id="driver-session",
            model="different-reported-model",
        )

    with (
        patch.object(AgentExecutor, "execute", execute),
        pytest.raises(DriverModelMismatchError),
    ):
        transport.request_decision(packet)

    mismatch = state.driver_state["model_mismatch"]
    assert mismatch["requested_model"] == "exact-driver-model"
    assert mismatch["reported_model"] == "different-reported-model"
    assert mismatch["sequence"] == packet.sequence
