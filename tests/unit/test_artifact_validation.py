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


def review_report(registry_rows, todos, *, outer_pipes=True):
    table = [
        "| ID | kind | root-cause identity | first-seen iteration/HEAD | impact | "
        "confidence | current status | linked ID | evidence HEAD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *registry_rows,
    ]
    if not outer_pipes:
        table = [line.strip("|").strip() for line in table]
    return "\n".join([
        "## Finding Registry",
        "",
        *table,
        "",
        "## Todo List",
        "",
        *todos,
        "",
    ])


@pytest.mark.parametrize("outer_pipes", [True, False])
@pytest.mark.parametrize(("original", "residual", "scenario", "link_from_residual"), [
    ("BLK-004", "BLK-011", "A rename submits stale points after a peer refresh", True),
    ("BLK-007", "BLK-010", "A catalog retry stalls after two unrelated peer edits", False),
])
def test_review_keeps_linked_residual_under_original_blocker(
    original, residual, scenario, link_from_residual, outer_pipes,
):
    old_link, new_link = ("—", original) if link_from_residual else (residual, "—")
    todo = "- [ ] `{}` — Source: `review` — Work: " + scenario + (
        " — Closure: Preserve the original acceptance. — Evidence: candidate probe 52ec9a4f:42"
    )
    rejected = review_report([
        f"| {original} | BLK | original acceptance | 1/8903e19b | Important | 95 | "
        f"closed | {old_link} | 52ec9a4f |",
        f"| {residual} | BLK | residual behavior | 2/52ec9a4f | Important | 95 | "
        f"open | {new_link} | 52ec9a4f |",
    ], [todo.format(residual)], outer_pipes=outer_pipes)
    assert parse_todo_list(rejected)[0].item_id == residual

    with pytest.raises(ArtifactFormatError) as error:
        validate(rejected)
    assert original in error.value.diagnostic and residual in error.value.diagnostic

    corrected = review_report([
        f"| {original} | BLK | original acceptance | 1/8903e19b | Important | 95 | "
        "open | — | 52ec9a4f |",
    ], [todo.format(original)], outer_pipes=outer_pipes)
    assert validate(corrected).items == parse_todo_list(corrected, expected_source="review")
    assert validate(corrected).items[0].item_id == original
    assert validate(corrected).items[0].work == scenario
    assert validate(corrected).items[0].evidence == "candidate probe 52ec9a4f:42"


@pytest.mark.parametrize("todo", ["No actionable work.",
    "- [x] `BLK-001` — Source: `review` — Work: Investigate — Closure: Resolve — Evidence: Probe",
])
def test_open_review_blocker_requires_an_unchecked_todo(todo):
    content = review_report([
        "| BLK-001 | BLK | missing evidence | 1/8903e19b | Important | 95 | open | — | 52ec9a4f |",
    ], [todo])
    with pytest.raises(ArtifactFormatError, match="BLK-001"):
        validate(content)


def test_review_accepts_unrelated_new_blocker_and_linked_follow_up():
    content = review_report([
        "| BLK-001 | BLK | fixed behavior | 1/8903e19b | Important | 95 | "
        "closed | — | 52ec9a4f |",
        "| BLK-002 | BLK | unrelated behavior | 2/52ec9a4f | Important | 95 | "
        "open | FUP-001 | 52ec9a4f |",
        "| FUP-001 | FUP | unrelated behavior | 1/8903e19b | Important | 95 | "
        "promoted | BLK-002 | 52ec9a4f |",
    ], ["- [ ] `BLK-002` — Source: `review` — Work: Write — Closure: Resolve — Evidence: Probe"])
    assert [item.item_id for item in validate(content).items] == ["BLK-002"]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fenced_review_registry_example_does_not_apply_to_report(fence):
    registry, _, _ = review_report([
        "| BLK-001 | BLK | example | 1/8903e19b | Important | 95 | "
        "open | — | 52ec9a4f |",
    ], ["No actionable work."]).partition("## Todo List")
    example = f"# Document\n\n{fence}markdown\n{registry}{fence}\n"
    empty_todos = "\n## Todo List\n\nNo actionable work.\n"
    assert validate(example + empty_todos).items == ()
    actual = review_report([
        "| BLK-002 | BLK | unresolved | 1/8903e19b | Important | 95 | "
        "open | — | 52ec9a4f |",
    ], ["No actionable work."])
    with pytest.raises(ArtifactFormatError, match="BLK-002"):
        validate(example + "\n" + actual)


def test_review_registry_without_todos_remains_available_as_a_partial_report():
    content, _, _ = review_report([
        "| BLK-001 | BLK | awaiting clarification | 1/8903e19b | Important | 95 | "
        "open | — | 52ec9a4f |",
    ], []).partition("## Todo List")
    assert validate(content).items is None


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
