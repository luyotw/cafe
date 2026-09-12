"""Pure suitability assessment for user-authorized Driver corrections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cafe.core.human_task_corrections import CorrectionRequest, CorrectionResult, HumanTaskCorrectionService
from cafe.core.human_task_records import HumanTaskRecordStore


@dataclass(frozen=True)
class CorrectionTakeoverAssessment:
    """A persisted-friendly assessment that never manufactures authority."""

    suitable: bool
    reason: str | None = None


def assess_correction_takeover(
    *,
    bounded: bool,
    clear: bool,
    reversible: bool,
    within_scope: bool,
    no_new_authority: bool,
) -> CorrectionTakeoverAssessment:
    """Reject at the first missing prerequisite with an explainable reason."""
    requirements = (
        ("bounded", bounded),
        ("clear", clear),
        ("reversible", reversible),
        ("within_scope", within_scope),
        ("no_new_authority", no_new_authority),
    )
    for reason, satisfied in requirements:
        if not satisfied:
            return CorrectionTakeoverAssessment(suitable=False, reason=reason)
    return CorrectionTakeoverAssessment(suitable=True)


def submit_authorized_correction(
    *,
    issue_dir: Path,
    workflow_id: str,
    task_id: str,
    artifact: str,
    base_hash: str | None,
    content: str,
    operation_id: str,
    authorization_id: str,
    suitability: Mapping[str, bool],
    manifest: tuple[dict[str, str], ...],
    completion_payload: Mapping[str, object] | None = None,
) -> CorrectionResult:
    """Submit the only Driver-proxy mutation path.

    The task is reloaded here so a Driver cannot use authorization from another
    task; the service repeats the durable validation while holding its lock.
    """
    task = HumanTaskRecordStore(issue_dir).get_task(task_id)
    if task.workflow_id != workflow_id:
        raise ValueError("Driver correction task is not in this workflow")
    assessment = assess_correction_takeover(**dict(suitability))
    if not assessment.suitable:
        raise ValueError(f"Driver correction is unsuitable: {assessment.reason}")
    return HumanTaskCorrectionService(issue_dir).apply(
        CorrectionRequest(
            workflow_id=workflow_id,
            task_id=task_id,
            artifact=artifact,
            base_hash=base_hash,
            content=content,
            operation_id=operation_id,
            actor="driver_on_behalf_of_user",
            manifest=manifest,
            completion_payload=completion_payload,
            proxy_authorization_id=authorization_id,
            suitability=dict(suitability),
        )
    )
