"""Tests for declarative human-task policy contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cafe.core.human_tasks import HumanTaskPolicy


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
