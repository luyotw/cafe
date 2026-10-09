"""Read-only Manager decision for one current, declared HumanTask."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from cafe.core.human_tasks import (
    HumanTaskPolicy,
    HumanTaskQuestion,
    HumanTaskRejection,
    validate_human_task_completion,
)
from cafe.core.packet_io import canonical_json

from ._schema import validate_contract
from .delivery_comparison import decide as decide_delivery_comparison
from .delivery_comparison import obligations


def _facts(route: str, owner: str, reason: str, *, allowed: bool = False) -> dict[str, Any]:
    return {
        "route_status": route,
        "resolution_owner": owner,
        "evidence_reason": reason,
        "allowed": allowed,
    }


def _answer_values(response: Mapping[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    answers = response.get("answers")
    if isinstance(answers, Mapping):
        for key, value in answers.items():
            for item in value if isinstance(value, list) else [value]:
                result.append((f"answers.{key}", str(item)))
    for key in ("decision", "feedback", "target"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            result.append((key, value.strip()))
    return result


def _exact_coverage(
    evidence: Mapping[str, Any],
    sources: Mapping[str, str],
) -> set[tuple[str, str]] | None:
    citations = evidence.get("citations", [])
    if not isinstance(citations, list):
        return None
    covered: set[tuple[str, str]] = set()
    for raw in citations:
        if not isinstance(raw, Mapping):
            return None
        field, value, source, excerpt = (
            raw.get("field"),
            raw.get("value"),
            raw.get("source"),
            raw.get("excerpt"),
        )
        if not all(isinstance(item, str) and item for item in (field, value, source, excerpt)):
            return None
        if source.startswith(("repo:", "current_output:")):
            return None
        text = sources.get(source)
        if not isinstance(text, str) or excerpt not in text:
            return None
        covered.add((field, value))
    return covered


def _technical_coverage(
    evidence: Mapping[str, Any],
    sources: Mapping[str, str],
    multi_fields: set[str],
) -> set[tuple[str, str]] | None:
    authority = evidence.get("authority")
    if not isinstance(authority, Mapping):
        return None
    source, excerpt = authority.get("source"), authority.get("excerpt")
    if not isinstance(source, str) or not isinstance(excerpt, str) or not excerpt:
        return None
    if source.startswith("current_output:"):
        return None
    if excerpt not in sources.get(source, ""):
        return None
    candidates = evidence.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    rankings: dict[str, dict[str, tuple[int, int]]] = {}
    for item in candidates:
        if not isinstance(item, Mapping):
            return None
        field, value = item.get("field"), item.get("value")
        precedent, footprint, reversible = (
            item.get("precedent"),
            item.get("footprint"),
            item.get("reversible"),
        )
        if (
            not isinstance(field, str)
            or not isinstance(value, str)
            or not isinstance(precedent, bool)
            or not isinstance(reversible, bool)
            or not isinstance(footprint, int)
            or isinstance(footprint, bool)
            or footprint < 0
            or not reversible
        ):
            return None
        if precedent:
            precedent_evidence = item.get("precedent_evidence")
            if not isinstance(precedent_evidence, Mapping):
                return None
            precedent_source = precedent_evidence.get("source")
            precedent_excerpt = precedent_evidence.get("excerpt")
            if (
                not isinstance(precedent_source, str)
                or not precedent_source.startswith("repo:")
                or not isinstance(precedent_excerpt, str)
                or not precedent_excerpt
                or precedent_excerpt not in sources.get(precedent_source, "")
            ):
                return None
        group = rankings.setdefault(field, {})
        if value in group:
            return None
        group[value] = (0 if precedent else 1, footprint)
    covered: set[tuple[str, str]] = set()
    for field, group in rankings.items():
        best = min(group.values())
        winners = {value for value, rank in group.items() if rank == best}
        if len(winners) != 1 and field not in multi_fields:
            return None
        covered.update((field, value) for value in winners)
    return covered


def _supported_confirmation(
    task: Mapping[str, Any],
    contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
    sources: Mapping[str, str],
    trusted_comparison: Mapping[str, Any] | None,
) -> bool:
    comparison = evidence.get("delivery_comparison")
    if not isinstance(comparison, Mapping) or set(comparison) != {"snapshot_sha256", "assessment"}:
        return False
    packet, assessment = trusted_comparison, comparison.get("assessment")
    if not isinstance(packet, dict) or not isinstance(assessment, dict):
        return False
    if comparison.get("snapshot_sha256") != packet.get("snapshot_sha256"):
        return False
    data = packet.get("data")
    if not isinstance(data, dict):
        return False
    provenance = task["provenance"]
    boundary = data.get("boundary")
    expected_boundary = {
        "step": provenance["step"],
        "task_id": task["id"],
        "iteration": provenance["iteration"],
        "intent": "confirm_output",
        "owner": "user",
        "active": True,
    }
    if (
        boundary != expected_boundary
        or data.get("identity") != {"issue_name": task["issue"], "workflow_id": task["workflow_id"]}
        or data.get("contract_sha256") != hashlib.sha256(canonical_json(contract)).hexdigest()
        or data.get("delivery_contract") != contract["delivery_contract"]
        or data.get("obligations") != obligations(contract["delivery_contract"])
    ):
        return False
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return False
    if not all(
        isinstance(name, str)
        and isinstance(text, str)
        and text in {sources.get(f"artifact:{name}"), sources.get(f"current_output:{name}")}
        for name, text in artifacts.items()
    ):
        return False
    current_names = {
        name for name in artifacts if sources.get(f"current_output:{name}") == artifacts[name]
    }
    if not current_names:
        return False
    coverage = assessment.get("coverage")
    if not isinstance(coverage, dict) or not any(
        isinstance(record, dict) and record.get("source") in current_names
        for record in coverage.values()
    ):
        return False
    try:
        return decide_delivery_comparison(packet, assessment).get("decision") == "accept"
    except (KeyError, TypeError, ValueError):
        return False


def decide_task_authority(
    *,
    task: Mapping[str, Any],
    contract: Mapping[str, Any],
    current_task_id: str,
    response: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    confirmed_sources: Mapping[str, str] | None = None,
    questions: Sequence[HumanTaskQuestion] | None = None,
    trusted_comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide only from current task identity, confirmed policy and checked evidence.

    The caller obtains task detail through TaskInboxService.inspect. Semantic
    assessment remains Manager-owned; source excerpts must match confirmed input.
    """
    provenance = task.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    route = str(provenance.get("trigger") or "unknown")
    owner = "user_required"
    try:
        policy = validate_contract(contract, allow_legacy_upgrade=True)
    except (ValueError, TypeError):
        return _facts(route, owner, "invalid_contract")
    phase, task_name = provenance.get("step"), provenance.get("policy_id")
    if (
        task.get("issue") != policy["identity"]["issue_name"]
        or task.get("workflow_id") != policy["identity"]["workflow_id"]
        or not isinstance(phase, str)
        or not isinstance(task_name, str)
    ):
        return _facts(route, owner, "contract_identity_mismatch")
    confirmation = policy["confirmation_contract"]
    if route == "confirm_output" and phase in confirmation["mandatory_human_stops"]:
        return _facts(route, owner, "mandatory_human_stop")
    if route == "confirm_output" and phase in confirmation["user_required"]:
        return _facts(route, owner, "confirmation_user_required")
    if route == "need_permission" or task.get("capability_approval") is not None:
        return _facts(route, owner, "permission_or_capability")
    if policy.get("contract_mode") == "compact":
        # Compact contracts deliberately omit task_contract: unresolved tasks
        # remain user-owned and cannot acquire Manager authority from evidence.
        if task.get("id") != current_task_id or not current_task_id:
            return _facts(route, owner, "stale_task_identity")
        return _facts(route, owner, "compact_user_required")
    version = policy["schema_version"]
    key = {"phase": phase, "task_id": task_name}
    overall_clarification = (
        route == "need_clarification"
        and policy["reactive_user_handoffs"].get("need_clarification") == "manager_confirmable"
    )
    if version == 8:
        declared = policy["task_contract"]
        if key in declared["user_required"]:
            return _facts(route, owner, "declared_user_required")
        if key not in declared["manager_confirmable"] and not overall_clarification:
            return _facts(route, owner, "task_ownership_undeclared")
    elif version == 5:
        if not overall_clarification and (
            route != "confirm_output"
            or phase not in confirmation["manager_confirmable"]
            or phase in confirmation["user_required"]
        ):
            return _facts(route, owner, "legacy_task_authority_unconfirmed")
    else:
        return _facts(route, owner, "legacy_task_authority_unconfirmed")
    owner = "manager_confirmable"
    if task.get("id") != current_task_id or not current_task_id:
        return _facts(route, owner, "stale_task_identity")
    wait = task.get("wait")
    if (
        task.get("status") != "pending"
        or task.get("result") is not None
        or not isinstance(wait, Mapping)
        or wait.get("released_at") is not None
    ):
        return _facts(route, owner, "task_not_pending")
    if response is None or evidence is None:
        return _facts(route, owner, "evidence_unevaluated")
    if not isinstance(response, Mapping) or not isinstance(evidence, Mapping):
        return _facts(route, owner, "invalid_response_or_evidence")
    expected = task.get("expected_result")
    try:
        task_policy = HumanTaskPolicy.model_validate(expected)
    except (ValueError, TypeError):
        return _facts(route, owner, "invalid_task_schema")
    if (
        task_policy.id != task_name
        or response.get("human_task_id") != current_task_id
        or response.get("task") != task_name
    ):
        return _facts(route, owner, "stale_task_identity")
    response_keys = {
        "answers": {"answers"},
        "feedback": {"feedback"},
        "target": {"target"},
        "decision": {"decision", "feedback", "target"},
    }[task_policy.input_schema] | {"task", "human_task_id", "work_report"}
    if not set(response) <= response_keys:
        return _facts(route, owner, "invalid_response_shape")
    if task_policy.input_schema == "answers":
        answers = response.get("answers")
        declared = questions if task_policy.questions_from_xml else task_policy.questions
        if (
            declared is None
            or not isinstance(answers, Mapping)
            or set(answers) != {question.id for question in declared}
        ):
            return _facts(route, owner, "incomplete_response")
    completion = validate_human_task_completion(task_policy, response, questions=questions)
    if isinstance(completion, HumanTaskRejection):
        return _facts(route, owner, "invalid_or_incomplete_response")
    if evidence.get("exhaustive") is not True or evidence.get("material_alternatives") is True:
        return _facts(route, owner, "ambiguous_or_unverified_evidence")
    if evidence.get("category") not in (None, "confirmed", "technical"):
        return _facts(route, owner, "user_owned_decision_category")
    values = _answer_values(response)
    basis = evidence.get("basis")
    sources = confirmed_sources or {}
    if route == "confirm_output" and response.get("decision") == "confirm":
        if basis == "confirmed_exact" and _supported_confirmation(
            task, policy, evidence, sources, trusted_comparison
        ):
            return _facts(route, owner, "grounded_confirmation", allowed=True)
        return _facts(route, owner, "unsupported_or_ambiguous_response")
    exact = _exact_coverage(evidence, sources)
    if basis == "confirmed_exact" and exact is not None and bool(values) and set(values) == exact:
        return _facts(route, owner, "confirmed_exact_evidence", allowed=True)
    multi_fields = set()
    if task_policy.input_schema == "answers":
        multi_fields = {f"answers.{question.id}" for question in declared if question.multiple}
    if (
        basis == "reversible_technical"
        and evidence.get("category") == "technical"
        and exact is not None
    ):
        technical = _technical_coverage(evidence, sources, multi_fields)
        if (
            technical is not None
            and bool(values)
            and not exact & technical
            and set(values) == exact | technical
        ):
            return _facts(route, owner, "authorized_reversible_technical_choice", allowed=True)
    return _facts(route, owner, "unsupported_or_ambiguous_response")
