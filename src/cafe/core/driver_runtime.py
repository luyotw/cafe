"""Durable provider-neutral workflow driver boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cafe.core.blackboard import BlackboardState, BlackboardStore
from cafe.core.driver_policy import DriverPolicyContract


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StrictDriverModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DriverPacket(_StrictDriverModel):
    workflow_id: str
    sequence: int = Field(ge=1)
    completed_phase: str
    requested_action: str
    boundary_id: str
    created_at: str = Field(default_factory=_now_iso)

    @field_validator("workflow_id", "completed_phase", "requested_action", "boundary_id")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("driver packet fields must not be empty")
        return value


class DriverDecision(_StrictDriverModel):
    workflow_id: str
    sequence: int = Field(ge=1)
    requested_action: str
    action: Literal["advance", "pause", "stop"]
    rationale: str = ""
    decided_at: str = Field(default_factory=_now_iso)


class DriverBoundaryResolution(_StrictDriverModel):
    action_source: Literal[
        "attached",
        "unattended",
        "delegated",
        "unattended_fallback",
        "delegated_unavailable",
    ]
    requires_decision: bool
    pause: bool
    return_after_boundary: bool


def resolve_driver_boundary(
    policy: DriverPolicyContract,
    *,
    delegated_available: bool,
) -> DriverBoundaryResolution:
    """Resolve ownership independently from advancement and hosting."""
    return_after_boundary = policy.execution.advancement == "single_step"
    if policy.driver.mode == "attached":
        return DriverBoundaryResolution(
            action_source="attached",
            requires_decision=False,
            pause=False,
            return_after_boundary=return_after_boundary,
        )
    if policy.driver.mode == "unattended":
        return DriverBoundaryResolution(
            action_source="unattended",
            requires_decision=False,
            pause=False,
            return_after_boundary=return_after_boundary,
        )
    if delegated_available:
        return DriverBoundaryResolution(
            action_source="delegated",
            requires_decision=True,
            pause=False,
            return_after_boundary=return_after_boundary,
        )
    availability = policy.driver.delegated.availability if policy.driver.delegated else "required"
    if availability == "best_effort":
        return DriverBoundaryResolution(
            action_source="unattended_fallback",
            requires_decision=False,
            pause=False,
            return_after_boundary=return_after_boundary,
        )
    return DriverBoundaryResolution(
        action_source="delegated_unavailable",
        requires_decision=False,
        pause=True,
        return_after_boundary=True,
    )


def _initial_driver_state() -> dict[str, Any]:
    return {
        "lifecycle": "idle",
        "next_sequence": 1,
        "packets": {},
        "decisions": {},
        "consumed_sequences": [],
        "advancement_lease": None,
        "session": None,
        "worker": None,
        "notification_receipts": {},
    }


def _state_data(state: BlackboardState) -> dict[str, Any]:
    if not state.driver_state:
        state.driver_state = _initial_driver_state()
    else:
        defaults = _initial_driver_state()
        defaults.update(state.driver_state)
        state.driver_state = defaults
    return state.driver_state


class DriverCoordinator:
    """Serialize boundary packets, decisions, consumption, and advancement ownership."""

    def __init__(self, store: BlackboardStore, state: BlackboardState) -> None:
        self.store = store
        self.state = state

    def open_boundary(
        self,
        *,
        completed_phase: str,
        requested_action: str,
        boundary_id: str | None = None,
    ) -> DriverPacket:
        identity = boundary_id or f"{completed_phase}:{requested_action}"
        with self.store.driver_transaction(self.state) as state:
            data = _state_data(state)
            for raw_packet in data["packets"].values():
                packet = DriverPacket.model_validate(raw_packet)
                if packet.boundary_id == identity:
                    return packet
            sequence = int(data["next_sequence"])
            packet = DriverPacket(
                workflow_id=state.workflow_id,
                sequence=sequence,
                completed_phase=completed_phase,
                requested_action=requested_action,
                boundary_id=identity,
            )
            data["packets"][str(sequence)] = packet.model_dump(mode="json")
            data["next_sequence"] = sequence + 1
            data["lifecycle"] = "awaiting_decision"
            return packet

    def record_decision(self, decision: DriverDecision) -> DriverDecision:
        with self.store.driver_transaction(self.state) as state:
            data = _state_data(state)
            packet_raw = data["packets"].get(str(decision.sequence))
            if packet_raw is None:
                raise ValueError("driver decision sequence has no packet")
            packet = DriverPacket.model_validate(packet_raw)
            if (
                decision.workflow_id != state.workflow_id
                or decision.workflow_id != packet.workflow_id
            ):
                raise ValueError("driver decision workflow does not match packet provenance")
            if decision.requested_action != packet.requested_action:
                raise ValueError("driver decision requested action does not match packet")
            existing_raw = data["decisions"].get(str(decision.sequence))
            if existing_raw is not None:
                existing = DriverDecision.model_validate(existing_raw)
                if existing != decision:
                    raise ValueError("driver sequence already has a different decision")
                return existing
            data["decisions"][str(decision.sequence)] = decision.model_dump(mode="json")
            data["lifecycle"] = "ready" if decision.action == "advance" else "paused"
            return decision

    def consume_authorization(self, sequence: int) -> DriverDecision | None:
        with self.store.driver_transaction(self.state) as state:
            data = _state_data(state)
            consumed = {int(value) for value in data["consumed_sequences"]}
            if sequence in consumed:
                return None
            decision_raw = data["decisions"].get(str(sequence))
            if decision_raw is None:
                return None
            decision = DriverDecision.model_validate(decision_raw)
            if decision.action != "advance":
                return None
            data["consumed_sequences"].append(sequence)
            data["lifecycle"] = "advancing"
            return decision

    def claim_advancement_lease(self, holder: str, *, ttl_seconds: int) -> bool:
        if not holder.strip() or ttl_seconds <= 0:
            raise ValueError("holder and positive ttl_seconds are required")
        now = datetime.now(timezone.utc)
        with self.store.driver_transaction(self.state) as state:
            data = _state_data(state)
            lease = data.get("advancement_lease")
            if isinstance(lease, dict):
                expires_at = datetime.fromisoformat(str(lease["expires_at"]))
                if expires_at > now and lease.get("holder") != holder:
                    return False
            data["advancement_lease"] = {
                "holder": holder,
                "acquired_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            }
            return True

    def release_advancement_lease(self, holder: str) -> bool:
        with self.store.driver_transaction(self.state) as state:
            data = _state_data(state)
            lease = data.get("advancement_lease")
            if not isinstance(lease, dict) or lease.get("holder") != holder:
                return False
            data["advancement_lease"] = None
            return True
