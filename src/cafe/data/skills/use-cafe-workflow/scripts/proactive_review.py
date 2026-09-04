#!/usr/bin/env python3
"""Issue-local proactive phase-review contracts and current review evidence.

The workflow driver owns the semantic judgement that selects a smallest useful
review set.  This module intentionally validates only the durable contract's
structure, current playbook binding, and atomic persistence boundaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from cafe.core.types import AgentCLI
from cafe.playbooks.loader import PlaybookLoader


CONTRACT_FILENAME = "contract.yaml"
STATE_FILENAME = "state.yaml"
SCHEMA_VERSION = 1
SELECTION_FACTORS = frozenset(
    {
        "ambiguity",
        "novelty",
        "blast_radius",
        "protected_risk",
        "durable_contract",
        "downstream_review",
        "late_correction",
        "cost",
    }
)
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "issue_name",
        "playbook_id",
        "proposal_digest",
        "confirmed_by",
        "confirmed_at",
        "policy",
    }
)


class ContractNotFoundError(ValueError):
    """Raised only when no confirmed proactive-review contract exists."""


class StaleContractError(ValueError):
    """A confirmed contract no longer matches its live issue/playbook context."""


def contract_path(issue_dir: Path) -> Path:
    """Return the only durable active-contract path for an issue."""
    return issue_dir / "driver" / "proactive_review" / CONTRACT_FILENAME


def state_path(issue_dir: Path) -> Path:
    """Return the bounded, current-evidence snapshot path for an issue."""
    return issue_dir / "driver" / "proactive_review" / STATE_FILENAME


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return dict(value)


def _non_empty(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def policy_digest(policy: Mapping[str, Any]) -> str:
    """Hash canonical rendered-policy content, excluding confirmation metadata."""
    return hashlib.sha256(_canonical_json(policy).encode("utf-8")).hexdigest()


def _band_or_estimate(value: Any, *, label: str) -> Any:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an estimate or bounded band")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError(f"{label} estimate must be positive")
        return value
    if isinstance(value, str):
        return _non_empty(value, label=label)
    item = _mapping(value, label=label)
    if set(item) == {"estimate"}:
        return {"estimate": _band_or_estimate(item["estimate"], label=f"{label}.estimate")}
    if set(item) != {"band"}:
        raise ValueError(f"{label} requires exactly one estimate or band")
    band = _mapping(item["band"], label=f"{label}.band")
    if set(band) != {"minimum", "maximum", "unit"}:
        raise ValueError(f"{label}.band requires minimum, maximum, and unit")
    minimum, maximum = band["minimum"], band["maximum"]
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, (int, float))
        or not isinstance(maximum, (int, float))
        or minimum <= 0
        or maximum < minimum
    ):
        raise ValueError(f"{label}.band must be a positive bounded range")
    return {"band": {"minimum": minimum, "maximum": maximum, "unit": _non_empty(band["unit"], label=f"{label}.band.unit")}}


def _review_cost(value: Any, *, label: str) -> dict[str, Any]:
    item = _mapping(value, label=label)
    required = {"tokens", "latency", "assumptions", "delay_impact"}
    if set(item) != required:
        raise ValueError(f"{label} requires tokens, latency, assumptions, and delay_impact")
    return {
        "tokens": _band_or_estimate(item["tokens"], label=f"{label}.tokens"),
        "latency": _band_or_estimate(item["latency"], label=f"{label}.latency"),
        "assumptions": _non_empty(item["assumptions"], label=f"{label}.assumptions"),
        "delay_impact": _non_empty(item["delay_impact"], label=f"{label}.delay_impact"),
    }


def _rereview_cost(value: Any) -> dict[str, Any]:
    item = _mapping(value, label="rereview_cost")
    foreseeable = item.get("foreseeable")
    if not isinstance(foreseeable, bool):
        raise ValueError("rereview_cost.foreseeable must be a boolean")
    if foreseeable:
        if set(item) != {"foreseeable", "tokens", "latency", "assumptions", "delay_impact"}:
            raise ValueError("foreseeable rereview cost requires a complete cost disclosure")
        return {"foreseeable": True, **_review_cost({key: item[key] for key in item if key != "foreseeable"}, label="rereview_cost")}
    if set(item) != {"foreseeable", "reason"}:
        raise ValueError("unforeseeable rereview cost requires only a reason")
    return {"foreseeable": False, "reason": _non_empty(item["reason"], label="rereview_cost.reason")}


def _agent_phase_names(playbook: Any) -> tuple[str, ...]:
    return tuple(
        name
        for name, step in playbook.steps.items()
        if step.assignee_type in {"agent", "hybrid"}
    )


def _enforceable_boundary(playbook: Any, phase: str) -> bool:
    """Whether the playbook exposes an existing post-output pause boundary.

    A driver must not manufacture a worker gate.  Existing confirmation or
    explicit manual-handoff outcomes are the only boundaries this helper treats
    as enforceable before the next automated phase.
    """
    step = playbook.steps[phase]
    return step.output_artifact is not None and (
        "confirm_output" in step.on or "manual_handoff" in step.on
    )


def validate_policy(policy: Any, *, playbook: Any) -> dict[str, Any]:
    """Validate a phase-complete policy without evaluating rationale quality."""
    raw = _mapping(policy, label="proactive review policy")
    if set(raw) != {"playbook_id", "phases"}:
        raise ValueError("proactive review policy requires playbook_id and phases")
    playbook_id = _non_empty(raw["playbook_id"], label="policy.playbook_id")
    if playbook_id != playbook.playbook.id:
        raise StaleContractError("policy playbook_id does not match the live playbook")
    entries = raw["phases"]
    if not isinstance(entries, list):
        raise ValueError("policy.phases must be a list")
    expected = _agent_phase_names(playbook)
    if len(entries) != len(expected):
        raise ValueError("policy must cover every agent-executed phase exactly once")

    normalized: list[dict[str, Any]] = []
    found: list[str] = []
    for index, value in enumerate(entries):
        entry = _mapping(value, label=f"policy.phases[{index}]")
        phase = _non_empty(entry.get("phase"), label=f"policy.phases[{index}].phase")
        selected = entry.get("selected")
        if not isinstance(selected, bool):
            raise ValueError(f"policy.phases[{index}].selected must be a boolean")
        rationale = _non_empty(entry.get("rationale"), label=f"policy.phases[{index}].rationale")
        factors = _mapping(entry.get("factors"), label=f"policy.phases[{index}].factors")
        if set(factors) != SELECTION_FACTORS:
            raise ValueError(f"policy.phases[{index}].factors must cover the selection policy")
        normalized_factors = {
            name: _non_empty(factors[name], label=f"policy.phases[{index}].factors.{name}")
            for name in sorted(SELECTION_FACTORS)
        }
        common = {"phase", "selected", "rationale", "factors"}
        if not selected:
            if set(entry) != common:
                raise ValueError("excluded phase cannot carry reviewer, ordering, or cost fields")
            normalized.append(
                {"phase": phase, "selected": False, "rationale": rationale, "factors": normalized_factors}
            )
            found.append(phase)
            continue
        required = common | {"reviewer", "ordering", "initial_review_cost", "rereview_cost"}
        if set(entry) != required:
            raise ValueError("selected phase requires exact reviewer, ordering, and cost disclosures")
        reviewer = _mapping(entry["reviewer"], label=f"policy.phases[{index}].reviewer")
        if set(reviewer) != {"cli", "model"}:
            raise ValueError("selected reviewer requires exactly cli and model")
        try:
            cli = AgentCLI(_non_empty(reviewer["cli"], label="reviewer.cli")).value
        except ValueError as exc:
            raise ValueError("selected reviewer CLI is unsupported") from exc
        ordering = entry["ordering"]
        if ordering not in {"before_next_phase", "non_gating"}:
            raise ValueError("selected review ordering is invalid")
        if ordering == "before_next_phase" and not _enforceable_boundary(playbook, phase):
            raise ValueError("before_next_phase lacks an existing enforceable graph boundary")
        normalized.append(
            {
                "phase": phase,
                "selected": True,
                "rationale": rationale,
                "factors": normalized_factors,
                "reviewer": {"cli": cli, "model": _non_empty(reviewer["model"], label="reviewer.model")},
                "ordering": ordering,
                "initial_review_cost": _review_cost(entry["initial_review_cost"], label="initial_review_cost"),
                "rereview_cost": _rereview_cost(entry["rereview_cost"]),
            }
        )
        found.append(phase)
    if tuple(found) != expected:
        raise ValueError("policy phase inventory must match the live playbook order exactly")
    return {"playbook_id": playbook_id, "phases": normalized}


def _read_issue_playbook_id(issue_dir: Path) -> str:
    issue_file = issue_dir / "issue.yaml"
    if not issue_file.is_file():
        raise StaleContractError("issue is not prepared with an issue.yaml playbook binding")
    try:
        value = yaml.safe_load(issue_file.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StaleContractError("issue.yaml is unreadable") from exc
    item = _mapping(value, label="issue.yaml")
    return _non_empty(item.get("playbook_id"), label="issue.yaml.playbook_id")


def _live_playbook(*, issue_dir: Path, project_root: Path, playbook_id: str) -> Any:
    if _read_issue_playbook_id(issue_dir) != playbook_id:
        raise StaleContractError("issue.yaml playbook_id differs from proactive review contract")
    try:
        return PlaybookLoader(project_root=project_root).load_model(playbook_id).model
    except (FileNotFoundError, LookupError, ValueError) as exc:
        raise StaleContractError("current effective playbook cannot validate proactive review contract") from exc


def _validate_confirmation(
    confirmation: Any, *, issue_dir: Path, policy: Mapping[str, Any]
) -> dict[str, Any]:
    item = _mapping(confirmation, label="confirmation")
    if set(item) != {"schema_version", "issue_name", "playbook_id", "confirmed_by", "confirmed_at"}:
        raise ValueError("confirmation requires only schema_version, issue_name, playbook_id, confirmed_by, and confirmed_at")
    if item["schema_version"] != SCHEMA_VERSION:
        raise ValueError("confirmation schema_version is invalid")
    if item["issue_name"] != issue_dir.name or not issue_dir.name:
        raise StaleContractError("confirmation issue_name must match the prepared issue directory")
    if item["playbook_id"] != policy["playbook_id"]:
        raise StaleContractError("confirmation playbook_id must match the policy")
    if item["confirmed_by"] != "user":
        raise ValueError("confirmed_by must be the literal user")
    confirmed_at = _non_empty(item["confirmed_at"], label="confirmed_at")
    try:
        parsed = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("confirmed_at must be a parseable RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("confirmed_at must include a timezone")
    return {
        "schema_version": SCHEMA_VERSION,
        "issue_name": issue_dir.name,
        "playbook_id": policy["playbook_id"],
        "confirmed_by": "user",
        "confirmed_at": confirmed_at,
    }


def _atomic_yaml_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(dict(value), handle, allow_unicode=True, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activate_contract(
    *,
    issue_dir: Path,
    project_root: Path,
    policy: Any,
    confirmation: Any,
    expected_active_digest: str | None = None,
) -> Path:
    """Atomically activate an initial or fully reconfirmed review policy."""
    if not issue_dir.is_dir():
        raise ValueError("proactive review activation requires an already prepared issue directory")
    playbook_id = _read_issue_playbook_id(issue_dir)
    live = _live_playbook(issue_dir=issue_dir, project_root=project_root, playbook_id=playbook_id)
    validated_policy = validate_policy(policy, playbook=live)
    validated_confirmation = _validate_confirmation(
        confirmation, issue_dir=issue_dir, policy=validated_policy
    )
    digest = policy_digest(validated_policy)
    target = contract_path(issue_dir)
    if target.exists():
        existing = load_active_contract(issue_dir=issue_dir, project_root=project_root)
        if not expected_active_digest or expected_active_digest != existing["proposal_digest"]:
            raise StaleContractError("replacement requires the expected active proposal digest")
    elif expected_active_digest is not None:
        raise StaleContractError("initial activation cannot compare an absent active contract")
    envelope = {
        **validated_confirmation,
        "proposal_digest": digest,
        "policy": validated_policy,
    }
    _atomic_yaml_write(target, envelope)
    return target


def load_active_contract(*, issue_dir: Path, project_root: Path) -> dict[str, Any]:
    """Load the active contract through the shared live identity/playbook check."""
    target = contract_path(issue_dir)
    if not target.is_file() or target.is_symlink():
        raise ContractNotFoundError("no active proactive review contract")
    try:
        value = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StaleContractError("active proactive review contract is unreadable") from exc
    envelope = _mapping(value, label="active proactive review contract")
    if set(envelope) != _ENVELOPE_FIELDS:
        raise StaleContractError("active proactive review contract has an invalid envelope")
    policy = envelope["policy"]
    confirmation = {key: envelope[key] for key in _ENVELOPE_FIELDS - {"proposal_digest", "policy"}}
    validated_confirmation = _validate_confirmation(confirmation, issue_dir=issue_dir, policy=_mapping(policy, label="policy"))
    playbook_id = _read_issue_playbook_id(issue_dir)
    if envelope["playbook_id"] != playbook_id:
        raise StaleContractError("active contract playbook no longer matches issue.yaml")
    live = _live_playbook(issue_dir=issue_dir, project_root=project_root, playbook_id=playbook_id)
    validated_policy = validate_policy(policy, playbook=live)
    digest = policy_digest(validated_policy)
    if envelope["proposal_digest"] != digest:
        raise StaleContractError("active contract proposal digest is stale or invalid")
    return {**validated_confirmation, "proposal_digest": digest, "policy": validated_policy}
