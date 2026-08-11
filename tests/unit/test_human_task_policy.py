"""Tests for declarative human-task policy contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cafe.core.human_tasks import (
    HumanTaskBinding,
    HumanTaskCompletion,
    HumanTaskPolicy,
    HumanTaskQuestion,
    HumanTaskRejection,
    resolve_human_task_continuation,
    validate_human_task_completion,
)


def _decisions(*ids: str) -> list[dict[str, str]]:
    return [{"id": item, "label": item.title()} for item in ids]


@pytest.mark.parametrize(
    ("pattern", "input_schema", "extra"),
    [
        ("confirm_output", "decision", {"decisions": _decisions("confirm", "revise")}),
        (
            "answer_questions",
            "answers",
            {"questions": [{"id": "source", "prompt": "Choose a source"}]},
        ),
        ("revision_feedback", "feedback", {}),
        ("no_changes_needed", "decision", {"decisions": _decisions("agree", "disagree")}),
        ("select_next_step", "target", {"allowed_targets": ["collect"]}),
    ],
)
def test_policy_accepts_each_supported_response_pattern(
    pattern: str, input_schema: str, extra: dict,
) -> None:
    """Every declared pattern has one compatible, explicit answer shape."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": pattern,
            "pattern": pattern,
            "prompt": "Provide the requested response",
            "input_schema": input_schema,
            **extra,
        }
    )

    assert policy.pattern == pattern
    assert policy.input_schema == input_schema


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "unknown", "pattern": "unknown", "prompt": "Choose", "input_schema": "decision"},
        {"id": "empty", "pattern": "revision_feedback", "prompt": "  ", "input_schema": "feedback"},
        {
            "id": "duplicates",
            "pattern": "confirm_output",
            "prompt": "Choose",
            "input_schema": "decision",
            "decisions": _decisions("confirm", "confirm"),
        },
        {
            "id": "wrong-shape",
            "pattern": "answer_questions",
            "prompt": "Answer",
            "input_schema": "feedback",
        },
    ],
)
def test_policy_rejects_invalid_or_incompatible_declarations(payload: dict) -> None:
    """Malformed declarations fail before a workflow can pause for a person."""
    with pytest.raises(ValidationError):
        HumanTaskPolicy.model_validate(payload)


