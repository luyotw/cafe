"""Validate dated primary-source evidence for exact provider model identities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

ASSESSMENT_MAX_AGE = timedelta(days=7)
_FLOATING_VERSIONS = {"latest", "stable", "default", "current", "preview", ""}


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def assess_model_evidence(
    record: dict[str, Any],
    *,
    now: datetime,
    current_sources: dict[str, str] | None = None,
    contradictions: list[str] | None = None,
    expected_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    diagnostics: list[str] = []
    invalidated = record.get("invalidated_sources", [])
    if not isinstance(invalidated, list) or any(
        not isinstance(url, str) or not url.strip() for url in invalidated
    ):
        diagnostics.append("source_invalidation_metadata_invalid")
    elif invalidated:
        diagnostics.append("source_fingerprint_changed")
    provider = record.get("provider")
    model = record.get("model")
    version = record.get("version")
    if not all(isinstance(value, str) and value.strip() for value in (provider, model, version)):
        diagnostics.append("exact_model_identity_missing")
    elif version.strip().lower() in _FLOATING_VERSIONS or any(
        marker in version.strip().lower() for marker in ("latest", "stable", "preview", "alias")
    ):
        diagnostics.append("floating_model_version")
    identity = {"provider": provider, "model": model, "version": version}
    if "reasoning_effort" in record:
        effort = record["reasoning_effort"]
        if (
            not isinstance(effort, str)
            or not effort.isascii()
            or not effort.isalpha()
            or effort.lower() != effort
        ):
            diagnostics.append("effort_identity_invalid")
        identity["reasoning_effort"] = effort
    if expected_identity is not None and any(
        identity.get(key) != value for key, value in expected_identity.items()
    ):
        diagnostics.append("model_identity_changed")
    assessed_at = _date(record.get("assessed_at"))
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        diagnostics.append("primary_sources_missing")
        sources = []
    source_dates: list[datetime] = []
    validities: list[datetime] = []
    for source in sources:
        if not isinstance(source, dict):
            diagnostics.append("source_record_invalid")
            continue
        url = source.get("url")
        retrieved_at = _date(source.get("retrieved_at"))
        fingerprint = source.get("fingerprint")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            diagnostics.append("primary_source_reference_invalid")
        if retrieved_at is None:
            diagnostics.append("source_retrieval_date_missing_or_invalid")
        else:
            source_dates.append(retrieved_at)
        if not isinstance(fingerprint, str) or not fingerprint:
            diagnostics.append("source_fingerprint_missing")
        if source.get("valid_until") is not None:
            validity = _date(source.get("valid_until"))
            if validity is None:
                diagnostics.append("source_validity_date_invalid")
            else:
                validities.append(validity)
        if current_sources is not None and isinstance(url, str):
            current = current_sources.get(url)
            if current is None:
                diagnostics.append("source_not_refreshed")
            elif current != fingerprint:
                diagnostics.append("source_fingerprint_changed")
    workloads = record.get("workloads")
    allowed_workloads = {
        "general",
        "requirements",
        "planning",
        "implementation",
        "review",
        "publication",
        "operations",
        "research",
        "content",
    }
    if (
        not isinstance(workloads, list)
        or not workloads
        or any(not isinstance(item, str) or item not in allowed_workloads for item in workloads)
    ):
        diagnostics.append("assessment_workload_missing")
    reasoning = record.get("reasoning")
    if reasoning not in {"routine", "standard", "high"}:
        diagnostics.append("assessment_reasoning_scope_missing")
    bands = record.get("capability_bands")
    if not isinstance(bands, dict) or not bands:
        diagnostics.append("capability_assessment_missing")
    limitations = record.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item.strip() for item in limitations)
    ):
        diagnostics.append("assessment_uncertainties_missing")
    if assessed_at is None or not source_dates:
        diagnostics.append("assessment_dates_incomplete")
        expires_at = None
    else:
        oldest = min(assessed_at, min(source_dates))
        expires_at = oldest + ASSESSMENT_MAX_AGE
        if validities:
            expires_at = min(expires_at, *validities)
        instant = now.astimezone(timezone.utc) if now.tzinfo else None
        if instant is None:
            diagnostics.append("current_time_requires_timezone")
        elif assessed_at > instant or any(date > instant for date in source_dates):
            diagnostics.append("future_assessment_or_source_date")
        elif expires_at <= instant:
            diagnostics.append("assessment_expired")
    if contradictions:
        diagnostics.append("contradictory_current_evidence")
    status = "hit" if not diagnostics else "miss"
    return {
        "status": status,
        "evidence_kind": "capability_assessment",
        "identity": identity,
        "expires_at": None if expires_at is None else expires_at.isoformat(),
        "diagnostics": diagnostics,
        "assessment": (
            {
                key: record[key]
                for key in (
                    "workloads",
                    "reasoning",
                    "capability_bands",
                    "limitations",
                    "sources",
                    "assessed_at",
                )
            }
            if status == "hit"
            else None
        ),
    }


def reassess_completed_phases(completed, *, prior_assessments, started_phases, recommendations):
    """Plan ordered recommendations at an existing Manager observation boundary.

    Callers append these records to their existing Manager output. This pure
    operation neither writes configuration nor grants adjustment authority.
    Completion identity comes from durable phase/iteration evidence.
    """

    def identity(row):
        return row["phase"], row["iteration"], row["completed_at"]

    observed = {identity(row) for row in prior_assessments}
    rows = []
    eligible = {
        name: value for name, value in recommendations.items() if name not in started_phases
    }
    for recommendation in eligible.values():
        if recommendation.get("band") not in {"efficiency", "balanced", "frontier"}:
            raise ValueError("reassessment requires a capability band")
        if not recommendation.get("model_rationale") or not recommendation.get("effort_rationale"):
            raise ValueError("reassessment requires model and effort rationale")
        chain = recommendation.get("chain")
        if not chain or any(not entry.get("reasoning_effort") for entry in chain):
            raise ValueError("reassessment requires complete explicit tuples")
    for completion in sorted(
        completed, key=lambda row: (row["completed_at"], row["phase"], row["iteration"])
    ):
        key = identity(completion)
        if key in observed:
            continue
        observed.add(key)
        rows.append(
            {
                **{field: completion[field] for field in ("phase", "iteration", "completed_at")},
                "outcome": "recommendation_only" if eligible else "no_adjustment",
                "eligible_future_phases": list(eligible),
                "recommendations": eligible,
                "authorized_mutation": False,
                "model_rationale": "Assess remaining workload, phase minimum and durable uncertainty evidence.",
                "effort_rationale": "Reassess each exact provider tuple; no eligible future phase requires an explicit no-adjustment record.",
            }
        )
    return rows
