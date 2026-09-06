"""Purpose-specific public application API for durable Driver kickoff authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ._freshness import Freshness
from ._lifecycle import activate, adopt_legacy, evaluate, event_callback_policy, replace
from ._store import DriverContractMissingError, DriverContractUnsafeError


@dataclass(frozen=True)
class ActivateConfirmedContract:
    issue_dir: Path
    issue_name: str
    workflow_id: str
    confirmed_by: str
    confirmed_at: datetime
    proposal: Mapping[str, Any]


@dataclass(frozen=True)
class ReplaceConfirmedContract:
    issue_dir: Path
    issue_name: str
    workflow_id: str
    confirmed_by: str
    confirmed_at: datetime
    proposal: Mapping[str, Any]
    expected_predecessor_sha256: str
    kind: str
    delegated_change: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DriverEntryRequest:
    issue_dir: Path
    issue_name: str
    workflow_id: str
    fresh_facts: Mapping[str, Any]


@dataclass(frozen=True)
class LegacyAdoptionRequest:
    issue_dir: Path
    issue_name: str
    workflow_id: str


@dataclass(frozen=True)
class ActivationResult:
    revision: int
    contract_sha256: str
    created: bool


@dataclass(frozen=True)
class ReplacementResult:
    revision: int
    contract_sha256: str


@dataclass(frozen=True)
class DriverEntryResult:
    freshness: Freshness
    revision: int
    contract_sha256: str
    runtime: Mapping[str, Any]
    event: Mapping[str, Any] | None
    proactive_review: tuple[Mapping[str, str], ...]
    generic_inputs: Mapping[str, Any]


@dataclass(frozen=True)
class EventCallbackRequest:
    """Identity required to derive an event callback transport projection."""

    issue_dir: Path
    issue_name: str
    workflow_id: str


@dataclass(frozen=True)
class EventCallbackPolicy:
    """Digest-bound, policy-free runtime projection for one callback."""

    contract_sha256: str
    event: Mapping[str, Any] | None


@dataclass(frozen=True)
class LegacyAdoptionResult:
    adopted: bool
    revision: int | None
    contract_sha256: str | None
    disposition: str


def _time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("confirmation time must be timezone-aware")
    return value.isoformat()


def _freeze(value: Any) -> Any:
    """Recursively expose results as immutable value objects."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def activate_confirmed_contract(command: ActivateConfirmedContract) -> ActivationResult:
    revision, digest, created = activate(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
        confirmed_by=command.confirmed_by,
        confirmed_at=_time(command.confirmed_at),
        proposal=command.proposal,
    )
    return ActivationResult(revision, digest, created)


def replace_confirmed_contract(command: ReplaceConfirmedContract) -> ReplacementResult:
    revision, digest = replace(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
        confirmed_by=command.confirmed_by,
        confirmed_at=_time(command.confirmed_at),
        proposal=command.proposal,
        expected_predecessor_sha256=command.expected_predecessor_sha256,
        kind=command.kind,
        delegated_change=command.delegated_change,
    )
    return ReplacementResult(revision, digest)


def evaluate_driver_entry(command: DriverEntryRequest) -> DriverEntryResult:
    freshness, contract, digest = evaluate(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
        fresh_facts=command.fresh_facts,
    )
    phase_chains = {
        phase["name"]: tuple(dict(entry) for entry in phase["chain"])
        for phase in contract["phases"]
        if phase["assignee_type"] in {"agent", "hybrid"}
    }
    generic: dict[str, Any] = {
        "playbook_id": contract["playbook"]["id"],
        "phase_chains": phase_chains,
    }
    if "pr" in contract:
        generic["pr_auto_create"] = contract["pr"]["auto_create"]
    event = None
    if contract["driver"]["mode"] == "event-driven":
        event = {"clis": tuple(dict(item) for item in contract["driver"]["clis"])}
    return DriverEntryResult(
        freshness=freshness,
        revision=contract["revision"]["generation"],
        contract_sha256=digest,
        runtime=_freeze({"driver": contract["driver"], "checkout": contract["checkout"]}),
        event=_freeze(event) if event is not None else None,
        proactive_review=_freeze(contract["proactive_review"]["phase_decisions"]),
        generic_inputs=_freeze(generic),
    )


def event_callback_projection(command: EventCallbackRequest) -> EventCallbackPolicy:
    """Load and project the event transport from the checked contract only."""
    event, digest = event_callback_policy(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
    )
    return EventCallbackPolicy(contract_sha256=digest, event=_freeze(event) if event else None)


def adopt_legacy_contract(command: LegacyAdoptionRequest) -> LegacyAdoptionResult:
    adopted, revision, digest, disposition = adopt_legacy(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
    )
    return LegacyAdoptionResult(adopted, revision, digest, disposition)


__all__ = [
    "ActivateConfirmedContract",
    "ActivationResult",
    "DriverEntryRequest",
    "DriverEntryResult",
    "DriverContractMissingError",
    "DriverContractUnsafeError",
    "EventCallbackPolicy",
    "EventCallbackRequest",
    "Freshness",
    "LegacyAdoptionRequest",
    "LegacyAdoptionResult",
    "ReplaceConfirmedContract",
    "ReplacementResult",
    "activate_confirmed_contract",
    "adopt_legacy_contract",
    "evaluate_driver_entry",
    "event_callback_projection",
    "replace_confirmed_contract",
]
