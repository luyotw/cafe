"""Unit tests for checklist_validator module."""

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from cafe.core.todo import parse_todo_list
from cafe.utils.checklist_validator import (
    MAX_EVIDENCE_PATHS_PER_ITEM,
    _command_file_arguments,
    _is_test_path,
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


def test_projected_todo_evidence_accepts_non_pytest_runner(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source_file = tmp_path / "src" / "Feature.php"
    test_file = tmp_path / "tests" / "integration" / "FeatureTest.php"
    runner = tmp_path / "vendor" / "bin" / "phpunit"
    source_file.parent.mkdir()
    test_file.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True)
    source_file.write_text("<?php\n", encoding="utf-8")
    test_file.write_text("<?php\n", encoding="utf-8")
    runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runner.chmod(0o755)
    (tmp_path / ".gitignore").write_text(
        "checklist.md\noutput.md\nverification.json\nverification.log\n", encoding="utf-8"
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
    command = [
        "./vendor/bin/phpunit",
        "--no-coverage",
        "tests/integration/FeatureTest.php",
    ]
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: `src/Feature.php`, `tests/integration/FeatureTest.php`\n"
        f"- Commit: `{head}`\n"
        f"- Targeted evidence: command=`{' '.join(command)}`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert (
        run_verification(output_file=output, command=command, scope="targeted", cwd=tmp_path)[0]
        == 0
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_accepts_only_verifiable_no_change_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    test_file = tmp_path / "tests" / "test_noop.py"
    readme_file = tmp_path / "README.md"
    test_file.parent.mkdir()
    test_file.write_text("def test_noop(): assert True\n")
    readme_file.write_text("not a test\n")
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
    unrelated = [sys.executable, "-c", "pass", "README.md"]
    assert (
        run_verification(
            output_file=output,
            command=unrelated,
            scope="targeted",
            cwd=tmp_path,
        )[0]
        == 0
    )
    output.write_text(
        output.read_text().replace(
            "pytest -q tests/test_noop.py::test_noop", " ".join(unrelated)
        )
    )
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    focused = ["pytest", "-q", "tests/test_noop.py::test_noop"]
    assert (
        run_verification(
            output_file=output,
            command=focused,
            scope="targeted",
            cwd=tmp_path,
        )[0]
        == 0
    )
    output.write_text(output.read_text().replace(" ".join(unrelated), " ".join(focused)))
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


def test_repository_evidence_preflight_failure_rejects_complete_set(tmp_path):
    oversized = {
        "Files": ", ".join(
            f"`tests/test_{index}.py`" for index in range(MAX_EVIDENCE_PATHS_PER_ITEM + 1)
        ),
        "Commit": f"`{'a' * 40}`",
        "Targeted evidence": (
            f"command=`pytest -q tests/test_0.py`; exit=0; head=`{'a' * 40}`"
        ),
    }
    unchecked_sibling = {
        "Files": "`tests/test_sibling.py`",
        "Commit": f"`{'a' * 40}`",
        "Targeted evidence": (
            f"command=`pytest -q tests/test_sibling.py`; exit=0; head=`{'a' * 40}`"
        ),
    }
    with patch("cafe.utils.checklist_validator._git") as git_call:
        errors = validate_todo_evidence_set(
            {"PLAN-001": oversized, "PLAN-002": unchecked_sibling},
            tmp_path,
            output_path=tmp_path / "output.md",
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


UNRELATED_ERROR = "Todo ledger targeted evidence is unrelated for PLAN-001"

DEFAULT_WORKSPACE = "packages:\n  - apps/*\n"


def _write_package(tmp_path, directory, name):
    """Create one workspace package with a `src/lib/api.test.ts` module."""
    package_dir = tmp_path / directory
    (package_dir / "src" / "lib").mkdir(parents=True, exist_ok=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    (package_dir / "src" / "lib" / "api.ts").write_text(
        f"export const value = '{name}'\n", encoding="utf-8"
    )
    (package_dir / "src" / "lib" / "api.test.ts").write_text(
        f"test('{name} api', () => {{}})\n", encoding="utf-8"
    )


def _init_monorepo(
    tmp_path,
    *,
    extra_files=(),
    symlinks=(),
    workspace=DEFAULT_WORKSPACE,
    extra_packages=(),
    gitignore_extra=(),
):
    """Create a pnpm workspace with two packages sharing a relative test path.

    `apps/web` and `apps/admin` both contain `src/lib/api.test.ts`, so a correct
    filter must select the package by workspace membership and manifest name,
    never by suffix uniqueness. `extra_packages` are created before the initial
    commit so their files have valid commit membership.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    for directory, name in (("apps/web", "web"), ("apps/admin", "admin"), *extra_packages):
        _write_package(tmp_path, directory, name)
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "root", "version": "1.0.0", "private": True}) + "\n",
        encoding="utf-8",
    )
    if workspace is not None:
        (tmp_path / "pnpm-workspace.yaml").write_text(workspace, encoding="utf-8")
    for relative in extra_files:
        extra = tmp_path / relative
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("test('extra', () => {})\n", encoding="utf-8")
    for link_relative, target in symlinks:
        link = tmp_path / link_relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    # A fake `pnpm` that ignores its arguments; the validator must parse the
    # recorded command rather than observe the process.
    runner = tmp_path / "tools" / "pnpm"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runner.chmod(0o755)
    (tmp_path / ".gitignore").write_text(
        "checklist.md\noutput.md\nverification.json\nverification.log\n"
        + "".join(f"{entry}\n" for entry in gitignore_extra),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Add workspace"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    item = parse_todo_list(
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: x — Closure: y — Evidence: z\n"
    )[0]
    return head, item


def _pnpm_command(package, *test_paths, exec_args=("exec", "vitest", "run")):
    """Build the confirmed pnpm filtered command shape."""
    return ["./tools/pnpm", "--filter", package, *exec_args, *test_paths]


def _write_monorepo_ledger(tmp_path, item, head, command, files):
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        f"- Files: {files}\n"
        f"- Commit: `{head}`\n"
        f"- Targeted evidence: command=`{' '.join(command)}`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert (
        run_verification(output_file=output, command=command, scope="targeted", cwd=tmp_path)[0]
        == 0
    )
    return checklist, output


WEB_FILES = "`apps/web/src/lib/api.ts`, `apps/web/src/lib/api.test.ts`"


def test_projected_todo_evidence_accepts_pnpm_filtered_package_tests(tmp_path):
    """The confirmed pnpm filtered command resolves package-relative tests."""
    head, item = _init_monorepo(tmp_path)
    command = _pnpm_command("web", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_selects_filtered_package_not_unique_suffix(tmp_path):
    """The filter picks the package deterministically even when both packages match.

    `apps/web` and `apps/admin` both hold `src/lib/api.test.ts`, so resolving to
    web here can only come from the `--filter web` manifest lookup.
    """
    head, item = _init_monorepo(tmp_path)
    command = _pnpm_command("admin", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    # The command ran in admin, so web's claimed test is unrelated evidence.
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_accepts_filtered_admin_package_tests(tmp_path):
    """The same relative path resolves to admin when admin is the filter."""
    head, item = _init_monorepo(tmp_path)
    command = _pnpm_command("admin", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/admin/src/lib/api.ts`, `apps/admin/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_rejects_root_relative_path_under_filter(tmp_path):
    """A filtered command must not fall back to the repository root."""
    head, item = _init_monorepo(tmp_path)
    # Repo-root spelling under a filter would resolve to apps/web/apps/web/... .
    command = _pnpm_command("web", "apps/web/src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_root_package_duplicate_under_filter(tmp_path):
    """A duplicate at the repository root must not satisfy a filtered command."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = _pnpm_command("web", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    # The command resolves inside apps/web, so the root duplicate is unrelated.
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize(
    "selector",
    ["nonexistent", "web*", "...web", "./apps/web", "{apps/**}", "@acme/web"],
)
def test_projected_todo_evidence_rejects_unsupported_or_unknown_filters(tmp_path, selector):
    """Globbed, unsupported, or unknown filters fail closed."""
    head, item = _init_monorepo(tmp_path)
    command = _pnpm_command(selector, "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize(
    "head_args",
    [
        ["-F", "web"],
        ["-Fweb"],
        ["--filter-prod", "web"],
        ["--filter-prod=web"],
        ["-C", "apps/web"],
        ["--dir", "apps/web"],
        ["-r"],
        ["--recursive"],
        ["--workspace-root"],
        ["-w"],
        ["--include-workspace-root"],
        ["--filter", "web", "-F", "admin"],
        ["--filter", "web", "--filter", "admin"],
        ["--filter", "web", "--filter-prod", "admin"],
        ["--filter", "web", "-r"],
        ["-r", "--filter", "web"],
        ["--filter=web", "--filter=admin"],
    ],
)
def test_projected_todo_evidence_rejects_unsupported_pre_exec_arguments(tmp_path, head_args):
    """Anything but the exact supported head before `exec` fails closed.

    The claimed file is a real root-relative test, so a parser that ignored these
    forms would fall back to root context and wrongly accept this evidence.
    """
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", *head_args, "exec", "vitest", "run", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_run_subcommand_with_filter(tmp_path):
    """`pnpm run --filter ...` is not modelled and must fail closed."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", "run", "--filter", "web", "test", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_ignores_child_flags_after_exec(tmp_path):
    """Child flags after `exec` never change pnpm's package context.

    pnpm itself is unfiltered here, so the command runs at the repository root and
    the root-relative test is valid evidence.
    """
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = [
        "./tools/pnpm",
        "exec",
        "vitest",
        "run",
        "--filter",
        "web",
        "src/lib/api.test.ts",
    ]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_ignores_child_flags_after_double_dash(tmp_path):
    """An explicit `--` also ends pnpm's own argument list."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", "--", "vitest", "--filter", "web", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_rejects_filter_without_exec(tmp_path):
    """A filtered command with no `exec` subcommand fails closed."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", "--filter", "web", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_accepts_attached_filter_form(tmp_path):
    """`--filter=NAME exec` is the second supported filtered spelling."""
    head, item = _init_monorepo(tmp_path)
    command = ["./tools/pnpm", "--filter=web", "exec", "vitest", "run", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_rejects_ellipsis_selector_syntax(tmp_path):
    """`web...` is pnpm dependent-selector syntax, not a package name.

    The workspace literally contains an included package named `web...` with
    otherwise-valid evidence, so only the selector-syntax rejection can explain
    the failure: a parser that treated the dots as part of the name would bind.
    """
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n",
        extra_packages=(("apps/dependents", "web..."),),
    )
    command = _pnpm_command("web...", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/dependents/src/lib/api.ts`, `apps/dependents/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize("spelling", ["separate", "attached"])
def test_projected_todo_evidence_accepts_package_literally_named_exec(tmp_path, spelling):
    """A package may legitimately be named `exec`; positional parsing handles it."""
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n",
        extra_packages=(("apps/exec", "exec"),),
    )
    if spelling == "separate":
        head_args = ["--filter", "exec"]
    else:
        head_args = ["--filter=exec"]
    command = ["./tools/pnpm", *head_args, "exec", "vitest", "run", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/exec/src/lib/api.ts`, `apps/exec/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_ignores_child_exec_token_after_double_dash(tmp_path):
    """A child `exec` token after `--` must not create a filtered context."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = [
        "./tools/pnpm",
        "run",
        "test",
        "--",
        "--grep",
        "exec",
        "src/lib/api.test.ts",
    ]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


@pytest.mark.parametrize("alias", ["recursive", "multi", "m"])
@pytest.mark.parametrize("leading_boundary", [False, True])
def test_projected_todo_evidence_rejects_word_form_recursive_aliases(
    tmp_path, alias, leading_boundary
):
    """Word-form recursive aliases run across packages, so they are not root.

    The claimed file is a real root-relative test, so an alias that fell through
    to root context would make this evidence wrongly acceptable.
    """
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    prefix = ["--", alias] if leading_boundary else [alias]
    command = ["./tools/pnpm", *prefix, "exec", "vitest", "run", "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize("alias", ["recursive", "multi", "m"])
def test_projected_todo_evidence_allows_alias_word_in_child_argv(tmp_path, alias):
    """An alias word in genuine child argv must not change pnpm's context.

    `pnpm exec` is already a recognised root prefix, so the later token is the
    child runner's argument and carries no dispatch meaning.
    """
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", "exec", "vitest", "run", alias, "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


@pytest.mark.parametrize("alias", ["recursive", "multi", "m"])
def test_projected_todo_evidence_allows_alias_word_after_script_boundary(tmp_path, alias):
    """An alias word after a normal root command boundary stays child argv."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = ["./tools/pnpm", "run", "test", "--", alias, "src/lib/api.test.ts"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_rejects_filter_with_non_positional_exec(tmp_path):
    """A filter whose `exec` is not positionally third fails closed."""
    head, item = _init_monorepo(tmp_path, extra_files=("src/lib/api.test.ts",))
    command = [
        "./tools/pnpm",
        "--filter",
        "web",
        "run",
        "exec",
        "src/lib/api.test.ts",
    ]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`src/lib/api.test.ts`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_ambiguous_duplicate_workspace_names(tmp_path):
    """Two included workspace members claiming one name cannot identify a root.

    Both manifests exist before the initial commit, so every other evidence check
    passes and only the ambiguous filter can explain the rejection.
    """
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n  - vendor/*\n",
        extra_packages=(("vendor/web", "web"),),
    )
    command = _pnpm_command("web", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_ignores_excluded_duplicate_workspace_name(tmp_path):
    """An excluded duplicate must not make the real web package ambiguous."""
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n  - fixtures/*\n  - '!fixtures/web'\n",
        extra_packages=(("fixtures/web", "web"),),
    )
    command = _pnpm_command("web", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


def test_projected_todo_evidence_rejects_dot_segment_under_wildcard(tmp_path):
    """A `*` wildcard must not match a dot-prefixed directory segment."""
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n",
        extra_packages=((("apps/.fixture"), "fixture"),),
    )
    command = _pnpm_command("fixture", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/.fixture/src/lib/api.ts`, `apps/.fixture/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_accepts_literal_dot_segment_entry(tmp_path):
    """An explicit literal entry may still name a dot-prefixed directory."""
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n  - apps/.fixture\n",
        extra_packages=((("apps/.fixture"), "fixture"),),
    )
    command = _pnpm_command("fixture", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/.fixture/src/lib/api.ts`, `apps/.fixture/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


@pytest.mark.parametrize("vendor", ["node_modules", "bower_components"])
def test_projected_todo_evidence_rejects_dependency_tree_packages(tmp_path, vendor):
    """Dependency trees are never workspace members, however they are declared."""
    head, item = _init_monorepo(
        tmp_path,
        workspace=f"packages:\n  - apps/*\n  - {vendor}/*\n",
        extra_packages=((f"{vendor}/vendored", "vendored"),),
    )
    command = _pnpm_command("vendored", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        f"`{vendor}/vendored/src/lib/api.ts`, `{vendor}/vendored/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_non_member_package_context(tmp_path):
    """A package outside the workspace `packages:` globs cannot establish context."""
    head, item = _init_monorepo(
        tmp_path,
        workspace="packages:\n  - apps/*\n",
        extra_packages=(("fixtures/web2", "web2"),),
    )
    command = _pnpm_command("web2", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`fixtures/web2/src/lib/api.ts`, `fixtures/web2/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize(
    "workspace",
    [
        None,
        "packages:\n  - 'apps/**'\n",
        "packages:\n  - '{apps,libs}/*'\n",
        "packages: []\n",
        "packages: apps/*\n",
        "[not, a, mapping]\n",
    ],
)
def test_projected_todo_evidence_rejects_unsupported_workspace_syntax(tmp_path, workspace):
    """Missing or unsupported workspace syntax fails closed for filtered commands."""
    head, item = _init_monorepo(tmp_path, workspace=workspace)
    command = _pnpm_command("web", "src/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_external_symlink_test(tmp_path):
    """A tracked test-named symlink pointing outside the repository is not a test."""
    outside = tmp_path.parent / "outside_api.test.ts"
    outside.write_text("test('outside', () => {})\n", encoding="utf-8")
    head, item = _init_monorepo(
        tmp_path, symlinks=(("apps/web/src/lib/linked.test.ts", outside),)
    )
    command = _pnpm_command("web", "src/lib/linked.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/web/src/lib/api.ts`, `apps/web/src/lib/linked.test.ts`",
    )

    errors = validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    assert UNRELATED_ERROR in errors
    assert "Todo ledger file is missing or untracked for PLAN-001" in errors


def test_projected_todo_evidence_rejects_internal_symlink_test(tmp_path):
    """A tracked symlink to another in-repo source is not an executable test."""
    head, item = _init_monorepo(
        tmp_path, symlinks=(("apps/web/src/lib/alias.test.ts", "api.ts"),)
    )
    command = _pnpm_command("web", "src/lib/alias.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/web/src/lib/api.ts`, `apps/web/src/lib/alias.test.ts`",
    )

    errors = validate_projected_todos(checklist, output, (item,), repo_root=tmp_path)
    assert UNRELATED_ERROR in errors
    assert "Todo ledger file is missing or untracked for PLAN-001" in errors


def test_projected_todo_evidence_rejects_ancestor_symlink_escape(tmp_path):
    """A test reached through a symlinked ancestor directory is not contained."""
    outside = tmp_path.parent / "outside_pkg"
    (outside / "lib").mkdir(parents=True, exist_ok=True)
    (outside / "lib" / "api.test.ts").write_text(
        "test('outside', () => {})\n", encoding="utf-8"
    )
    head, item = _init_monorepo(
        tmp_path, symlinks=(("apps/web/src/linked", outside),)
    )
    command = _pnpm_command("web", "src/linked/lib/api.test.ts")
    checklist, output = _write_monorepo_ledger(
        tmp_path,
        item,
        head,
        command,
        "`apps/web/src/lib/api.ts`, `apps/web/src/lib/api.test.ts`",
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_no_change_evidence_rejects_symlinked_claim(tmp_path):
    """No-change evidence shares the canonical containment checks."""
    outside = tmp_path.parent / "outside_nochange.test.ts"
    outside.write_text("test('outside', () => {})\n", encoding="utf-8")
    head, item = _init_monorepo(
        tmp_path, symlinks=(("apps/web/src/lib/linked.test.ts", outside),)
    )
    command = _pnpm_command("web", "src/lib/linked.test.ts")
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: N/A (no repository changes)\n"
        "- Commit: N/A (no repository changes): documentation only\n"
        f"- Targeted evidence: command=`{' '.join(command)}`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert (
        run_verification(output_file=output, command=command, scope="targeted", cwd=tmp_path)[0]
        == 0
    )

    # The symlinked test cannot supply command evidence even with no claimed files.
    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        # Executable test modules outside root tests/.
        ("apps/web/src/lib/api.test.ts", True),
        ("apps/web/src/lib/api.spec.tsx", True),
        ("pkg/feature_test.go", True),
        ("src/test_feature.py", True),
        ("src/feature_test.py", True),
        ("src/FeatureTest.php", True),
        ("src/feature_spec.rb", True),
        # Root tests/ keeps its historical reach, including JS module suffixes.
        ("tests/test_feature.py", True),
        ("tests/check_feature.py", True),
        ("tests/integration/FeatureTest.php", True),
        ("tests/harness.mjs", True),
        ("tests/harness.cjs", True),
        ("tests/harness.mts", True),
        ("tests/harness.cts", True),
        ("tests/helpers.ts", True),
        # Root tests/ still requires a code extension.
        ("tests/fixtures/data.json", False),
        ("tests/README.md", False),
        # Docs and data that only look test-shaped.
        ("docs/spec/api.yaml", False),
        ("src/api.spec.json", False),
        ("config/test-data.json", False),
        # Helper modules: neither hyphen nor underscore prefixes are JS/TS tests.
        ("src/test-utils.ts", False),
        ("src/test_utils.ts", False),
        ("src/spec-helpers.ts", False),
        # Ordinary sources.
        ("apps/web/src/lib/api.ts", False),
        ("src/latest.ts", False),
        ("src/feature.py", False),
    ],
)
def test_is_test_path_classifies_executable_tests_only(relative, expected):
    """The classifier admits real test modules and rejects lookalikes."""
    assert _is_test_path(relative) is expected


def test_projected_todo_evidence_accepts_explicitly_targeted_root_tests_file(tmp_path):
    """Root tests/ compatibility: `tests/check_feature.py` stays valid evidence."""
    head, item = _init_monorepo(tmp_path, extra_files=("tests/check_feature.py",))
    command = ["./tools/pnpm", "exec", "pytest", "-q", "tests/check_feature.py"]
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, "`tests/check_feature.py`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == []


@pytest.mark.parametrize(
    "relative",
    [
        "docs/spec/api.yaml",
        "src/api.spec.json",
        "config/test-data.json",
        "src/test-utils.ts",
        "src/test_utils.ts",
    ],
)
def test_projected_todo_evidence_rejects_non_executable_test_lookalikes(tmp_path, relative):
    """Docs and data that merely look test-shaped are not targeted test evidence."""
    head, item = _init_monorepo(tmp_path, extra_files=(f"apps/web/{relative}",))
    command = _pnpm_command("web", relative)
    checklist, output = _write_monorepo_ledger(
        tmp_path, item, head, command, f"`apps/web/{relative}`"
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_non_test_command_arguments(tmp_path):
    """A command naming only non-test sources is not targeted test evidence."""
    head, item = _init_monorepo(tmp_path)
    command = _pnpm_command("web", "src/lib/api.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_command_file_arguments_excludes_ignored_untracked_test(tmp_path):
    """An ignored-but-present test is excluded while a tracked sibling resolves.

    Asserting the resolver directly keeps this non-vacuous: the ghost path exists
    on disk and is named by the command, so only the tracked-set lookup excludes
    it, and the tracked sibling in the same command proves resolution still works.
    """
    _init_monorepo(tmp_path, gitignore_extra=("ghost.test.ts",))
    ghost = tmp_path / "apps" / "web" / "src" / "lib" / "ghost.test.ts"
    ghost.write_text("test('ghost', () => {})\n", encoding="utf-8")
    assert ghost.is_file()
    tracked = set(
        subprocess.check_output(
            ["git", "-C", str(tmp_path), "ls-files", "-z"], text=True
        ).split("\0")
    )
    assert "apps/web/src/lib/ghost.test.ts" not in tracked
    command = _pnpm_command("web", "src/lib/ghost.test.ts", "src/lib/api.test.ts")

    resolved = _command_file_arguments(command, tmp_path.resolve(), tracked)

    assert resolved == {"apps/web/src/lib/api.test.ts"}


def test_projected_todo_no_change_evidence_rejects_ignored_untracked_test(tmp_path):
    """No-change evidence cannot rest on an ignored-but-present test file.

    With no claimed files there is no intersection requirement, so accepting the
    ghost path would make validation pass outright.
    """
    head, item = _init_monorepo(tmp_path, gitignore_extra=("ghost.test.ts",))
    ghost = tmp_path / "apps" / "web" / "src" / "lib" / "ghost.test.ts"
    ghost.write_text("test('ghost', () => {})\n", encoding="utf-8")
    command = _pnpm_command("web", "src/lib/ghost.test.ts")
    checklist = tmp_path / "checklist.md"
    output = tmp_path / "output.md"
    checklist.write_text(item.checklist_row().replace("[ ]", "[x]") + "\n")
    output.write_text(
        "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
        f"- Source fingerprint: `{item.fingerprint}`\n"
        "- Files: N/A (no repository changes)\n"
        "- Commit: N/A (no repository changes): documentation only\n"
        f"- Targeted evidence: command=`{' '.join(command)}`; exit=0; head=`{head}`\n"
        "- Remaining work: None.\n- Next action: Review.\n"
    )
    assert (
        run_verification(output_file=output, command=command, scope="targeted", cwd=tmp_path)[0]
        == 0
    )

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


def test_projected_todo_evidence_rejects_unrelated_monorepo_tests(tmp_path):
    """Claimed tests must intersect the tests the recorded command actually named."""
    head, item = _init_monorepo(tmp_path, extra_files=("apps/web/src/lib/other.test.ts",))
    command = _pnpm_command("web", "src/lib/other.test.ts")
    checklist, output = _write_monorepo_ledger(tmp_path, item, head, command, WEB_FILES)

    assert validate_projected_todos(checklist, output, (item,), repo_root=tmp_path) == [
        UNRELATED_ERROR
    ]


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
