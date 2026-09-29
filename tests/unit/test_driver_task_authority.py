"""Task-level Driver authority is independent of the workflow pause route."""

from copy import deepcopy

import pytest

from cafe.driver._schema import (
    build_driver_settings_update,
    build_initial_contract,
    freshness_semantic_facts,
    validate_contract,
)
from cafe.driver.task_authority import decide_task_authority
from tests.unit.test_driver_contract_application import _proposal


def _task_proposal():
    proposal = deepcopy(_proposal())
    proposal["task_contract"] = {
        "user_required": [{"phase": "develop", "task_id": "choose-release"}],
        "driver_confirmable": [{"phase": "develop", "task_id": "known-answer"}],
    }
    proposal["reactive_user_handoffs"].pop("need_clarification")
    return proposal


def test_new_contract_declares_custom_task_owner_without_clarification_route_field():
    contract = build_initial_contract(
        proposal=_task_proposal(),
        issue_name="issue500",
        workflow_id="workflow500",
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    assert contract["schema_version"] > 5
    assert "need_clarification" not in contract["reactive_user_handoffs"]
    assert contract["task_contract"]["driver_confirmable"] == [
        {"phase": "develop", "task_id": "known-answer"}
    ]
    assert validate_contract(contract) == contract


def test_driver_settings_update_preserves_confirmed_task_ownership():
    contract = _contract()
    updated = build_driver_settings_update(
        contract,
        {"mode": "attached", "poll_interval_seconds": 30},
        previous_contract_sha256="a" * 64,
    )
    assert updated["schema_version"] == 6
    assert updated["task_contract"] == contract["task_contract"]
    assert (
        freshness_semantic_facts(updated)["effective_policy"]["task_contract"]
        == contract["task_contract"]
    )


@pytest.mark.parametrize("change", ["duplicate", "overlap", "invalid_overall"])
def test_new_contract_rejects_ambiguous_task_ownership(change):
    proposal = _task_proposal()
    if change == "duplicate":
        proposal["task_contract"]["driver_confirmable"].append(
            {"phase": "develop", "task_id": "known-answer"}
        )
    elif change == "overlap":
        proposal["task_contract"]["user_required"].append(
            {"phase": "develop", "task_id": "known-answer"}
        )
    else:
        proposal["reactive_user_handoffs"]["need_clarification"] = "unknown"
    with pytest.raises(ValueError):
        build_initial_contract(
            proposal=proposal,
            issue_name="issue500",
            workflow_id="workflow500",
            confirmed_by="user",
            confirmed_at="2026-09-26T12:00:00+00:00",
        )


def test_legacy_explicit_clarification_policy_retains_ownership_and_requires_evidence():
    proposal = _proposal()
    proposal["reactive_user_handoffs"]["need_clarification"] = "driver_confirmable"
    proposal["confirmation_contract"]["driver_confirmable"] = ["develop"]
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id="workflow500",
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    assert contract["schema_version"] == 5
    result = decide_task_authority(
        task=_task(),
        contract=contract,
        current_task_id="durable-1",
    )
    assert result["resolution_owner"] == "driver_confirmable"
    assert result["evidence_reason"] == "evidence_unevaluated"
    assert result["allowed"] is False
    assert result["route_status"] == "need_clarification"


def _task(schema="answers", *, trigger="need_clarification", task_id="known-answer"):
    pattern = {
        "answers": "answer_questions",
        "feedback": "revision_feedback",
        "target": "select_next_step",
        "decision": "confirm_output",
    }[schema]
    policy = {
        "id": task_id,
        "pattern": pattern,
        "prompt": "Provide the established response",
        "input_schema": schema,
        "required": True,
        "questions": (
            [{"id": "q1", "prompt": "Which outcomes?", "options": ["A", "B"], "multiple": True}]
            if schema == "answers"
            else []
        ),
    }
    if schema == "target":
        policy["allowed_targets"] = ["develop", "review"]
    if schema == "decision":
        policy["decisions"] = [{"id": "confirm", "label": "Confirm"}]
    return {
        "id": "durable-1",
        "issue": "issue500",
        "workflow_id": "workflow500",
        "provenance": {"step": "develop", "iteration": 1, "trigger": trigger, "policy_id": task_id},
        "expected_result": policy,
        "status": "pending",
        "wait": {"released_at": None},
        "result": None,
        "capability_approval": None,
    }


def _contract():
    return build_initial_contract(
        proposal=_task_proposal(),
        issue_name="issue500",
        workflow_id="workflow500",
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )


def test_complete_confirmed_multiselect_answer_is_driver_confirmable():
    result = decide_task_authority(
        task=_task(),
        contract=_contract(),
        current_task_id="durable-1",
        response={
            "task": "known-answer",
            "human_task_id": "durable-1",
            "answers": {"q1": ["A", "B"]},
        },
        evidence={
            "basis": "confirmed_exact",
            "exhaustive": True,
            "citations": [
                {"field": "answers.q1", "value": value, "source": "contract", "excerpt": "A and B"}
                for value in ("A", "B")
            ],
        },
        confirmed_sources={"contract": "All required outcomes: A and B"},
    )
    assert result["resolution_owner"] == "driver_confirmable"
    assert result["allowed"] is True
    assert result["route_status"] == "need_clarification"


@pytest.mark.parametrize(
    "change", ["missing", "stale", "mandatory", "permission", "capability", "unsupported"]
)
def test_incomplete_or_user_owned_response_fails_closed(change):
    task, contract = _task(), _contract()
    response = {"task": "known-answer", "human_task_id": "durable-1", "answers": {"q1": ["A", "B"]}}
    if change == "missing":
        response["answers"] = {}
    elif change == "stale":
        task["id"] = "durable-2"
    elif change == "mandatory":
        contract["confirmation_contract"]["mandatory_human_stops"].append("develop")
        task["provenance"]["trigger"] = "confirm_output"
    elif change == "permission":
        task["provenance"]["trigger"] = "need_permission"
    elif change == "capability":
        task["capability_approval"] = {"state": "pending"}
    else:
        response["answers"] = {"q1": ["A"]}
    result = decide_task_authority(
        task=task,
        contract=contract,
        current_task_id="durable-1",
        response=response,
        evidence={"basis": "confirmed_exact", "exhaustive": True, "citations": []},
        confirmed_sources={"contract": "All required outcomes: A and B"},
    )
    assert result["allowed"] is False


def test_reversible_technical_tie_breakers_choose_one_supported_option():
    task = _task("feedback")
    response = {"task": "known-answer", "human_task_id": "durable-1", "feedback": "small change"}
    result = decide_task_authority(
        task=task,
        contract=_contract(),
        current_task_id="durable-1",
        response=response,
        evidence={
            "basis": "reversible_technical",
            "category": "technical",
            "exhaustive": True,
            "authority": {"source": "contract", "excerpt": "reversible technical choice"},
            "candidates": [
                {
                    "field": "feedback",
                    "value": "small change",
                    "precedent": True,
                    "precedent_evidence": {"source": "repo:pattern.py", "excerpt": "small change"},
                    "footprint": 1,
                    "reversible": True,
                },
                {
                    "field": "feedback",
                    "value": "larger change",
                    "precedent": False,
                    "footprint": 4,
                    "reversible": True,
                },
            ],
        },
        confirmed_sources={
            "contract": "User authorized reversible technical choice",
            "repo:pattern.py": "Existing small change",
        },
    )
    assert result["allowed"] is True
    assert result["evidence_reason"] == "authorized_reversible_technical_choice"


def test_declared_target_with_exact_confirmed_evidence_is_complete():
    result = decide_task_authority(
        task=_task("target"),
        contract=_contract(),
        current_task_id="durable-1",
        response={"task": "known-answer", "human_task_id": "durable-1", "target": "review"},
        evidence={
            "basis": "confirmed_exact",
            "exhaustive": True,
            "citations": [
                {
                    "field": "target",
                    "value": "review",
                    "source": "contract",
                    "excerpt": "Continue at review",
                }
            ],
        },
        confirmed_sources={"contract": "Continue at review"},
    )
    assert result["allowed"] is True


def test_equal_rank_technical_choices_remain_ambiguous():
    result = decide_task_authority(
        task=_task("feedback"),
        contract=_contract(),
        current_task_id="durable-1",
        response={"task": "known-answer", "human_task_id": "durable-1", "feedback": "A"},
        evidence={
            "basis": "reversible_technical",
            "category": "technical",
            "exhaustive": True,
            "authority": {"source": "contract", "excerpt": "Technical choice allowed"},
            "candidates": [
                {
                    "field": "feedback",
                    "value": value,
                    "precedent": True,
                    "precedent_evidence": {"source": "repo:pattern.py", "excerpt": "pattern"},
                    "footprint": 1,
                    "reversible": True,
                }
                for value in ("A", "B")
            ],
        },
        confirmed_sources={"contract": "Technical choice allowed", "repo:pattern.py": "pattern"},
    )
    assert result["allowed"] is False


def test_mandatory_confirmation_overrides_task_declaration():
    proposal = _task_proposal()
    proposal["confirmation_contract"]["mandatory_human_stops"].append("develop")
    contract = build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id="workflow500",
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )
    result = decide_task_authority(
        task=_task("decision", trigger="confirm_output"),
        contract=contract,
        current_task_id="durable-1",
    )
    assert result["resolution_owner"] == "user_required"
    assert result["evidence_reason"] == "mandatory_human_stop"


