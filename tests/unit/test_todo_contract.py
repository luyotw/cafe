"""Invariant tests for the canonical workflow Todo grammar."""

import json
import re
from pathlib import Path

import pytest

from cafe.core.todo import (
    TodoContractError,
    parse_todo_list,
    parse_todo_identity_continuity,
    resolve_todo_source,
    validate_todo_identities,
    workflow_feedback_todo_items,
)


def _item(work: str = "add parser") -> str:
    return (
        "- [ ] `PLAN-001` — Source: `plan` — Work: "
        + work
        + " — Closure: parser accepts valid input — Evidence: targeted pytest"
    )


def test_todo_parser_only_accepts_items_in_its_designated_section() -> None:
    content = "- [ ] unrelated\n## Test Plan\n- [ ] ignored\n## Todo List\n" + _item()
    items = parse_todo_list(content, expected_source="plan")
    assert [item.item_id for item in items] == ["PLAN-001"]


@pytest.mark.parametrize(
    "content",
    [
        "## Todo List\n",
        "## Todo List\nNo actionable work.\n" + _item(),
        "## Todo List\n- [ ] malformed",
        "## Todo List\n" + _item() + "\n" + _item("another task"),
        "## Todo List\n- [ ] `PLAN-001` — Source: `not-valid` — Work: x — Closure: y — Evidence: z",
    ],
)
def test_todo_parser_rejects_malformed_or_ambiguous_authoritative_work(content: str) -> None:
    with pytest.raises(TodoContractError):
        parse_todo_list(content)


def test_todo_parser_reports_the_malformed_section_line() -> None:
    content = "## Todo List\n" + _item() + "\nTrailing prose is not a Todo row.\n"

    with pytest.raises(TodoContractError, match=r"malformed item at line 3"):
        parse_todo_list(content)


def test_todo_parser_stops_the_section_at_a_new_heading() -> None:
    content = (
        "## Todo List\n"
        + _item()
        + "\n\n## Implementation Notes\nTrailing prose belongs to the notes.\n"
    )

    items = parse_todo_list(content)

    assert [item.item_id for item in items] == ["PLAN-001"]


def test_todo_fingerprint_changes_when_any_closure_requirement_changes() -> None:
    first = parse_todo_list("## Todo List\n" + _item())[0]
    changed = parse_todo_list("## Todo List\n" + _item("implement parser"))[0]
    assert first.fingerprint != changed.fingerprint


def test_todo_identity_continuity_parses_explicit_prior_work_fingerprints() -> None:
    content = (
        "## Todo List\n"
        + _item()
        + "\n\n## Todo Identity Continuity\n"
        "- `PLAN-001` — Previous work fingerprint: `"
        + "a" * 64
        + "`\n"
    )

    assert parse_todo_identity_continuity(content) == {"PLAN-001": "a" * 64}


def test_todo_identity_continuity_rejects_duplicate_or_malformed_rows() -> None:
    with pytest.raises(TodoContractError):
        parse_todo_identity_continuity(
            "## Todo List\n"
            + _item()
            + "\n\n## Todo Identity Continuity\n"
            "- `PLAN-001` — Previous work fingerprint: `short`\n"
        )


def test_todo_parser_accepts_only_the_canonical_intentionally_empty_marker() -> None:
    assert parse_todo_list("## Todo List\nNo actionable work.\n") == ()


@pytest.mark.parametrize(
    "content",
    [
        "# Todo List\n" + _item(),
        "## Todo List\n- [ ] `TASK-001` — Source: `plan` — Work: build — Closure: done — Evidence: test",
        "## Todo List\n- [ ] `PLAN-1` — Source: `plan` — Work: build — Closure: done — Evidence: test",
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work:  — Closure: done — Evidence: test",
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: build — Closure:  — Evidence: test",
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: build — Closure: done — Evidence:  ",
    ],
)
def test_todo_parser_rejects_noncanonical_heading_ids_and_empty_fields(content: str) -> None:
    with pytest.raises(TodoContractError):
        parse_todo_list(content)


def test_todo_item_identity_validation_preserves_revisions_and_rejects_reassignment() -> None:
    original = parse_todo_list("## Todo List\n" + _item())[0]
    revised = parse_todo_list(
        "## Todo List\n"
        "- [ ] `PLAN-001` — Source: `plan` — Work: improve parser — "
        "Closure: parser accepts valid input — Evidence: targeted pytest"
    )[0]
    added = parse_todo_list(
        "## Todo List\n"
        "- [ ] `PLAN-001` — Source: `plan` — Work: improve parser — "
        "Closure: parser accepts valid input — Evidence: targeted pytest\n"
        "- [ ] `PLAN-002` — Source: `plan` — Work: add docs — "
        "Closure: docs explain the contract — Evidence: docs review"
    )
    validate_todo_identities((original,), (revised,))
    validate_todo_identities((original,), added)
    reassigned = parse_todo_list(
        "## Todo List\n"
        "- [ ] `PLAN-001` — Source: `plan` — Work: unrelated work — "
        "Closure: another condition — Evidence: another test"
    )[0]
    with pytest.raises(TodoContractError, match="identity"):
        validate_todo_identities((original,), (reassigned,), retained=("PLAN-001",))


