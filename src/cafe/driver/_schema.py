"""Strict, package-private schema and semantic projection for Driver contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from typing import Any, Mapping

from cafe.core.packet_io import canonical_json
from cafe.core.types import AgentCLI


SCHEMA_VERSION = 1
_RUNTIME_KEYS = {
    "session",
    "sessions",
    "session_id",
    "dispatch",
    "dispatch_state",
    "active_cli",
    "active_index",
    "locks",
    "lock",
    "baton",
    "history",
    "pr_url",
}
_PROPOSAL_KEYS = {
    "locales",
    "confirmation_contract",
    "reactive_user_handoffs",
    "mandate",
    "issue_assessment",
    "phases",
    "proactive_review",
    "model_adjustment",
    "driver",
    "checkout",
    "semantic_facts",
    "material_assumptions",
}
_CONTRACT_KEYS = _PROPOSAL_KEYS - {"semantic_facts", "material_assumptions"} | {
    "schema_version",
    "identity",
    "revision",
    "provenance",
    "preflight",
}
_POLICY_SEMANTIC_FIELDS = (
    "locales",
    "confirmation_contract",
    "reactive_user_handoffs",
    "mandate",
    "issue_assessment",
    "phases",
    "proactive_review",
    "model_adjustment",
    "driver",
    "checkout",
)


def _mapping(value: Any, label: str, *, keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    result = dict(value)
    if keys is not None and set(result) != keys:
        raise ValueError(f"{label} has unsupported or missing fields")
    return result


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a Boolean")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings")
    result = [item.strip() for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    try:
        canonical_json(result)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain canonical JSON values") from exc
    return deepcopy(result)


def _aware_time(value: Any, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def _validate_locales(value: Any) -> dict[str, Any]:
    result = _mapping(value, "locales", keys={"conversation"})
    locale = _mapping(result["conversation"], "locales.conversation", keys={"value", "source"})
    locale["value"] = _string(locale["value"], "locales.conversation.value")
    locale["source"] = _string(locale["source"], "locales.conversation.source")
    result["conversation"] = locale
    return result


def _validate_confirmation(value: Any) -> dict[str, Any]:
    required = {"user_required", "driver_confirmable", "mandatory_human_stops"}
    result = _mapping(value, "confirmation_contract", keys=required)
    for name in required:
        result[name] = _string_list(result[name], f"confirmation_contract.{name}")
    if set(result["user_required"]) & set(result["driver_confirmable"]):
        raise ValueError("confirmation ownership must be disjoint")
    if set(result["mandatory_human_stops"]) & set(result["driver_confirmable"]):
        raise ValueError("mandatory human stops cannot be driver-confirmable")
    return result


def _validate_phases(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("phases must be a list")
    phases: list[dict[str, Any]] = []
    names: set[str] = set()
    expected = {"name", "chain", "rationale"}
    for index, raw in enumerate(value):
        phase = _mapping(raw, f"phases[{index}]", keys=expected)
        for field in ("name", "rationale"):
            phase[field] = _string(phase[field], f"phases[{index}].{field}")
        if phase["name"] in names:
            raise ValueError("phases must have distinct names")
        names.add(phase["name"])
        if not isinstance(phase["chain"], list):
            raise ValueError("phase chain must be a list")
        chain: list[dict[str, str]] = []
        seen_clis: set[str] = set()
        for chain_index, raw_entry in enumerate(phase["chain"]):
            entry = _mapping(
                raw_entry, f"phases[{index}].chain[{chain_index}]", keys={"cli", "model"}
            )
            cli = _string(entry["cli"], "phase chain cli")
            try:
                cli = AgentCLI(cli).value
            except ValueError as exc:
                raise ValueError("phase chain CLI is unsupported") from exc
            entry = {"cli": cli, "model": _string(entry["model"], "phase chain model")}
            if entry["cli"] in seen_clis:
                raise ValueError("phase chain CLIs must be distinct")
            seen_clis.add(entry["cli"])
            chain.append(entry)
        if not chain:
            raise ValueError("Driver phases require an ordered CLI/model chain")
        phase["chain"] = chain
        phases.append(phase)
    return phases


def _validate_proactive(value: Any, phases: list[dict[str, Any]]) -> dict[str, Any]:
    result = _mapping(value, "proactive_review", keys={"phase_decisions"})
    raw_decisions = result["phase_decisions"]
    if not isinstance(raw_decisions, list):
        raise ValueError("proactive_review.phase_decisions must be a list")
    agent_phases = [phase["name"] for phase in phases]
    decisions: list[dict[str, str]] = []
    for index, raw in enumerate(raw_decisions):
        decision = _mapping(
            raw,
            f"proactive_review.phase_decisions[{index}]",
            keys={"phase", "decision", "rationale"},
        )
        phase = _string(decision["phase"], "proactive review phase")
        state = _string(decision["decision"], "proactive review decision")
        if state not in {"required", "not_required"}:
            raise ValueError("proactive review decision is invalid")
        decisions.append(
            {
                "phase": phase,
                "decision": state,
                "rationale": _string(decision["rationale"], "proactive review rationale"),
            }
        )
    if [item["phase"] for item in decisions] != agent_phases:
        raise ValueError("proactive review decisions must cover Driver phases in order")
    result["phase_decisions"] = decisions
    return result


def _validate_driver(value: Any) -> dict[str, Any]:
    result = _mapping(value, "driver")
    if _RUNTIME_KEYS & set(result):
        raise ValueError("driver runtime state does not belong in the confirmed contract")
    mode = _string(result.get("mode"), "driver.mode")
    if mode == "attached":
        if set(result) != {"mode", "poll_interval_seconds"}:
            raise ValueError("attached driver has invalid fields")
        seconds = result["poll_interval_seconds"]
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError("attached driver requires a positive poll interval")
    elif mode == "unattended":
        if set(result) != {"mode"}:
            raise ValueError("unattended driver has invalid fields")
    elif mode == "event-driven":
        if (
            set(result) != {"mode", "clis"}
            or not isinstance(result["clis"], list)
            or not result["clis"]
        ):
            raise ValueError("event-driven driver requires an ordered CLI/model chain")
        clis: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in result["clis"]:
            entry = _mapping(raw, "event-driven CLI", keys={"cli", "model"})
            cli = _string(entry["cli"], "event-driven CLI")
            try:
                cli = AgentCLI(cli).value
            except ValueError as exc:
                raise ValueError("event-driven CLI is unsupported") from exc
            model = _string(entry["model"], "event-driven model")
            if cli in seen:
                raise ValueError("event-driven CLIs must be distinct")
            seen.add(cli)
            clis.append({"cli": cli, "model": model})
        result["clis"] = clis
    else:
        raise ValueError("driver.mode is invalid")
    return result


def _validate_checkout(value: Any) -> dict[str, Any]:
    result = _mapping(value, "checkout")
    kind = _string(result.get("kind"), "checkout.kind")
    if kind == "current_checkout" and set(result) == {"kind"}:
        return result
    if kind == "worktree" and set(result) == {"kind", "path"}:
        result["path"] = _string(result["path"], "checkout.path")
        return result
    raise ValueError("checkout must be current_checkout or a named worktree")


def _validate_policy(proposal: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(proposal, "confirmed proposal", keys=set(proposal))
    if _RUNTIME_KEYS & set(raw):
        raise ValueError("mutable runtime state does not belong in the confirmed contract")
    if set(raw) - _PROPOSAL_KEYS:
        raise ValueError("confirmed proposal has unknown authority fields")
    if set(raw) != _PROPOSAL_KEYS:
        raise ValueError("confirmed proposal is incomplete")
    phases = _validate_phases(raw["phases"])
    adjustment = _json_mapping(raw["model_adjustment"], "model_adjustment")
    adjustment_keys = set(adjustment)
    if adjustment_keys not in ({"authority"}, {"authority", "confirmed_by", "confirmed_at"}):
        raise ValueError("model_adjustment has unsupported or missing fields")
    result: dict[str, Any] = {
        "locales": _validate_locales(raw["locales"]),
        "confirmation_contract": _validate_confirmation(raw["confirmation_contract"]),
        "reactive_user_handoffs": _mapping(
            raw["reactive_user_handoffs"],
            "reactive_user_handoffs",
            keys={"need_clarification", "need_permission", "alignment_checkpoint"},
        ),
        "mandate": _json_mapping(raw["mandate"], "mandate"),
        "issue_assessment": _mapping(
            raw["issue_assessment"],
            "issue_assessment",
            keys={"nature", "scale", "risks", "rationale"},
        ),
        "phases": phases,
        "proactive_review": _validate_proactive(raw["proactive_review"], phases),
        "model_adjustment": adjustment,
        "driver": _validate_driver(raw["driver"]),
        "checkout": _validate_checkout(raw["checkout"]),
    }
    for field in ("need_clarification", "need_permission", "alignment_checkpoint"):
        result["reactive_user_handoffs"][field] = _string(
            result["reactive_user_handoffs"][field], f"reactive_user_handoffs.{field}"
        )
    assessment = result["issue_assessment"]
    assessment["nature"] = _string(assessment["nature"], "issue_assessment.nature")
    assessment["scale"] = _string(assessment["scale"], "issue_assessment.scale")
    assessment["risks"] = _string_list(assessment["risks"], "issue_assessment.risks")
    assessment["rationale"] = _string(assessment["rationale"], "issue_assessment.rationale")
    adjustment = result["model_adjustment"]
    if adjustment["authority"] not in {"driver_autonomous", "user_approval_required"}:
        raise ValueError("model adjustment authority is invalid")
    if "confirmed_by" in adjustment:
        adjustment["confirmed_by"] = _string(
            adjustment["confirmed_by"], "model_adjustment.confirmed_by"
        )
        adjustment["confirmed_at"] = _aware_time(
            adjustment["confirmed_at"], "model_adjustment.confirmed_at"
        )
    expected_semantics = {
        "effective_policy": {
            name: deepcopy(result[name]) for name in _POLICY_SEMANTIC_FIELDS if name in result
        }
    }
    supplied_semantics = _json_mapping(raw["semantic_facts"], "semantic_facts")
    if supplied_semantics != expected_semantics:
        raise ValueError("semantic_facts must exactly represent the complete effective policy")
    result["preflight"] = {
        "semantic_facts": expected_semantics,
        "material_assumptions": _json_mapping(raw["material_assumptions"], "material_assumptions"),
    }
    return result


def _semantic_projection_from_validated(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Project a policy already validated by the caller or proposal builder."""
    fields = (
        "identity",
        "locales",
        "confirmation_contract",
        "reactive_user_handoffs",
        "mandate",
        "issue_assessment",
        "phases",
        "proactive_review",
        "model_adjustment",
        "driver",
        "checkout",
    )
    projection = {name: deepcopy(contract[name]) for name in fields if name in contract}
    projection["material_assumptions"] = deepcopy(contract["preflight"]["material_assumptions"])
    return projection