@pytest.mark.parametrize(
    "category", ["preference", "strategy", "scope", "permission", "capability", "uncertain"]
)
def test_unconfirmed_decision_categories_remain_user_owned(category):
    result = decide_task_authority(
        task=_task("feedback"),
        contract=_contract(),
        current_task_id="durable-1",
        response={"task": "known-answer", "human_task_id": "durable-1", "feedback": "A"},
        evidence={
            "basis": "confirmed_exact",
            "category": category,
            "exhaustive": True,
            "citations": [
                {"field": "feedback", "value": "A", "source": "contract", "excerpt": "A"}
            ],
        },
        confirmed_sources={"contract": "A"},
    )
    assert result["allowed"] is False
    assert result["evidence_reason"] == "user_owned_decision_category"


def _overall_contract(owner="driver_confirmable", override=None, *, mandatory=False):
    proposal = _task_proposal()
    proposal["reactive_user_handoffs"]["need_clarification"] = owner
    proposal["task_contract"] = {"user_required": [], "driver_confirmable": []}
    if mandatory:
        proposal["confirmation_contract"]["mandatory_human_stops"].append("develop")
    if override:
        proposal["task_contract"][override] = [{"phase": "develop", "task_id": "known-answer"}]
    return build_initial_contract(
        proposal=proposal,
        issue_name="issue500",
        workflow_id="workflow500",
        confirmed_by="user",
        confirmed_at="2026-09-26T12:00:00+00:00",
    )


