#!/usr/bin/env python3
"""Issue-local proactive phase-review contracts and current review evidence.

The workflow driver owns the semantic judgement that selects a smallest useful
review set.  This module intentionally validates only the durable contract's
structure, current playbook binding, and atomic persistence boundaries.
"""

from __future__ import annotations

import argparse
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


class ReviewStateError(ValueError):
    """Current review evidence is incomplete or cannot support acceptance."""


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
    return {
        "band": {
            "minimum": minimum,
            "maximum": maximum,
            "unit": _non_empty(band["unit"], label=f"{label}.band.unit"),
        }
    }


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
        return {
            "foreseeable": True,
            **_review_cost(
                {key: item[key] for key in item if key != "foreseeable"}, label="rereview_cost"
            ),
        }
    if set(item) != {"foreseeable", "reason"}:
        raise ValueError("unforeseeable rereview cost requires only a reason")
    return {
        "foreseeable": False,
        "reason": _non_empty(item["reason"], label="rereview_cost.reason"),
    }


def _agent_phase_names(playbook: Any) -> tuple[str, ...]:
    return tuple(
        name for name, step in playbook.steps.items() if step.assignee_type in {"agent", "hybrid"}
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
                {
                    "phase": phase,
                    "selected": False,
                    "rationale": rationale,
                    "factors": normalized_factors,
                }
            )
            found.append(phase)
            continue
        required = common | {"reviewer", "ordering", "initial_review_cost", "rereview_cost"}
        if set(entry) != required:
            raise ValueError(
                "selected phase requires exact reviewer, ordering, and cost disclosures"
            )
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
                "reviewer": {
                    "cli": cli,
                    "model": _non_empty(reviewer["model"], label="reviewer.model"),
                },
                "ordering": ordering,
                "initial_review_cost": _review_cost(
                    entry["initial_review_cost"], label="initial_review_cost"
                ),
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
    try:
        item = _mapping(value, label="issue.yaml")
        return _non_empty(item.get("playbook_id"), label="issue.yaml.playbook_id")
    except ValueError as exc:
        raise StaleContractError("issue.yaml has no valid playbook binding") from exc


def _live_playbook(*, issue_dir: Path, project_root: Path, playbook_id: str) -> Any:
    if _read_issue_playbook_id(issue_dir) != playbook_id:
        raise StaleContractError("issue.yaml playbook_id differs from proactive review contract")
    try:
        return PlaybookLoader(project_root=project_root).load_model(playbook_id).model
    except (FileNotFoundError, LookupError, ValueError) as exc:
        raise StaleContractError(
            "current effective playbook cannot validate proactive review contract"
        ) from exc


def _validate_confirmation(
    confirmation: Any, *, issue_dir: Path, policy: Mapping[str, Any]
) -> dict[str, Any]:
    item = _mapping(confirmation, label="confirmation")
    required = {
        "schema_version",
        "issue_name",
        "playbook_id",
        "proposal_digest",
        "confirmed_by",
        "confirmed_at",
    }
    if set(item) != required:
        raise ValueError(
            "confirmation requires exact schema, issue, playbook, policy digest, "
            "actor, and timestamp"
        )
    if item["schema_version"] != SCHEMA_VERSION:
        raise ValueError("confirmation schema_version is invalid")
    if item["issue_name"] != issue_dir.name or not issue_dir.name:
        raise StaleContractError("confirmation issue_name must match the prepared issue directory")
    if item["playbook_id"] != policy["playbook_id"]:
        raise StaleContractError("confirmation playbook_id must match the policy")
    digest = _non_empty(item["proposal_digest"], label="proposal_digest")
    if digest != policy_digest(policy):
        raise StaleContractError(
            "confirmation proposal_digest must match the exact rendered policy"
        )
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
        "proposal_digest": digest,
        "confirmed_by": "user",
        "confirmed_at": confirmed_at,
    }


