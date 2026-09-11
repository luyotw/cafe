"""Pure suitability assessment for user-authorized Driver corrections."""

from __future__ import annotations

from dataclasses import dataclass


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
