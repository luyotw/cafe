"""U1–U4: strict producer syntax, narrow diagnostics and persisted repair budget."""

import json
from pathlib import Path

import pytest

from cafe.core.artifact_validation import (
    ArtifactCorrectionBudget,
    ArtifactCorrectionExhausted,
    ArtifactFormatError,
    validate_artifact_syntax,
)
from cafe.core.todo import TodoContractError, parse_todo_list

FIXTURES = Path(__file__).parents[1] / "fixtures" / "artifact_correction"


def validate(content):
    return validate_artifact_syntax(
        content, producer="inspect_custom", artifact="evidence_bundle", path="output.md"
    )


def test_u1_duplicate_rejected_and_evidence_preserving_control_valid():
    rejected = (FIXTURES / "rejected.md").read_text()
    corrected = (FIXTURES / "corrected.md").read_text()
    with pytest.raises(TodoContractError):
        parse_todo_list(rejected)
    assert parse_todo_list(corrected) == ()
    assert validate(corrected).items == ()
    for line in rejected.splitlines():
        if "evidence:" in line or "proposal:" in line:
            assert line in corrected


@pytest.mark.parametrize("content", [
    (FIXTURES / "rejected.md").read_text(),
    "<!-- plan-stage: detailed-plan -->\n# Missing Todo List\n",
    "<!-- plan-stage: solution-alignment -->\n## Todo List\nNo actionable work.\n",
])
def test_u2_known_syntax_failure_carries_declared_identity(content):
    with pytest.raises(ArtifactFormatError) as failure:
        validate(content)
    diagnostic = failure.value.to_dict()
    assert diagnostic["producer"] == "inspect_custom"
    assert diagnostic["artifact"] == "evidence_bundle"
    assert diagnostic["path"] == "output.md"
    assert diagnostic["diagnostic"]
    assert isinstance(failure.value.__cause__, TodoContractError)


def test_u1_provisional_alignment_remains_exempt():
    assert validate("<!-- plan-stage: solution-alignment -->\n# Direction\n").items == ()
    assert validate("# Ordinary document\n").items is None



def test_u3_changed_diagnostics_and_reload_do_not_replenish_budget():
    budget = ArtifactCorrectionBudget()
    for index in range(3):
        error = ArtifactFormatError("inspect_custom", "evidence_bundle", "output.md", f"failure {index}")
        budget.reject(error)
        if index < 2:
            budget.consume()
            budget = ArtifactCorrectionBudget.from_dict(json.loads(json.dumps(budget.to_dict())))
        else:
            with pytest.raises(ArtifactCorrectionExhausted) as failure:
                budget.consume()
            assert failure.value.budget.consumed == 2
            assert len(failure.value.budget.rejections) == 3
            assert "failure 2" in str(failure.value)
            assert "evidence_bundle" in str(failure.value)
    assert budget.consumed == 2


def test_u4_feedback_is_report_only_and_preserves_evidence():
    error = ArtifactFormatError("inspect_custom", "evidence_bundle", "output.md", "duplicate sections")
    prompt = error.correction_prompt(remaining=1)
    for value in ("inspect_custom", "evidence_bundle", "output.md", "duplicate sections"):
        assert value in prompt
    for concept in ("acceptance evidence", "follow-up proposals", "checklist", "authority", "session"):
        assert concept in prompt
    assert "Do not" in prompt