def _atomic_yaml_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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
    digest = validated_confirmation["proposal_digest"]
    target = contract_path(issue_dir)
    if target.exists():
        existing = load_active_contract(issue_dir=issue_dir, project_root=project_root)
        if not expected_active_digest or expected_active_digest != existing["proposal_digest"]:
            raise StaleContractError("replacement requires the expected active proposal digest")
    elif expected_active_digest is not None:
        raise StaleContractError("initial activation cannot compare an absent active contract")
    envelope = {
        **validated_confirmation,
        "policy": validated_policy,
    }
    _atomic_yaml_write(target, envelope)
    existing_state = state_path(issue_dir)
    if existing_state.is_file() and not existing_state.is_symlink():
        _atomic_yaml_write(existing_state, _empty_review_state(digest))
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
    try:
        policy = envelope["policy"]
        confirmation = {key: envelope[key] for key in _ENVELOPE_FIELDS - {"policy"}}
        validated_confirmation = _validate_confirmation(
            confirmation, issue_dir=issue_dir, policy=_mapping(policy, label="policy")
        )
        playbook_id = _read_issue_playbook_id(issue_dir)
        if envelope["playbook_id"] != playbook_id:
            raise StaleContractError("active contract playbook no longer matches issue.yaml")
        live = _live_playbook(
            issue_dir=issue_dir, project_root=project_root, playbook_id=playbook_id
        )
        validated_policy = validate_policy(policy, playbook=live)
    except StaleContractError:
        raise
    except ValueError as exc:
        raise StaleContractError("active proactive review contract is invalid") from exc
    return {**validated_confirmation, "policy": validated_policy}


def _empty_review_state(proposal_digest: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "proposal_digest": proposal_digest, "episodes": {}}


def _output_identity(output_path: Path) -> dict[str, str]:
    if not output_path.is_file() or output_path.is_symlink():
        raise ReviewStateError("review requires one complete durable output file")
    try:
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReviewStateError("review output cannot be read") from exc
    return {"path": str(output_path.resolve()), "sha256": digest}