@pytest.mark.parametrize(
    "overall,override,expected",
    [
        ("driver_confirmable", None, "driver_confirmable"),
        ("user_required", None, "user_required"),
        ("driver_confirmable", "user_required", "user_required"),
        ("user_required", "driver_confirmable", "driver_confirmable"),
    ],
)
def test_overall_clarification_policy_and_explicit_override_precedence(overall, override, expected):
    contract = _overall_contract(overall, override)
    assert contract["schema_version"] == 7
    result = decide_task_authority(
        task=_task(),
        contract=contract,
        current_task_id="durable-1",
        response={
            "task": "known-answer",
            "human_task_id": "durable-1",
            "answers": {"q1": ["A", "B"]},
        },
        evidence={
            "basis": "confirmed_exact",
            "exhaustive": True,
            "citations": [
                {"field": "answers.q1", "value": value, "source": "contract", "excerpt": "A and B"}
                for value in ("A", "B")
            ],
        },
        confirmed_sources={"contract": "All required outcomes: A and B"},
    )
    assert result["resolution_owner"] == expected
    assert result["allowed"] is (expected == "driver_confirmable")


@pytest.mark.parametrize(
    "kind", ["permission", "capability", "mandatory", "other_route", "unsupported", "scope"]
)
def test_overall_clarification_policy_preserves_user_boundaries_and_evidence(kind):
    contract, task = _overall_contract(mandatory=kind == "mandatory"), _task()
    response = {"task": "known-answer", "human_task_id": "durable-1", "answers": {"q1": ["A", "B"]}}
    evidence = {"basis": "confirmed_exact", "exhaustive": True, "citations": []}
    if kind == "permission":
        task["provenance"]["trigger"] = "need_permission"
    elif kind == "capability":
        task["capability_approval"] = {"state": "pending"}
    elif kind == "mandatory":
        task["provenance"]["trigger"] = "confirm_output"
    elif kind == "other_route":
        task["provenance"]["trigger"] = "manual_handoff"
    elif kind == "scope":
        evidence["category"] = "scope"
    result = decide_task_authority(
        task=task,
        contract=contract,
        current_task_id="durable-1",
        response=response,
        evidence=evidence,
        confirmed_sources={"contract": "A and B"},
    )
    assert result["allowed"] is False


def test_reading_v6_contract_preserves_digest_and_task_only_ownership():
    from cafe.core.packet_io import canonical_json

    contract = _contract()
    before = canonical_json(contract)
    assert contract["schema_version"] == 6
    assert canonical_json(validate_contract(contract)) == before
    result = decide_task_authority(
        task=_task(task_id="undeclared"),
        contract=contract,
        current_task_id="durable-1",
    )
    assert result["resolution_owner"] == "user_required"
    assert result["evidence_reason"] == "task_ownership_undeclared"
    assert canonical_json(contract) == before


def test_overall_policy_is_required_in_v7_and_cannot_be_inserted_into_v6():
    contract = _overall_contract()
    contract["reactive_user_handoffs"].pop("need_clarification")
    with pytest.raises(ValueError, match="explicit overall"):
        validate_contract(contract)
    contract = _overall_contract()
    contract["schema_version"] = 6
    with pytest.raises(ValueError, match="reconfirm as v7"):
        validate_contract(contract)


def test_driver_settings_update_preserves_overall_policy_and_schema():
    contract = _overall_contract("user_required", "driver_confirmable")
    updated = build_driver_settings_update(
        contract,
        {"mode": "attached", "poll_interval_seconds": 30},
        previous_contract_sha256="a" * 64,
    )
    assert updated["schema_version"] == 7
    assert updated["reactive_user_handoffs"] == contract["reactive_user_handoffs"]
    assert updated["task_contract"] == contract["task_contract"]
    facts = freshness_semantic_facts(updated)
    assert (
        facts["effective_policy"]["reactive_user_handoffs"]["need_clarification"] == "user_required"
    )
