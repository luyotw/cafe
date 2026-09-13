"""Unit tests for checklist_validator module."""

import subprocess
from unittest.mock import patch

import pytest

from cafe.core.todo import parse_todo_list
from cafe.utils.checklist_validator import (
    MAX_EVIDENCE_PATHS_PER_ITEM,
    completion_requires_checklist,
    validate_checklist,
    validate_projected_todos,
    validate_todo_evidence_set,
)
from cafe.verification import run_verification


@pytest.mark.parametrize("intent", ["await_agent", "confirm_output", "workflow_complete"])
def test_completion_baton_requires_checklist(intent):
    assert completion_requires_checklist(baton_intent=intent, status_code="need_clarification")


@pytest.mark.parametrize("intent", ["need_clarification", "need_permission", "manual_handoff"])
def test_pause_baton_does_not_require_checklist(intent):
    assert not completion_requires_checklist(baton_intent=intent, status_code="confirmed")


def test_legacy_status_is_used_only_without_baton():
    assert completion_requires_checklist(status_code="ready_for_review")
    assert not completion_requires_checklist(status_code="need_clarification")


def test_validate_checklist_all_complete(tmp_path):
    """Test validation with all items checked."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[x] Task 1
[x] Task 2
[x] Task 3
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is True
    assert result.unchecked_count == 0
    assert result.checklist_path == checklist_file


def test_validate_checklist_partial_complete(tmp_path):
    """Test validation with some unchecked items."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[x] Task 1
[ ] Task 2
[x] Task 3
[ ] Task 4
[ ] Task 5
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is False
    assert result.unchecked_count == 3
    assert result.checklist_path == checklist_file


def test_validate_checklist_empty_file(tmp_path):
    """Test validation with empty file."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("")

    result = validate_checklist(checklist_file)

    assert result.is_complete is True
    assert result.unchecked_count == 0
    assert result.checklist_path == checklist_file


def test_validate_checklist_special_formats(tmp_path):
    """Test that special formats are not counted as unchecked.

    Only "[ ]" (bracket space bracket) should be counted as unchecked.
    Other variations like "[]", "[ x ]", "[x]", etc. should not be counted.
    """
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
[] Not a checkbox
[ x ] Task with spaces
[x] Completed task
[X] Also completed
[-] Partial task
[~] In progress task
[ ] Unchecked task 1
Some text with [] brackets
[ ] Unchecked task 2
""")

    result = validate_checklist(checklist_file)

    # Only the two "[ ]" patterns should be counted
    assert result.is_complete is False
    assert result.unchecked_count == 2
    assert result.checklist_path == checklist_file


def test_validate_checklist_file_not_found(tmp_path):
    """Test validation with non-existent file."""
    checklist_file = tmp_path / "nonexistent.md"

    with pytest.raises(FileNotFoundError) as exc_info:
        validate_checklist(checklist_file)

    assert "Checklist file not found" in str(exc_info.value)
    assert str(checklist_file) in str(exc_info.value)


def test_validate_checklist_with_nested_checkboxes(tmp_path):
    """Test validation with nested/indented checkboxes."""
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""## Checklist
- [ ] Top level unchecked
  - [x] Nested checked
  - [ ] Nested unchecked
- [x] Top level checked
    - [ ] Deep nested unchecked