def test_todo_parser_accepts_a_declared_custom_source() -> None:
    items = parse_todo_list(
        "## Todo List\n- [ ] `CUSTOM-001` — Source: `bespoke` — Work: build — "
        "Closure: done — Evidence: test",
        expected_source="bespoke",
    )
    assert items[0].source == "bespoke"


def test_every_builtin_todo_producer_declares_the_empty_marker() -> None:
    skills = Path("src/cafe/data/skills")
    for name in ("cafe-plan", "cafe-review", "cafe-qa", "cafe-pr"):
        guidance = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        assert "No actionable work." in guidance
        assert "100" in guidance


def test_every_bundled_plan_template_has_one_canonical_todo_ledger() -> None:
    template_dir = Path("src/cafe/data/skills/cafe-plan/assets/templates")
    for name in ("default", "bug", "simple"):
        items = parse_todo_list((template_dir / f"{name}.md").read_text(encoding="utf-8"))
        if name == "simple":
            assert items == ()
        else:
            assert items
            assert all(item.source == "plan" for item in items)
            assert all(re.match(r"PLAN-\d{3}", item.item_id) for item in items)


def test_todo_parser_rejects_limit_plus_one_items() -> None:
    rows = [
        _item(f"work {index}").replace("PLAN-001", f"PLAN-{index:03d}") for index in range(1, 102)
    ]
    assert len(parse_todo_list("## Todo List\n" + "\n".join(rows[:100]))) == 100
    with pytest.raises(TodoContractError):
        parse_todo_list("## Todo List\n" + "\n".join(rows))


def test_correction_source_requires_explicit_causal_artifact(tmp_path) -> None:
    review = tmp_path / "review.md"
    qa = tmp_path / "qa.md"
    review.write_text("## Todo List\n", encoding="utf-8")
    qa.write_text("## Todo List\n", encoding="utf-8")
    selected = resolve_todo_source(
        artifact="qa_feedback",
        source="qa",
        artifacts={"review_feedback": review, "qa_feedback": qa},
    )
    assert selected.artifact == "qa_feedback"
    with pytest.raises(TodoContractError, match="missing causal"):
        resolve_todo_source(
            artifact="pr_result", source="pr_comment", artifacts={"qa_feedback": qa}
        )


def test_workflow_feedback_normalization_selects_exact_causal_entries(tmp_path) -> None:
    ledger = tmp_path / "workflow_feedback.json"
    entries = [
        {
            "source_identity": "local_review:pr:local-review:1",
            "source_kind": "local_review",
            "target_step": "develop",
            "content": "fix the local review blocker",
            "actionable": False,
            "consumed": True,
            "resolved": False,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        },
        {
            "source_identity": "github-pr:10:20",
            "source_kind": "github_pr",
            "target_step": "develop",
            "content": "fix the PR comment",
            "actionable": True,
            "consumed": False,
            "resolved": False,
            "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        },
        {
            "source_identity": "github-pr:10:30",
            "source_kind": "github_pr",
            "target_step": "other",
            "content": "unrelated",
            "actionable": True,
            "consumed": False,
            "resolved": False,
            "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        },
    ]
    ledger.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")

    sources = {"github_pr": "pr_comment", "local_review": "workflow_feedback"}
    prefixes = {"github_pr": "PRC", "local_review": "WF"}
    pending = workflow_feedback_todo_items(
        ledger,
        target_step="develop",
        source_by_kind=sources,
        id_prefix_by_kind=prefixes,
    )
    delivered = workflow_feedback_todo_items(
        ledger,
        target_step="develop",
        source_by_kind=sources,
        id_prefix_by_kind=prefixes,
        source_identities=("local_review:pr:local-review:1",),
    )
    assert [item.source for item in pending] == ["pr_comment"]
    assert pending[0].item_id.startswith("PRC-")
    assert [item.work for item in pending] == ["fix the PR comment"]
    assert [item.source for item in delivered] == ["workflow_feedback"]
    assert delivered[0].item_id.startswith("WF-")
    assert [item.work for item in delivered] == ["fix the local review blocker"]

    custom_source = workflow_feedback_todo_items(
        ledger,
        target_step="develop",
        source_by_kind={"github_pr": "bespoke"},
        id_prefix_by_kind={"github_pr": "PRC"},
    )
    assert custom_source[0].item_id == pending[0].item_id
    assert custom_source[0].source == "bespoke"

    with pytest.raises(TodoContractError, match="missing"):
        workflow_feedback_todo_items(
            ledger,
            target_step="develop",
            source_by_kind=sources,
            id_prefix_by_kind=prefixes,
            source_identities=("stale",),
        )
