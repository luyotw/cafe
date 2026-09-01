"""Isolated delegated-driver execution over the five supported CLI transports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.driver_policy import DelegatedDriverPolicy, DriverPolicyContract
from cafe.core.driver_runtime import (
    DriverDecision,
    DriverModelMismatchError,
    DriverPacket,
    DriverUnavailableError,
)
from cafe.core.session import SessionStore
from cafe.core.session_continuation import SessionContinuation
from cafe.core.types import AgentCLI, AgentConfig, SessionData

DRIVER_AGENT_NAME = "__cafe_delegated_driver__"
DRIVER_NAMESPACE = "cafe.workflow.driver.v2"


class BlackboardDriverSessionStore(SessionStore):
    """Store only CAFE-acquired driver session provenance in blackboard state."""

    def __init__(
        self,
        store: BlackboardStore,
        state: BlackboardState,
        *,
        acquisition_sequence: int,
        requested_model: str,
    ) -> None:
        self.store = store
        self.state = state
        self.acquisition_sequence = acquisition_sequence
        self.requested_model = requested_model

    def load_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> Optional[SessionData]:
        if agent_name != DRIVER_AGENT_NAME or issue_name is not None or phase_name is not None:
            return None
        with self.store.driver_transaction(self.state) as state:
            raw = state.driver_state.get("session")
            if not isinstance(raw, dict):
                return None
            if (
                raw.get("namespace") != DRIVER_NAMESPACE
                or raw.get("workflow_id") != state.workflow_id
                or raw.get("cli") != cli.value
                or raw.get("requested_model") != self.requested_model
            ):
                return None
            created_at = datetime.fromisoformat(str(raw["created_at"]))
            last_used_at = datetime.fromisoformat(str(raw["last_used_at"]))
            return SessionData(
                agent_name=DRIVER_AGENT_NAME,
                cli=cli,
                session_id=str(raw["session_id"]),
                created_at=created_at,
                last_used_at=last_used_at,
                phase_name=None,
            )

    def save_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        session_id: str,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> None:
        if (
            agent_name != DRIVER_AGENT_NAME
            or issue_name is not None
            or phase_name is not None
            or not session_id.strip()
        ):
            raise ValueError("delegated driver session provenance is not valid")
        now = datetime.now(timezone.utc).isoformat()
        with self.store.driver_transaction(self.state) as state:
            existing = state.driver_state.get("session")
            if isinstance(existing, dict):
                if (
                    existing.get("namespace") != DRIVER_NAMESPACE
                    or existing.get("workflow_id") != state.workflow_id
                    or existing.get("cli") != cli.value
                    or existing.get("requested_model") != self.requested_model
                    or existing.get("session_id") != session_id
                ):
                    raise ValueError("delegated driver session identity cannot be replaced")
                existing["last_used_at"] = now
                return
            state.driver_state["session"] = {
                "namespace": DRIVER_NAMESPACE,
                "workflow_id": state.workflow_id,
                "cli": cli.value,
                "requested_model": self.requested_model,
                "session_id": session_id,
                "acquisition_sequence": self.acquisition_sequence,
                "created_at": now,
                "last_used_at": now,
            }

    def continuation(self, cli: AgentCLI) -> SessionContinuation:
        saved = self.load_session(DRIVER_AGENT_NAME, cli)
        if saved is None:
            return SessionContinuation.new()
        return SessionContinuation.resume_exact(cli, saved.session_id)


class DelegatedDriverTransport:
    """Run one structured driver packet without consulting normal agent sessions."""

    def __init__(
        self,
        policy: DriverPolicyContract,
        session_store: BlackboardDriverSessionStore,
    ) -> None:
        if not isinstance(policy.driver, DelegatedDriverPolicy):
            raise ValueError("delegated transport requires delegated driver policy")
        self.policy = policy
        self.session_store = session_store
        self.cli = AgentCLI(policy.driver.cli)
        if session_store.requested_model != policy.driver.model:
            raise ValueError("delegated session model must match the policy's exact model")

    def request_decision(
        self,
        packet: DriverPacket,
        *,
        manager: AgentManager | None = None,
    ) -> DriverDecision:
        if packet.workflow_id != self.session_store.state.workflow_id:
            raise ValueError("driver packet belongs to a different workflow")
        agent_manager = manager or AgentManager(
            session_manager=self.session_store,
            issue_name=None,
            stream_agent_output=False,
        )
        agent_manager.register_agent(
            AgentConfig(
                name=DRIVER_AGENT_NAME,
                cli=self.cli,
                model=self.policy.driver.model,
                clis=[],
                backup_clis=[],
            )
        )
        prompt = json.dumps(
            {
                "contract": "cafe.workflow.driver.decision.v2",
                "completed_phase": packet.completed_phase,
                "requested_action": packet.requested_action,
                "workflow_id": packet.workflow_id,
                "sequence": packet.sequence,
                "response_schema": {
                    "workflow_id": packet.workflow_id,
                    "sequence": packet.sequence,
                    "requested_action": packet.requested_action,
                    "action": ["advance", "pause", "stop"],
                    "rationale": "optional string",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            execution = agent_manager.execute(
                DRIVER_AGENT_NAME,
                prompt,
                continuation=self.session_store.continuation(self.cli),
            )
        except AgentExecutionError as exc:
            raise DriverUnavailableError(str(exc)) from exc
        except (FileNotFoundError, OSError) as exc:
            raise DriverUnavailableError(str(exc)) from exc
        response = execution[0]
        reported_model = execution[5]
        if reported_model is not None and reported_model != self.policy.driver.model:
            with self.session_store.store.driver_transaction(
                self.session_store.state
            ) as state:
                state.driver_state["model_mismatch"] = {
                    "cli": self.cli.value,
                    "requested_model": self.policy.driver.model,
                    "reported_model": reported_model,
                    "sequence": packet.sequence,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
            raise DriverModelMismatchError(
                "delegated driver reported a model different from the requested model"
            )
        if self.session_store.load_session(DRIVER_AGENT_NAME, self.cli) is None:
            raise DriverUnavailableError("delegated driver session acquisition was not durable")
        try:
            raw = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError("delegated driver response must be one JSON decision") from exc
        decision = DriverDecision.model_validate(raw)
        if (
            decision.workflow_id != packet.workflow_id
            or decision.sequence != packet.sequence
            or decision.requested_action != packet.requested_action
        ):
            raise ValueError("delegated driver decision does not correlate to its packet")
        return decision
