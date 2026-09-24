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


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)


@pytest.mark.parametrize(
    "targeted",
    [
        "- Targeted evidence: command=`pnpm --filter web exec vitest run`; exit=0\n",
        "- Targeted evidence: ran some tests, they passed\n",
        "- Targeted evidence: n/a\n",
        # Duplicate occurrences and indented continuation are informational too.
        "- Targeted evidence: web suite green\n- Targeted evidence: api suite green\n",
        "- Targeted evidence: e2e run\n  - Browser: Chromium\n  - Shard: 2/4\n",
        "",
    ],
)
def test_targeted_evidence_is_informational_and_never_blocks(tmp_path, targeted):
    """Missing or arbitrary Targeted evidence passes; Files/Commit stay authoritative."""
    _init_repo(tmp_path)
    source_file = tmp_path / "src" / "feature.py"
    source_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    (tmp_path / ".gitignore").write_text("checklist.md\noutput.md\n")
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
        "- Files: `src/feature.py`\n"
        f"- Commit: `{head}`\n" + targeted + "- Remaining work: None.\n- Next action: Review.\n"
    )
    output.write_text(ledger)
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []
    # Files remain authoritative regardless of the Targeted evidence text.
    output.write_text(ledger.replace("`src/feature.py`", "`src/absent.py`"))
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)


@pytest.mark.parametrize("malformed", ["- Status : failed\n", "- Status : failed   \n"])
def test_unindented_malformed_field_after_targeted_evidence_is_rejected(tmp_path, malformed):
    """Evidence mode skips indented continuation only, never a top-level bullet.

    Trailing whitespace is not indentation, so it cannot disguise the bullet as
    continuation of the preceding `Targeted evidence` text.
    """
    _init_repo(tmp_path)
    source_file = tmp_path / "src" / "feature.py"
    source_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    (tmp_path / ".gitignore").write_text("checklist.md\noutput.md\n")
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
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: `src/feature.py`\n"
        f"- Commit: `{head}`\n"
        "- Targeted evidence: note\n"
        # Unindented, so it is a top-level field and stays malformed.
        + malformed
        + "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        "Todo ledger evidence is malformed or duplicated for PLAN-001"
    ]


def test_no_change_evidence_still_requires_a_clean_worktree(tmp_path):
    _init_repo(tmp_path)
    tracked = tmp_path / "README.md"
    tracked.write_text("notes\n")
    (tmp_path / ".gitignore").write_text("checklist.md\noutput.md\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Add readme"], check=True)
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
        "- Targeted evidence: nothing to run\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []
    tracked.write_text("dirty\n")
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    tracked.write_text("notes\n")
    stray = tmp_path / "stray.txt"
    stray.write_text("untracked\n")
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)


def test_files_commit_evidence_requires_a_clean_worktree(tmp_path):
    """A normal Files/Commit ledger must fail when the current worktree is dirty."""
    _init_repo(tmp_path)
    source_file = tmp_path / "src" / "feature.py"
    source_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    (tmp_path / ".gitignore").write_text("checklist.md\noutput.md\n")
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
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: `src/feature.py`\n"
        f"- Commit: `{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []
    source_file.write_text("VALUE = 2\n")
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    source_file.write_text("VALUE = 1\n")
    (tmp_path / "src" / "stray.py").write_text("STRAY = 1\n")
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)


def test_repository_evidence_queries_are_constant_for_many_items(tmp_path):
    _init_repo(tmp_path)
    source_file = tmp_path / "src" / "feature.py"
    test_file = tmp_path / "tests" / "test_feature.py"
    source_file.parent.mkdir()
    test_file.parent.mkdir()
    source_file.write_text("VALUE = 1\n")
    test_file.write_text("def test_value(): assert True\n")
    (tmp_path / ".gitignore").write_text("checklist.md\noutput.md\n")
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
    output.write_text(
        "## Todo Progress\n\n"
        + "\n\n".join(
            f"### {item.item_id}\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n"
            "- Files: `src/feature.py`, `tests/test_feature.py`\n"
            f"- Commit: `{head}`\n"
            "- Targeted evidence: tests passed\n"
            "- Remaining work: None.\n- Next action: Review."
            for item in items
        )
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
    assert calls == 3


def test_repository_evidence_rejects_path_limit_before_git(tmp_path):
    fields = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM + 1)
        ),
        "Commit": f"`{'a' * 40}`",
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set({"PLAN-001": fields}, tmp_path)
    assert errors["PLAN-001"]
    git_call.assert_not_called()


def test_repository_evidence_preflight_failure_rejects_complete_set(tmp_path):
    oversized = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM + 1)
        ),
        "Commit": f"`{'a' * 40}`",
    }
    unchecked_sibling = {
        "Files": "`tests/test_sibling.py`",
        "Commit": f"`{'a' * 40}`",
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set(
            {"PLAN-001": oversized, "PLAN-002": unchecked_sibling},
            tmp_path,
        )
    assert errors["PLAN-001"]
    assert errors["PLAN-002"]
    git_call.assert_not_called()


def test_repository_evidence_rejects_commit_limit_before_git(tmp_path):
    from cafe.utils.checklist_validator import MAX_EVIDENCE_COMMITS_PER_ITEM

    fields = {
        "Files": "`tests/test_feature.py`",
        "Commit": ", ".join(
            f"`{index:040x}`" for index in range(MAX_EVIDENCE_COMMITS_PER_ITEM + 1)
        ),
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set({"PLAN-001": fields}, tmp_path)
    assert errors["PLAN-001"]
    git_call.assert_not_called()


def test_repository_evidence_accepts_exact_cardinality_limits_before_lookup(tmp_path):
    from cafe.utils.checklist_validator import MAX_EVIDENCE_COMMITS_PER_ITEM

    fields = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM)
        ),
        "Commit": ", ".join(f"`{index:040x}`" for index in range(MAX_EVIDENCE_COMMITS_PER_ITEM)),
    }
    failed = subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr="fail")
    with patch("cafe.utils.checklist_validator._git", return_value=failed) as git_call:
        errors = validate_todo_evidence_set({"PLAN-001": fields}, tmp_path)
    assert errors["PLAN-001"]
    assert git_call.call_count == 3


def test_repository_evidence_empty_set_performs_no_queries(tmp_path):
    with patch("cafe.utils.checklist_validator._git") as git_call:
        assert validate_todo_evidence_set({}, tmp_path) == {}
    git_call.assert_not_called()


def test_repository_evidence_git_timeout_fails_closed(tmp_path):
    fields = {
        "Files": "`tests/test_feature.py`",
        "Commit": f"`{'a' * 40}`",
    }
    with patch(
        "cafe.utils.checklist_validator.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 10),
    ):
        errors = validate_todo_evidence_set({"PLAN-001": fields}, tmp_path)
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