""")

    result = validate_checklist(checklist_file)

    assert result.is_complete is False
    assert result.unchecked_count == 3
    assert result.checklist_path == checklist_file


def test_projected_todo_completion_requires_exact_set_and_ledger(tmp_path):
    source = (
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: x — Closure: y — Evidence: z\n"
    )
    item = parse_todo_list(source)[0]
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n", encoding="utf-8")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: a.py\n- Commit: abc\n- Targeted evidence: tests passed\n"
        "- Remaining work: None.\n- Next action: Review.\n",
        encoding="utf-8",
    )
    assert validate_projected_todos(checklist, output, (item,)) == []
    checklist.write_text("", encoding="utf-8")
    assert validate_projected_todos(checklist, output, (item,))


def test_projected_todo_evidence_is_bound_to_repository_state(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source_file = tmp_path / "src" / "feature.py"
    test_file = tmp_path / "tests" / "test_feature.py"
    source_file.parent.mkdir()
    test_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    test_file.write_text("def test_value(): assert True\n")
    (tmp_path / ".gitignore").write_text(
        "checklist.md\noutput.md\nverification.json\nverification.log\n.pytest_cache/\n__pycache__/\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Add feature"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    item = parse_todo_list(
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: x — Closure: y — Evidence: z\n"
    )[0]
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    ledger = (
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: `src/feature.py`, `tests/test_feature.py`\n"
        f"- Commit: `{head}`\n"
        f"- Targeted evidence: command=`pytest -q tests/test_feature.py`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    output.write_text(ledger)
    assert (
        run_verification(
            output_file=output,
            command=["pytest", "-q", "tests/test_feature.py"],
            scope="targeted",
            cwd=tmp_path,
        )[0]
        == 0
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []
    output.write_text(ledger.replace("pytest -q tests/test_feature.py", "python -c pass"))
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    output.write_text(ledger.replace(head, "0" * 40))
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)


def test_projected_todo_accepts_only_verifiable_no_change_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    test_file = tmp_path / "tests" / "test_noop.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_noop(): assert True\n")
    (tmp_path / ".gitignore").write_text(
        "checklist.md\noutput.md\nverification.json\nverification.log\n.pytest_cache/\n__pycache__/\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Add test"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    item = parse_todo_list(
        "## Todo List\n"
        "- [ ] `PLAN-001` — Source: `plan` — Work: inspect — "
        "Closure: done — Evidence: test\n"
    )[0]
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: N/A (no repository changes)\n"
        "- Commit: N/A (no repository changes): inspection-only item\n"
        f"- Targeted evidence: command=`pytest -q tests/test_noop.py`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert (
        run_verification(
            output_file=output,
            command=["pytest", "-q", "tests/test_noop.py::test_noop"],
            scope="targeted",
            cwd=tmp_path,
        )[0]
        == 0
    )
    output.write_text(
        output.read_text().replace(
            "pytest -q tests/test_noop.py", "pytest -q tests/test_noop.py::test_noop"
        )
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []
    test_file.write_text("def test_noop(): assert False\n")
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)


def test_repository_evidence_queries_are_constant_for_many_items(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source_file = tmp_path / "src" / "feature.py"
    test_file = tmp_path / "tests" / "test_feature.py"
    source_file.parent.mkdir()
    test_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    test_file.write_text("def test_value(): assert True\n")
    (tmp_path / ".gitignore").write_text(
        "checklist.md\noutput.md\nverification.json\nverification.log\n.pytest_cache/\n__pycache__/\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Add feature"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    items = parse_todo_list(
        "## Todo List\n"
        + "\n".join(
            f"- [ ] `PLAN-{index:03d}` — Source: `plan` — Work: work {index} — "
            "Closure: done — Evidence: test"
            for index in range(1, 11)
        )
    )
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text("\n".join(item.checklist_row().replace("[ ]", "[x]") for item in items))
    command = "pytest -q tests/test_feature.py::test_value"
    output.write_text(
        "## Todo Progress\n\n"
        + "\n\n".join(
            f"### {item.item_id}\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n"
            "- Files: `src/feature.py`, `tests/test_feature.py`\n"
            f"- Commit: `{head}`\n"
            f"- Targeted evidence: command=`{command}`; exit=0; head=`{head}`\n"
            "- Remaining work: None.\n- Next action: Review."
            for item in items
        )
    )
    assert (
        run_verification(
            output_file=output,
            command=command.split(),
            scope="targeted",
            cwd=tmp_path,
        )[0]
        == 0
    )
    from cafe.utils import checklist_validator

    calls = 0
    real_git = checklist_validator._git

    def counted_git(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_git(*args, **kwargs)

    with patch.object(checklist_validator, "_git", side_effect=counted_git):
        assert validate_projected_todos(checklist, output, items, repo_root=tmp_path) == []
    assert calls == 4


def test_repository_evidence_rejects_path_limit_before_git(tmp_path):
    fields = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM + 1)
        ),
        "Commit": f"`{'a' * 40}`",
        "Targeted evidence": f"command=`pytest -q tests/test_0.py`; exit=0; head=`{'a' * 40}`",
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set(
            {"PLAN-001": fields}, tmp_path, output_path=tmp_path / "output.md"
        )
    assert errors["PLAN-001"]
    git_call.assert_not_called()


def test_repository_evidence_rejects_commit_limit_before_git(tmp_path):
    from cafe.utils.checklist_validator import MAX_EVIDENCE_COMMITS_PER_ITEM

    fields = {
        "Files": "`tests/test_feature.py`",
        "Commit": ", ".join(
            f"`{index:040x}`" for index in range(MAX_EVIDENCE_COMMITS_PER_ITEM + 1)
        ),
        "Targeted evidence": (
            f"command=`pytest -q tests/test_feature.py`; exit=0; head=`{'a' * 40}`"
        ),
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set(
            {"PLAN-001": fields}, tmp_path, output_path=tmp_path / "output.md"
        )
    assert errors["PLAN-001"]
    git_call.assert_not_called()


def test_repository_evidence_accepts_exact_cardinality_limits_before_lookup(tmp_path):
    from cafe.utils.checklist_validator import MAX_EVIDENCE_COMMITS_PER_ITEM

    fields = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM)
        ),
        "Commit": ", ".join(f"`{index:040x}`" for index in range(MAX_EVIDENCE_COMMITS_PER_ITEM)),
        "Targeted evidence": f"command=`pytest -q tests/test_0.py`; exit=0; head=`{'a' * 40}`",
    }
    failed = subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr="fail")
    with patch("cafe.utils.checklist_validator._git", return_value=failed) as git_call:
        errors = validate_todo_evidence_set(
            {"PLAN-001": fields}, tmp_path, output_path=tmp_path / "output.md"
        )
    assert errors["PLAN-001"]
    assert git_call.call_count == 4


def test_repository_evidence_empty_set_performs_no_queries(tmp_path):
    with patch("cafe.utils.checklist_validator._git") as git_call:
        assert validate_todo_evidence_set({}, tmp_path, output_path=None) == {}
    git_call.assert_not_called()


def test_repository_evidence_git_timeout_fails_closed(tmp_path):
    fields = {
        "Files": "`tests/test_feature.py`",
        "Commit": f"`{'a' * 40}`",
        "Targeted evidence": (
            f"command=`pytest -q tests/test_feature.py`; exit=0; head=`{'a' * 40}`"
        ),
    }
    with patch(
        "cafe.utils.checklist_validator.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 10),
    ):
        errors = validate_todo_evidence_set(
            {"PLAN-001": fields}, tmp_path, output_path=tmp_path / "output.md"
        )
    assert errors["PLAN-001"]


@pytest.mark.parametrize(
    "mutation",
    [
        "rename",
        "rewrite",
        "duplicate",
        "omit",
        "unchecked",
    ],
)
def test_projected_todo_completion_rejects_every_row_tamper(tmp_path, mutation):
    items = parse_todo_list(
        "## Todo List\n"
        "- [ ] `PLAN-001` — Source: `plan` — Work: first — Closure: done — Evidence: test\n"
        "- [ ] `PLAN-002` — Source: `plan` — Work: second — Closure: done — Evidence: test\n"
    )
    rows = [item.checklist_row().replace("[ ]", "[x]") for item in items]
    if mutation == "rename":
        rows[0] = rows[0].replace("PLAN-001", "PLAN-009")
    elif mutation == "rewrite":
        rows[0] = rows[0].replace("first", "different")
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "omit":
        rows.pop()
    else:
        rows[0] = rows[0].replace("[x]", "[ ]")
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text("\n".join(rows) + "\n", encoding="utf-8")
    output.write_text(
        "## Todo Progress\n\n"
        + "\n\n".join(
            f"### {item.item_id}\n\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n- Files: a.py\n"
            "- Commit: abc\n- Targeted evidence: tests passed\n"
            "- Remaining work: None.\n- Next action: Review."
            for item in items
        ),
        encoding="utf-8",
    )
    assert validate_projected_todos(checklist, output, items)


def test_empty_authoritative_set_still_reconciles_projected_rows(tmp_path):
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    output.write_text("## Todo Progress\n", encoding="utf-8")
    checklist.write_text(
        "[x] `STALE-001` — stale work (source fingerprint: " + "a" * 64 + ")\n",
        encoding="utf-8",
    )
    assert validate_projected_todos(checklist, output, ())
    checklist.write_text("[x] ordinary gate\n", encoding="utf-8")
    assert validate_projected_todos(checklist, output, ()) == []


@pytest.mark.parametrize(
    "replacement",
    [
        "- Files: ",
        "- Commit: ",
        "- Targeted evidence: unavailable",
        "- Status: disputed",
    ],
)
def test_todo_ledger_rejects_empty_unavailable_or_open_evidence(tmp_path, replacement):
    item = parse_todo_list(
        "## Todo List\n"
        "- [ ] `BLK-001` — Source: `review` — Work: fix — Closure: done — Evidence: test\n"
    )[0]
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    fields = [
        "- Status: completed",
        f"- Source fingerprint: `{item.fingerprint}`",
        "- Files: a.py",
        "- Commit: abc",
        "- Targeted evidence: tests passed",
        "- Remaining work: None.",
        "- Next action: Review.",
    ]
    prefix = replacement.split(":", 1)[0] + ":"
    fields = [replacement if field.startswith(prefix) else field for field in fields]
    output.write_text("## Todo Progress\n\n### BLK-001\n\n" + "\n".join(fields))
    assert validate_projected_todos(checklist, output, (item,))


def test_todo_ledger_rejects_duplicate_and_prefix_colliding_entries(tmp_path):
    item = parse_todo_list(
        "## Todo List\n"
        "- [ ] `BLK-001` — Source: `review` — Work: fix — Closure: done — Evidence: test\n"
    )[0]
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    valid = (
        f"- Status: completed\n- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: a.py\n- Commit: abc\n- Targeted evidence: tests passed\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    output.write_text("## Todo Progress\n\n### BLK-001-extra\n\n" + valid)
    assert validate_projected_todos(checklist, output, (item,))
    output.write_text("## Todo Progress\n\n### BLK-001\n\n" + valid + "\n### BLK-001\n\n" + valid)
    assert validate_projected_todos(checklist, output, (item,))


def test_validate_checklist_mixed_content(tmp_path):
    """Test validation with mixed markdown content.

    Only checkbox items at the start of lines should be counted,
    not [ ] appearing in regular text or code blocks.
    """
    checklist_file = tmp_path / "checklist.md"
    checklist_file.write_text("""# Title

Some description text

## Section 1
- [x] Done
- [ ] Not done

Some more text with [ ] in it (should NOT count!)

## Section 2
- [x] All done here

Code example:
```python
# This [ ] should NOT count as it's not at line start
result = some_function()
```
""")

    result = validate_checklist(checklist_file)

    # Should find only 1 unchecked: the one in Section 1 checklist item
    # Text and code block [ ] should NOT be counted
    assert result.is_complete is False
    assert result.unchecked_count == 1  # Only the actual checklist item
    assert result.checklist_path == checklist_file
