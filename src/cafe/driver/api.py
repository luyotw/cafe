"""Purpose-specific public application API for durable Driver kickoff authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from cafe.core.human_task_corrections import CorrectionResult
from cafe.driver.proxy import submit_authorized_correction

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


@dataclass(frozen=True)
class DriverEntryRequest:
    issue_dir: Path
    issue_name: str
    workflow_id: str
    fresh_facts: Mapping[str, Any]


@dataclass(frozen=True)
class DriverCorrectionCommand:
    """A task-scoped correction request from the current Driver runtime."""

    issue_dir: Path
    workflow_id: str
    task_id: str
    artifact: str
    base_hash: str | None
    content: str
    operation_id: str
    authorization_id: str
    suitability: Mapping[str, bool]
    manifest: tuple[dict[str, str], ...]
    completion_payload: Mapping[str, object] | None = None


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
    phase_model_authority: Mapping[str, tuple[Mapping[str, str], ...]]
    delivery_contract: Mapping[str, Any]
    confirmation_contract: Mapping[str, Any]


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
    )
    return ReplacementResult(revision, digest)


def evaluate_driver_entry(command: DriverEntryRequest) -> DriverEntryResult:
    freshness, contract, digest = evaluate(
        issue_dir=command.issue_dir,
        issue_name=command.issue_name,
        workflow_id=command.workflow_id,
        fresh_facts=command.fresh_facts,
    )

    phase_model_authority = {
        phase["name"]: tuple(dict(entry) for entry in phase["chain"])
        for phase in contract["phases"]
    }
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
        phase_model_authority=_freeze(phase_model_authority),
        delivery_contract=_freeze(contract["delivery_contract"]),
        confirmation_contract=_freeze(contract["confirmation_contract"]),
    )


def submit_driver_correction(command: DriverCorrectionCommand) -> CorrectionResult:
    """Apply an explicitly authorized proxy correction through CAFE stores."""
    return submit_authorized_correction(
        issue_dir=command.issue_dir,
        workflow_id=command.workflow_id,
        task_id=command.task_id,
        artifact=command.artifact,
        base_hash=command.base_hash,
        content=command.content,
        operation_id=command.operation_id,
        authorization_id=command.authorization_id,
        suitability=command.suitability,
        manifest=command.manifest,
        completion_payload=command.completion_payload,
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
    "DriverCorrectionCommand",
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
    "submit_driver_correction",
]
