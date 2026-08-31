"""Dedicated delegated-driver transport isolation tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.core.blackboard import BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract
from cafe.core.driver_runtime import DriverCoordinator, DriverPacket, DriverUnavailableError
from cafe.core.driver_transport import (
    DRIVER_AGENT_NAME,
    BlackboardDriverSessionStore,
    DelegatedDriverTransport,
)
from cafe.core.types import AgentCLI, AgentResponse, TokenUsage


def _policy(cli: str = "codex") -> DriverPolicyContract:
    return DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {
                "mode": "delegated",
                "delegated": {"cli": cli, "availability": "required"},
            },
            "execution": {"advancement": "continuous", "hosting": "foreground"},
        }
    )


def _runtime(issue_dir: Path, cli: str = "codex"):
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    coordinator = DriverCoordinator(store, state)
    packet = coordinator.open_boundary(completed_phase="spec", requested_action="plan")
    sessions = BlackboardDriverSessionStore(store, state, acquisition_sequence=packet.sequence)
    transport = DelegatedDriverTransport(_policy(cli), sessions)
    return store, state, packet, sessions, transport


@pytest.mark.parametrize("cli", ["claude", "codex", "gemini", "copilot", "cursor-agent"])
def test_each_cli_starts_sessionless_then_resumes_only_blackboard_pair(
    tmp_path: Path, cli: str
) -> None:
    _, _, packet, sessions, transport = _runtime(tmp_path / cli, cli)
    attempted_sessions: list[str | None] = []
    cli_enum = AgentCLI(cli)

    def execute(executor, *_args, **_kwargs):
        attempted_sessions.append(executor.config.session_id)
        return AgentResponse(
            response=json.dumps(
                {
                    "workflow_id": packet.workflow_id,
                    "sequence": packet.sequence,
                    "requested_action": packet.requested_action,
                    "action": "advance",
                }
            ),
            token_usage=TokenUsage(),
            cli=cli_enum,
            session_id=f"driver-{cli}-session",
        )

    with patch.object(AgentExecutor, "execute", execute):
        decision = transport.request_decision(packet)

    assert decision.action == "advance"
    assert attempted_sessions == [None]
    saved = sessions.load_session(DRIVER_AGENT_NAME, cli_enum)
    assert saved is not None
    assert saved.session_id == f"driver-{cli}-session"

    with patch.object(AgentExecutor, "execute", execute):
        transport.request_decision(packet)

    assert attempted_sessions[-1] == f"driver-{cli}-session"


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
            response=json.dumps(
                {
                    "workflow_id": packet_a.workflow_id,
                    "sequence": packet_a.sequence,
                    "requested_action": packet_a.requested_action,
                    "action": "advance",
                }
            ),
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
    sessions = BlackboardDriverSessionStore(store, state, acquisition_sequence=1)
    unattended = DriverPolicyContract.model_validate(
        {
            "contract_version": 2,
            "driver": {"mode": "unattended"},
            "execution": {"advancement": "continuous", "hosting": "foreground"},
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
                    "delegated": {
                        "cli": "codex",
                        "availability": "required",
                        "session_id": "injected",
                    },
                },
                "execution": {"advancement": "continuous", "hosting": "foreground"},
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
