"""Read-only Driver decision for one current, declared HumanTask."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from cafe.core.human_tasks import (
    HumanTaskPolicy,
    HumanTaskQuestion,
    HumanTaskRejection,
    validate_human_task_completion,
)

from ._schema import validate_contract


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


def _supported_exact(
    answer_values: Sequence[tuple[str, str]],
    evidence: Mapping[str, Any],
    sources: Mapping[str, str],
) -> bool:
    citations = evidence.get("citations")
    if not isinstance(citations, list):
        return False
    covered: set[tuple[str, str]] = set()
    for raw in citations:
        if not isinstance(raw, Mapping):
            return False
        field, value, source, excerpt = (
            raw.get("field"),
            raw.get("value"),
            raw.get("source"),
            raw.get("excerpt"),
        )
        if not all(isinstance(item, str) and item for item in (field, value, source, excerpt)):
            return False
        text = sources.get(source)
        if not isinstance(text, str) or excerpt not in text or value not in excerpt:
            return False
        covered.add((field, value))
    return bool(answer_values) and set(answer_values) == covered


def _supported_technical(
    answer_values: Sequence[tuple[str, str]],
    evidence: Mapping[str, Any],
    sources: Mapping[str, str],
) -> bool:
    authority = evidence.get("authority")
    if not isinstance(authority, Mapping):
        return False
    source, excerpt = authority.get("source"), authority.get("excerpt")
    if not isinstance(source, str) or not isinstance(excerpt, str) or not excerpt:
        return False
    if excerpt not in sources.get(source, ""):
        return False
    candidates = evidence.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return False
    rankings: dict[tuple[str, str], tuple[int, int, int]] = {}
    for item in candidates:
        if not isinstance(item, Mapping):
            return False
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
            return False
        if precedent:
            precedent_evidence = item.get("precedent_evidence")
            if not isinstance(precedent_evidence, Mapping):
                return False
            precedent_source = precedent_evidence.get("source")
            precedent_excerpt = precedent_evidence.get("excerpt")
            if (
                not isinstance(precedent_source, str)
                or not precedent_source.startswith("repo:")
                or not isinstance(precedent_excerpt, str)
                or not precedent_excerpt
                or precedent_excerpt not in sources.get(precedent_source, "")
            ):
                return False
        rankings[(field, value)] = (0 if precedent else 1, footprint, 0 if reversible else 1)
    if not rankings or len(rankings) != len(candidates):
        return False
    best = min(rankings.values())
    winners = {key for key, rank in rankings.items() if rank == best}
    return len(winners) == 1 and set(answer_values) == winners


def decide_task_authority(
    *,
    task: Mapping[str, Any],
    contract: Mapping[str, Any],
    current_task_id: str,
    response: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    confirmed_sources: Mapping[str, str] | None = None,
    questions: Sequence[HumanTaskQuestion] | None = None,
) -> dict[str, Any]:
    """Decide only from current task identity, confirmed policy and checked evidence.

    The caller obtains task detail through TaskInboxService.inspect. Semantic
    assessment remains Driver-owned; source excerpts must match confirmed input.
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
    version = policy["schema_version"]
    key = {"phase": phase, "task_id": task_name}
    if version == 6:
        declared = policy["task_contract"]
        if key in declared["user_required"]:
            return _facts(route, owner, "declared_user_required")
        if key not in declared["driver_confirmable"]:
            return _facts(route, owner, "task_ownership_undeclared")
    elif version == 5:
        # A route-specific legacy clarification value never grants authority.
        if (
            route != "confirm_output"
            or phase not in confirmation["driver_confirmable"]
            or phase in confirmation["user_required"]
        ):
            return _facts(route, owner, "legacy_task_authority_unconfirmed")
    else:
        return _facts(route, owner, "legacy_task_authority_unconfirmed")
    owner = "driver_confirmable"
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
    if basis == "confirmed_exact" and _supported_exact(values, evidence, sources):
        return _facts(route, owner, "confirmed_exact_evidence", allowed=True)
    if (
        basis == "reversible_technical"
        and evidence.get("category") == "technical"
        and _supported_technical(values, evidence, sources)
    ):
        return _facts(route, owner, "authorized_reversible_technical_choice", allowed=True)
    return _facts(route, owner, "unsupported_or_ambiguous_response")
