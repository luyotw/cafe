"""Declarative contracts and validation for workflow human handoffs.

Policies describe what a person must provide.  They deliberately do not own
blackboard state, iteration files, or baton ownership; those remain runtime
responsibilities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HumanTaskPattern = Literal[
    "confirm_output",
    "answer_questions",
    "revision_feedback",
    "no_changes_needed",
    "select_next_step",
]
HumanTaskInputSchema = Literal["decision", "answers", "feedback", "target"]

_SCHEMA_BY_PATTERN: dict[str, str] = {
    "confirm_output": "decision",
    "answer_questions": "answers",
    "revision_feedback": "feedback",
    "no_changes_needed": "decision",
    "select_next_step": "target",
}


def _non_empty(value: str, *, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


class HumanTaskDecision(BaseModel):
    """One declared choice for a decision-based task."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    requires_feedback: bool = False

    @field_validator("id", "label")
    @classmethod
    def _validate_copy(cls, value: str, info) -> str:
        return _non_empty(value, field_name=info.field_name)


class HumanTaskQuestion(BaseModel):
    """One required structured answer in an answer-questions policy."""

    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    options: tuple[str, ...] = ()
    multiple: bool = False

    @field_validator("id", "prompt")
    @classmethod
    def _validate_copy(cls, value: str, info) -> str:
        return _non_empty(value, field_name=info.field_name)

    @field_validator("options")
    @classmethod
    def _validate_options(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_non_empty(item, field_name="question option") for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("question options must be unique")
        return cleaned


class HumanTaskPolicy(BaseModel):
    """Reusable presentation and completion contract owned by a skill."""

    model_config = ConfigDict(extra="forbid")

    id: str
    pattern: HumanTaskPattern
    prompt: str
    input_schema: HumanTaskInputSchema
    required: bool = True
    correction_guidance: str = "Provide a complete response using the requested format."
    decisions: tuple[HumanTaskDecision, ...] = ()
    questions: tuple[HumanTaskQuestion, ...] = ()
    questions_from_xml: bool = False
    allowed_targets: tuple[str, ...] = ()

    @field_validator("id", "prompt", "correction_guidance")
    @classmethod
    def _validate_copy(cls, value: str, info) -> str:
        return _non_empty(value, field_name=info.field_name)

    @field_validator("allowed_targets")
    @classmethod
    def _validate_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_non_empty(item, field_name="allowed target") for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("allowed targets must be unique")
        return cleaned

    @model_validator(mode="after")
    def _validate_shape(self) -> "HumanTaskPolicy":
        expected = _SCHEMA_BY_PATTERN[self.pattern]
        if self.input_schema != expected:
            raise ValueError(
                f"pattern {self.pattern!r} requires input_schema {expected!r}"
            )
        decision_ids = [item.id for item in self.decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise ValueError("decision ids must be unique")
        question_ids = [item.id for item in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("question ids must be unique")
        if self.input_schema == "decision" and not self.decisions:
            raise ValueError("decision policies require at least one decision")
        if self.input_schema == "answers" and not self.questions and not self.questions_from_xml:
            raise ValueError("answer policies require inline questions or questions_from_xml")
        if self.input_schema == "target" and not self.allowed_targets:
            raise ValueError("target policies require at least one allowed target")
        if self.input_schema != "decision" and self.decisions:
            raise ValueError("only decision policies may declare decisions")
        if self.input_schema != "answers" and self.questions:
            raise ValueError("only answer policies may declare questions")
        if self.input_schema != "answers" and self.questions_from_xml:
            raise ValueError("only answer policies may use questions_from_xml")
        return self


class HumanTaskBinding(BaseModel):
    """Step-level binding of a skill policy to declared continuation targets."""

    model_config = ConfigDict(extra="forbid")

    trigger: str
    task_id: str
    outcomes: dict[str, str] = Field(default_factory=dict)
    allowed_targets: tuple[str, ...] = ()
    prompt: Optional[str] = None
    correction_guidance: Optional[str] = None

    @field_validator("trigger", "task_id")
    @classmethod
    def _validate_identifier(cls, value: str, info) -> str:
        return _non_empty(value, field_name=info.field_name)

    @field_validator("outcomes")
    @classmethod
    def _validate_outcomes(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            _non_empty(key, field_name="outcome id"): _non_empty(
                target, field_name="outcome target"
            )
            for key, target in value.items()
        }

    @field_validator("allowed_targets")
    @classmethod
    def _validate_allowed_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_non_empty(item, field_name="allowed target") for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("allowed targets must be unique")
        return cleaned


class HumanTaskPolicyError(ValueError):
    """Raised when a task declaration cannot be resolved safely."""


@dataclass(frozen=True)
class HumanTaskCompletion:
    """Validated, transport-neutral participant response."""

    task_id: str
    decision: Optional[str] = None
    answers: Optional[dict[str, tuple[str, ...]]] = None
    feedback: Optional[str] = None
    target: Optional[str] = None

    def agent_input(self) -> str:
        """Preserve the existing agent-facing text-file boundary."""
        if self.feedback:
            return self.feedback
        if self.answers:
            return "\n".join(
                f"{question}: {', '.join(answer)}" for question, answer in self.answers.items()
            )
        if self.decision:
            return self.decision
        return self.target or ""


@dataclass(frozen=True)
class HumanTaskRejection:
    """Actionable validation failure that deliberately has no continuation."""

    message: str
    correction_guidance: str


def resolve_human_task_policy(
    *,
    defaults: Sequence[HumanTaskPolicy],
    binding: HumanTaskBinding,
) -> HumanTaskPolicy:
    """Merge a selected skill default with permitted step-level copy overrides."""
    matching = [policy for policy in defaults if policy.id == binding.task_id]
    if len(matching) != 1:
        raise HumanTaskPolicyError(
            f"No declared human-task policy matches task_id {binding.task_id!r}"
        )
    policy = matching[0]
    changes: dict[str, Any] = {}
    if binding.prompt is not None:
        changes["prompt"] = binding.prompt
    if binding.correction_guidance is not None:
        changes["correction_guidance"] = binding.correction_guidance
    if binding.allowed_targets:
        changes["allowed_targets"] = binding.allowed_targets
    return HumanTaskPolicy.model_validate({**policy.model_dump(), **changes})


def validate_human_task_completion(
    policy: HumanTaskPolicy,
    raw_payload: str | Mapping[str, Any],
    *,
    questions: Optional[Sequence[HumanTaskQuestion]] = None,
) -> HumanTaskCompletion | HumanTaskRejection:
    """Normalize interactive or command input without changing workflow state."""
    payload = _parse_payload(policy, raw_payload)
    if isinstance(payload, HumanTaskRejection):
        return payload
    task_id = str(payload.get("task") or payload.get("task_id") or policy.id).strip()
    if task_id != policy.id:
        return _reject(policy, "This response belongs to a different human task.")
    if policy.input_schema == "feedback":
        feedback = str(payload.get("feedback") or "").strip()
        if not feedback and policy.required:
            return _reject(policy, "Feedback is required before this task can continue.")
        return HumanTaskCompletion(task_id=policy.id, feedback=feedback or None)
    if policy.input_schema == "decision":
        decision = str(payload.get("decision") or "").strip()
        valid = {item.id: item for item in policy.decisions}
        selected = valid.get(decision)
        if selected is None:
            return _reject(policy, "Choose one of the declared decisions.")
        feedback = str(payload.get("feedback") or "").strip() or None
        if selected.requires_feedback and not feedback:
            return _reject(policy, "This decision requires feedback.")
        return HumanTaskCompletion(task_id=policy.id, decision=decision, feedback=feedback)
    if policy.input_schema == "target":
        target = str(payload.get("target") or "").strip()
        if target not in policy.allowed_targets:
            return _reject(policy, "Choose a target declared by this task.")
        return HumanTaskCompletion(task_id=policy.id, target=target)
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, Mapping):
        return _reject(policy, "Answers must be an object keyed by question id.")
    questions_to_validate = tuple(questions) if questions is not None else policy.questions
    if policy.questions_from_xml and not questions_to_validate:
        return _reject(policy, "The workflow has no valid clarification questions to answer.")
    answers: dict[str, tuple[str, ...]] = {}
    for question in questions_to_validate:
        provided = raw_answers.get(question.id)
        values = _answer_values(provided)
        if not values:
            return _reject(policy, f"Answer the required question {question.id!r}.")
        if not question.multiple and len(values) != 1:
            return _reject(policy, f"Question {question.id!r} accepts one answer.")
        if (
            question.options
            and not policy.questions_from_xml
            and any(value not in question.options for value in values)
        ):
            return _reject(policy, f"Question {question.id!r} has an unsupported answer.")
        answers[question.id] = values
    return HumanTaskCompletion(task_id=policy.id, answers=answers)


def resolve_human_task_continuation(
    *,
    policy: HumanTaskPolicy,
    binding: HumanTaskBinding,
    completion: HumanTaskCompletion,
    playbook_steps: Sequence[str],
) -> str | HumanTaskRejection:
    """Return only a step explicitly declared by the binding and playbook."""
    key = completion.target or completion.decision or "submit"
    target = binding.outcomes.get(key)
    if target is None and completion.target is not None:
        target = completion.target
    if not target:
        return _reject(policy, "This response has no declared continuation.")
    allowed = set(binding.allowed_targets or policy.allowed_targets)
    if allowed and target not in allowed:
        return _reject(policy, "The selected continuation is not permitted by this task.")
    if target != "_done" and target not in set(playbook_steps):
        return _reject(policy, "The selected continuation is not declared by this playbook.")
    return target


def _parse_payload(
    policy: HumanTaskPolicy, raw_payload: str | Mapping[str, Any]
) -> dict[str, Any] | HumanTaskRejection:
    if isinstance(raw_payload, Mapping):
        return dict(raw_payload)
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return _reject(policy, "A response is required.")
    text = raw_payload.strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        if policy.input_schema == "feedback":
            return {"task": policy.id, "feedback": text}
        return _reject(policy, "Use the declared structured response format.")
    if not isinstance(decoded, dict):
        return _reject(policy, "The response must be a JSON object.")
    return decoded


def _answer_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _reject(policy: HumanTaskPolicy, message: str) -> HumanTaskRejection:
    return HumanTaskRejection(message=message, correction_guidance=policy.correction_guidance)