def test_interactive_and_json_answers_normalize_to_the_same_completion() -> None:
    """Every transport reaches the same answer and agent-facing input boundary."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "source-questions",
            "pattern": "answer_questions",
            "prompt": "Select the evidence source",
            "input_schema": "answers",
            "questions": [{"id": "source", "prompt": "Source", "options": ["Papers"]}],
        }
    )
    interactive = validate_human_task_completion(policy, {"answers": {"source": "Papers"}})
    command = validate_human_task_completion(
        policy,
        '{"task":"source-questions","answers":{"source":"Papers"}}',
    )

    assert interactive == command == HumanTaskCompletion(
        task_id="source-questions", answers={"source": ("Papers",)}
    )
    assert interactive.agent_input() == "source: Papers"

    approval = HumanTaskCompletion(task_id="review", decision="confirm")
    assert approval.agent_input() == ""


def test_dynamic_question_answers_require_every_current_xml_question() -> None:
    """The caller-provided XML contract governs both interactive and command answers."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "clarification-answers",
            "pattern": "answer_questions",
            "prompt": "Answer the pending questions",
            "input_schema": "answers",
            "questions_from_xml": True,
        }
    )
    questions = (
        HumanTaskQuestion(id="scope", prompt="Scope", options=("Small",)),
        HumanTaskQuestion(id="compatibility", prompt="Compatibility", options=("Yes",)),
    )

    incomplete = validate_human_task_completion(
        policy,
        {"answers": {"scope": "Small"}},
        questions=questions,
    )
    complete = validate_human_task_completion(
        policy,
        {"answers": {"scope": "Small", "compatibility": "Yes"}},
        questions=questions,
    )

    assert isinstance(incomplete, HumanTaskRejection)
    assert complete == HumanTaskCompletion(
        task_id="clarification-answers",
        answers={"scope": ("Small",), "compatibility": ("Yes",)},
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"task":"review","decision":"unknown"}',
        '{"task":"other","decision":"confirm"}',
        '{"task":"review","decision":"revise"}',
        "not-json",
    ],
)
def test_invalid_completion_has_no_continuation(payload: str) -> None:
    """Incomplete or unsupported decisions remain actionable and un-routed."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "review",
            "pattern": "confirm_output",
            "prompt": "Review output",
            "input_schema": "decision",
            "decisions": [
                {"id": "confirm", "label": "Approve"},
                {"id": "revise", "label": "Revise", "requires_feedback": True},
            ],
        }
    )

    result = validate_human_task_completion(policy, payload)

    assert isinstance(result, HumanTaskRejection)


def test_revision_decision_requires_declared_target_and_feedback() -> None:
    """One revision choice can safely route to any playbook-allowed phase."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "review",
            "pattern": "confirm_output",
            "prompt": "Review output",
            "input_schema": "decision",
            "decisions": [
                {"id": "confirm", "label": "Approve"},
                {
                    "id": "revise",
                    "label": "Revise",
                    "requires_feedback": True,
                    "requires_target": True,
                    "correction": True,
                },
            ],
            "allowed_targets": ["build", "knowledge"],
        }
    )

    completion = validate_human_task_completion(
        policy,
        {
            "task": "review",
            "decision": "revise",
            "target": "build",
            "feedback": "Repair the source mapping.",
        },
    )

    assert completion == HumanTaskCompletion(
        task_id="review",
        decision="revise",
        target="build",
        feedback="Repair the source mapping.",
    )
    assert (
        resolve_human_task_continuation(
            policy=policy,
            binding=HumanTaskBinding(
                trigger="confirm_output",
                task_id="review",
                outcomes={"confirm": "closeout"},
                allowed_targets=("build", "knowledge"),
            ),
            completion=completion,
            playbook_steps=["build", "knowledge", "closeout"],
        )
        == "build"
    )
    assert (
        resolve_human_task_continuation(
            policy=policy,
            binding=HumanTaskBinding(
                trigger="confirm_output",
                task_id="review",
                outcomes={"confirm": "closeout"},
                allowed_targets=("build", "knowledge"),
            ),
            completion=HumanTaskCompletion(task_id="review", decision="confirm"),
            playbook_steps=["build", "knowledge", "closeout"],
        )
        == "closeout"
    )
    assert (
        resolve_human_task_continuation(
            policy=policy,
            binding=HumanTaskBinding(
                trigger="confirm_output",
                task_id="review",
                outcomes={"build": "knowledge"},
                allowed_targets=("build", "knowledge"),
            ),
            completion=HumanTaskCompletion(
                task_id="review",
                decision="revise",
                target="build",
                feedback="Repair build.",
            ),
            playbook_steps=["build", "knowledge"],
        )
        == "build"
    )

    invalid_payloads = [
        {"task": "review", "decision": "revise", "feedback": "Missing target"},
        {
            "task": "review",
            "decision": "revise",
            "target": "closeout",
            "feedback": "Target is not allowed",
        },
        {"task": "review", "decision": "confirm", "target": "build"},
    ]
    assert all(
        isinstance(validate_human_task_completion(policy, payload), HumanTaskRejection)
        for payload in invalid_payloads
    )


def test_continuation_must_be_declared_by_binding_and_playbook() -> None:
    """A decision cannot use an undeclared or unavailable target."""
    policy = HumanTaskPolicy.model_validate(
        {
            "id": "choose-step",
            "pattern": "select_next_step",
            "prompt": "Choose a next step",
            "input_schema": "target",
            "allowed_targets": ["collect"],
        }
    )
    completion = validate_human_task_completion(policy, {"target": "collect"})

    assert isinstance(completion, HumanTaskCompletion)
    result = resolve_human_task_continuation(
        policy=policy,
        binding=HumanTaskBinding(
            trigger="need_clarification", task_id="choose-step", allowed_targets=("collect",)
        ),
        completion=completion,
        playbook_steps=["draft"],
    )

    assert isinstance(result, HumanTaskRejection)