def _evidence_items(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ReviewStateError(f"{label} requires bounded current evidence")
    for item in value:
        if isinstance(item, str) and not item.strip():
            raise ReviewStateError(f"{label} cannot contain empty evidence")
    return list(value)


def _selected_phase(contract: Mapping[str, Any], phase: str) -> dict[str, Any]:
    name = _non_empty(phase, label="phase")
    for entry in contract["policy"]["phases"]:
        if entry["phase"] == name:
            if not entry["selected"]:
                raise ReviewStateError("phase is excluded from proactive review")
            return dict(entry)
    raise ReviewStateError("phase is not covered by the active proactive review contract")


def _load_state_for_contract(*, issue_dir: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    target = state_path(issue_dir)
    if not target.exists():
        return _empty_review_state(contract["proposal_digest"])
    if not target.is_file() or target.is_symlink():
        raise ReviewStateError("proactive review state is not a regular file")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReviewStateError("proactive review state is unreadable") from exc
    state = _mapping(raw, label="proactive review state")
    if set(state) != {"schema_version", "proposal_digest", "episodes"}:
        raise ReviewStateError("proactive review state has an invalid envelope")
    if state["schema_version"] != SCHEMA_VERSION:
        raise ReviewStateError("proactive review state schema is invalid")
    if state["proposal_digest"] != contract["proposal_digest"]:
        return _empty_review_state(contract["proposal_digest"])
    episodes = _mapping(state["episodes"], label="proactive review state episodes")
    selected = {entry["phase"] for entry in contract["policy"]["phases"] if entry["selected"]}
    if any(phase not in selected for phase in episodes):
        return _empty_review_state(contract["proposal_digest"])
    return {
        "schema_version": SCHEMA_VERSION,
        "proposal_digest": contract["proposal_digest"],
        "episodes": {
            phase: _mapping(episode, label=f"review episode {phase}")
            for phase, episode in episodes.items()
        },
    }


def _write_review_state(issue_dir: Path, state: Mapping[str, Any]) -> None:
    _atomic_yaml_write(state_path(issue_dir), state)


def load_review_state(*, issue_dir: Path, project_root: Path) -> dict[str, Any]:
    """Load bounded current evidence only after live contract validation."""
    contract = load_active_contract(issue_dir=issue_dir, project_root=project_root)
    return _load_state_for_contract(issue_dir=issue_dir, contract=contract)


def prepare_review_inputs(
    *,
    issue_dir: Path,
    project_root: Path,
    phase: str,
    output_path: Path,
    requirements: list[Any],
    upstream_artifacts: list[Any],
    repository_evidence: list[Any],
    correction_history: list[Any],
) -> dict[str, Any]:
    """Record a pending current-output obligation and return its complete inputs.

    The driver supplies only bounded, phase-relevant evidence.  This helper
    rejects a missing/stale contract or incomplete input instead of allowing a
    caller to treat the review as empty or clean.
    """
    contract = load_active_contract(issue_dir=issue_dir, project_root=project_root)
    selected = _selected_phase(contract, phase)
    identity = _output_identity(output_path)
    required = _evidence_items(requirements, label="confirmed requirements")
    upstream = _evidence_items(upstream_artifacts, label="accepted upstream artifacts")
    evidence = _evidence_items(repository_evidence, label="repository evidence")
    if not isinstance(correction_history, list):
        raise ReviewStateError("correction history must be a list")

    state = _load_state_for_contract(issue_dir=issue_dir, contract=contract)
    previous = state["episodes"].get(selected["phase"])
    if previous is not None and previous.get("output_identity") == identity:
        episode = previous
    else:
        was_blocking = isinstance(previous, Mapping) and previous.get("status") in {
            "blocking",
            "downstream_invalidated",
            "user_stop",
        }
        prior_blockers = previous.get("blockers", []) if isinstance(previous, Mapping) else []
        correction_statuses = {
            item.get("id"): item.get("status")
            for item in correction_history
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        preceding = [
            {
                **dict(blocker),
                "status": correction_statuses.get(
                    blocker.get("id"), blocker.get("status", "still_failing")
                ),
            }
            for blocker in prior_blockers
            if isinstance(blocker, Mapping)
        ]
        episode = {
            "status": "corrected_awaiting_rereview" if was_blocking else "pending",
            "proposal_digest": contract["proposal_digest"],
            "output_identity": identity,
            "reviewer": selected["reviewer"],
            "correction_history": list(correction_history),
            "preceding_blocker_statuses": preceding,
        }
        state["episodes"][selected["phase"]] = episode
        _write_review_state(issue_dir, state)

    return {
        "contract": contract,
        "phase": selected["phase"],
        "reviewer": selected["reviewer"],
        "ordering": selected["ordering"],
        "output_identity": identity,
        "complete_output": output_path.read_text(encoding="utf-8"),
        "requirements": required,
        "upstream_artifacts": upstream,
        "repository_evidence": evidence,
        "correction_history": list(correction_history),
    }


def _reviewer_identity(value: Any) -> dict[str, str]:
    item = _mapping(value, label="reviewer")
    if set(item) != {"cli", "model"}:
        raise ReviewStateError("reviewer identity must contain exact cli and model")
    try:
        cli = AgentCLI(_non_empty(item["cli"], label="reviewer.cli")).value
    except ValueError as exc:
        raise ReviewStateError("reviewer CLI is unsupported") from exc
    return {"cli": cli, "model": _non_empty(item["model"], label="reviewer.model")}


def _scope_adequacy(value: Any) -> dict[str, Any]:
    item = _mapping(value, label="scope adequacy")
    if set(item) != {"missing", "excess", "proportionality"}:
        raise ReviewStateError(
            "complete review must assess missing scope, excess scope, and proportionality"
        )
    if not isinstance(item["missing"], list) or not isinstance(item["excess"], list):
        raise ReviewStateError("scope adequacy findings must be collections")
    return {
        "missing": list(item["missing"]),
        "excess": list(item["excess"]),
        "proportionality": _non_empty(item["proportionality"], label="scope proportionality"),
    }


def _blockers(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ReviewStateError("review blockers must be a list")
    required = {"id", "evidence", "violated_constraint", "expected_outcome", "focused_verification"}
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        blocker = _mapping(raw, label=f"review blocker {index}")
        if set(blocker) != required:
            raise ReviewStateError(
                "each blocker requires grounded evidence and focused verification"
            )
        normalized = {
            name: _non_empty(blocker[name], label=f"review blocker {name}") for name in required
        }
        if normalized["id"] in seen:
            raise ReviewStateError("review blocker identities must be unique")
        seen.add(normalized["id"])
        result.append(normalized)
    return result


def _authorized_route(*, correction_route: Any, authorized_routes: Any) -> dict[str, str] | None:
    if correction_route is None:
        return None
    route = _mapping(correction_route, label="correction route")
    if set(route) != {"to_owner", "to_step", "intent"}:
        raise ReviewStateError("correction route must use the current handoff contract shape")
    if route.get("to_step") == "proactive_review":
        raise ReviewStateError("proactive review reports cannot be correction targets")
    if not isinstance(authorized_routes, list):
        raise ReviewStateError("authorized correction routes must be a list")
    normalized = {key: _non_empty(route[key], label=f"correction route.{key}") for key in route}
    for candidate in authorized_routes:
        if isinstance(candidate, Mapping) and dict(candidate) == normalized:
            return normalized
    raise ReviewStateError("correction route is not authorized by the current workflow handoff")


def _pending_episode(episode: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {**episode, "status": "pending", "pending_reason": reason}


def record_review_result(
    *,
    issue_dir: Path,
    project_root: Path,
    phase: str,
    output_identity: Mapping[str, Any],
    reviewer: Mapping[str, Any],
    result: Any,
    authorized_routes: list[Mapping[str, Any]],
    correction_route: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a complete independent result to the one current phase episode.

    Partial execution, stale output, and reviewer mismatch preserve a pending
    obligation.  Only a complete result from the confirmed reviewer for the
    current output can compact the episode to clean evidence.
    """
    contract = load_active_contract(issue_dir=issue_dir, project_root=project_root)
    selected = _selected_phase(contract, phase)
    state = _load_state_for_contract(issue_dir=issue_dir, contract=contract)
    episode = state["episodes"].get(selected["phase"])
    if not isinstance(episode, Mapping):
        raise ReviewStateError("review input preparation is required before recording a result")
    identity = _mapping(output_identity, label="review output identity")
    if identity != episode.get("output_identity"):
        pending = _pending_episode(episode, reason="output_identity_mismatch")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    try:
        current_identity = _output_identity(Path(str(identity.get("path", ""))))
    except ReviewStateError:
        current_identity = None
    if current_identity != identity:
        pending = _pending_episode(episode, reason="output_identity_stale")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    try:
        actual_reviewer = _reviewer_identity(reviewer)
    except ReviewStateError:
        pending = _pending_episode(episode, reason="reviewer_unavailable")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    if actual_reviewer != selected["reviewer"]:
        pending = _pending_episode(episode, reason="reviewer_mismatch")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    if not isinstance(result, Mapping) or result.get("complete") is not True:
        pending = _pending_episode(episode, reason="incomplete_or_failed_execution")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    if set(result) != {"complete", "scope_adequacy", "blockers"}:
        pending = _pending_episode(episode, reason="incomplete_result")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    try:
        scope = _scope_adequacy(result["scope_adequacy"])
        blockers = _blockers(result["blockers"])
    except ReviewStateError:
        pending = _pending_episode(episode, reason="incomplete_result")
        state["episodes"][selected["phase"]] = pending
        _write_review_state(issue_dir, state)
        return pending
    if not blockers:
        prior = episode.get("preceding_blocker_statuses", episode.get("blockers", []))
        resolved_summary = {
            "count": len(prior) if isinstance(prior, list) else 0,
            "digest": policy_digest({"resolved": prior if isinstance(prior, list) else []}),
        }
        clean = {
            "status": "clean",
            "proposal_digest": contract["proposal_digest"],
            "output_identity": dict(identity),
            "reviewer": actual_reviewer,
            "scope_adequacy": scope,
            "resolved_summary": resolved_summary,
        }
        state["episodes"][selected["phase"]] = clean
        _write_review_state(issue_dir, state)
        return clean

    route = _authorized_route(
        correction_route=correction_route, authorized_routes=authorized_routes
    )
    current = [dict(blocker, status="still_failing") for blocker in blockers]
    blocked = {
        "status": "blocking" if route is not None else "user_stop",
        "proposal_digest": contract["proposal_digest"],
        "output_identity": dict(identity),
        "reviewer": actual_reviewer,
        "scope_adequacy": scope,
        "blockers": current,
        "route": route,
    }
    state["episodes"][selected["phase"]] = blocked
    _write_review_state(issue_dir, state)
    return blocked


def mark_downstream_invalidated(
    *,
    issue_dir: Path,
    project_root: Path,
    phase: str,
    affected_downstream: list[Any],
) -> dict[str, Any]:
    """Record that downstream work needs its existing owner to revalidate it."""
    contract = load_active_contract(issue_dir=issue_dir, project_root=project_root)
    selected = _selected_phase(contract, phase)
    state = _load_state_for_contract(issue_dir=issue_dir, contract=contract)
    episode = state["episodes"].get(selected["phase"])
    if not isinstance(episode, Mapping) or episode.get("status") not in {"blocking", "user_stop"}:
        raise ReviewStateError("only a current blocking episode can invalidate downstream work")
    downstream = _evidence_items(affected_downstream, label="affected downstream work")
    invalidated = {**episode, "status": "downstream_invalidated", "affected_downstream": downstream}
    state["episodes"][selected["phase"]] = invalidated
    _write_review_state(issue_dir, state)
    return invalidated


def review_obligations(*, issue_dir: Path, project_root: Path) -> list[dict[str, Any]]:
    """Return bounded current obligations for driver resume/callback reconciliation."""
    contract = load_active_contract(issue_dir=issue_dir, project_root=project_root)
    state = _load_state_for_contract(issue_dir=issue_dir, contract=contract)
    obligations: list[dict[str, Any]] = []
    for entry in contract["policy"]["phases"]:
        if not entry["selected"]:
            continue
        episode = state["episodes"].get(entry["phase"])
        obligations.append(
            {
                "phase": entry["phase"],
                "ordering": entry["ordering"],
                "reviewer": entry["reviewer"],
                "status": episode.get("status", "not_started")
                if isinstance(episode, Mapping)
                else "not_started",
            }
        )
    return obligations


def _json_mapping(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    """Activate a policy only after the driver has prepared and confirmed it."""
    parser = argparse.ArgumentParser(
        description="Activate a confirmed issue-local proactive review policy."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--issue-dir", required=True, type=Path)
    activate.add_argument("--project-root", type=Path, default=Path.cwd())
    activate.add_argument("--policy-json", type=_json_mapping, required=True)
    activate.add_argument("--confirmation-json", type=_json_mapping, required=True)
    activate.add_argument("--expected-active-digest")
    args = parser.parse_args(argv)

    if args.command == "activate":
        target = activate_contract(
            issue_dir=args.issue_dir,
            project_root=args.project_root,
            policy=args.policy_json,
            confirmation=args.confirmation_json,
            expected_active_digest=args.expected_active_digest,
        )
        print(json.dumps({"contract_path": str(target)}, sort_keys=True))
        return 0
    raise AssertionError(
        "argparse returned an unknown proactive review command"
    )  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover - exercised through the public command.
    raise SystemExit(main())