def semantic_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return only confirmed behavior, excluding diagnostics and revision metadata."""
    return _semantic_projection_from_validated(validate_contract(contract))


def proposal_digest(contract: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_semantic_projection_from_validated(contract))).hexdigest()


def build_initial_contract(
    *,
    proposal: Mapping[str, Any],
    issue_name: str,
    workflow_id: str,
    confirmed_by: str,
    confirmed_at: str,
    revision: int = 1,
    previous_contract_sha256: str | None = None,
    provenance_kind: str = "initial",
    delegated_change: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a purpose-specific proposal into a strict durable document."""
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise ValueError("revision must be a positive integer")
    if provenance_kind not in {"initial", "user_reconfirmation", "delegated_change"}:
        raise ValueError("provenance kind is invalid")
    if provenance_kind == "delegated_change":
        if delegated_change is None:
            raise ValueError("delegated change provenance is required")
    elif delegated_change is not None:
        raise ValueError("only delegated changes may include delegated provenance")
    policy = _validate_policy(proposal)
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "issue_name": _string(issue_name, "identity.issue_name"),
            "workflow_id": _string(workflow_id, "identity.workflow_id"),
        },
        "revision": {"generation": revision, "previous_contract_sha256": previous_contract_sha256},
        "provenance": {
            "kind": provenance_kind,
            "confirmed_by": _string(confirmed_by, "provenance.confirmed_by"),
            "confirmed_at": _aware_time(confirmed_at, "provenance.confirmed_at"),
            "proposal_digest": "",
        },
        **policy,
    }
    if delegated_change is not None:
        document["provenance"]["delegated_change"] = _json_mapping(
            delegated_change, "delegated_change"
        )
    document["provenance"]["proposal_digest"] = proposal_digest(document)
    return validate_contract(document)


