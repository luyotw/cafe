"""Invariant tests for the canonical workflow Todo grammar."""

import pytest

from cafe.core.todo import TodoContractError, parse_todo_list


def _item(work: str = "add parser") -> str:
    return (
        "- [ ] `PLAN-001` — Source: `plan` — Work: " + work
        + " — Closure: parser accepts valid input — Evidence: targeted pytest"
    )


def test_todo_parser_only_accepts_items_in_its_designated_section() -> None:
    content = "- [ ] unrelated\n## Test Plan\n- [ ] ignored\n## Todo List\n" + _item()
    items = parse_todo_list(content, expected_source="plan")
    assert [item.item_id for item in items] == ["PLAN-001"]


@pytest.mark.parametrize(
    "content",
    [
        "## Todo List\n- [ ] malformed",
        "## Todo List\n" + _item() + "\n" + _item("another task"),
        "## Todo List\n- [ ] `PLAN-001` — Source: `unknown` — Work: x — Closure: y — Evidence: z",
    ],
)
def test_todo_parser_rejects_malformed_or_ambiguous_authoritative_work(content: str) -> None:
    with pytest.raises(TodoContractError):
        parse_todo_list(content)


def test_todo_fingerprint_changes_when_any_closure_requirement_changes() -> None:
    first = parse_todo_list("## Todo List\n" + _item())[0]
    changed = parse_todo_list("## Todo List\n" + _item("implement parser"))[0]
    assert first.fingerprint != changed.fingerprint