def validate_contract(
    document: Mapping[str, Any], *, issue_name: str | None = None, workflow_id: str | None = None
) -> dict[str, Any]:
    raw = _mapping(document, "contract")
    if set(raw) != _CONTRACT_KEYS:
        raise ValueError("contract has unsupported or missing fields")
    schema_version = raw["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        raise ValueError("contract schema version is unsupported")
    identity = _mapping(raw["identity"], "identity", keys={"issue_name", "workflow_id"})
    identity["issue_name"] = _string(identity["issue_name"], "identity.issue_name")
    identity["workflow_id"] = _string(identity["workflow_id"], "identity.workflow_id")
    if issue_name is not None and identity["issue_name"] != issue_name:
        raise ValueError("contract belongs to a different issue")
    if workflow_id is not None and identity["workflow_id"] != workflow_id:
        raise ValueError("contract belongs to a different workflow")
    revision = _mapping(
        raw["revision"], "revision", keys={"generation", "previous_contract_sha256"}
    )
    if (
        not isinstance(revision["generation"], int)
        or isinstance(revision["generation"], bool)
        or revision["generation"] <= 0
    ):
        raise ValueError("contract generation is invalid")
    previous = revision["previous_contract_sha256"]
    if previous is not None and (not isinstance(previous, str) or len(previous) != 64):
        raise ValueError("previous contract digest is invalid")
    provenance_keys = {"kind", "confirmed_by", "confirmed_at", "proposal_digest"}
    provenance = _mapping(raw["provenance"], "provenance")
    if provenance.get("kind") == "delegated_change":
        provenance_keys.add("delegated_change")
    if set(provenance) != provenance_keys:
        raise ValueError("contract provenance is invalid")
    kind = _string(provenance["kind"], "provenance.kind")
    if kind not in {"initial", "user_reconfirmation", "delegated_change"}:
        raise ValueError("contract provenance kind is invalid")
    provenance["confirmed_by"] = _string(provenance["confirmed_by"], "provenance.confirmed_by")
    provenance["confirmed_at"] = _aware_time(provenance["confirmed_at"], "provenance.confirmed_at")
    digest = _string(provenance["proposal_digest"], "provenance.proposal_digest")
    if len(digest) != 64:
        raise ValueError("contract proposal digest is invalid")
    proposal = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in {"schema_version", "identity", "revision", "provenance"}
    }
    proposal["semantic_facts"] = (
        proposal.pop("preflight")["semantic_facts"]
        if isinstance(proposal.get("preflight"), Mapping)
        else None
    )
    proposal["material_assumptions"] = (
        raw["preflight"].get("material_assumptions")
        if isinstance(raw["preflight"], Mapping)
        else None
    )
    policy = _validate_policy(proposal)
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "identity": identity,
        "revision": revision,
        "provenance": provenance,
        **policy,
    }
    if kind == "delegated_change":
        normalized["provenance"]["delegated_change"] = _json_mapping(
            provenance["delegated_change"], "delegated_change"
        )
    expected = hashlib.sha256(
        canonical_json(_semantic_projection_from_validated(normalized))
    ).hexdigest()
    if digest != expected:
        raise ValueError("contract proposal digest does not match its policy")
    return normalized
