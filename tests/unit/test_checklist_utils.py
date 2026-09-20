"""Tests for checklist utilities."""

import os
import subprocess
from unittest.mock import patch

import pytest

from cafe.utils.checklist_utils import (
    generate_checklist_file,
    resolve_checklist_placeholders,
)


class TestResolveChecklistPlaceholders:
    """Tests for resolve_checklist_placeholders function."""

    def test_resolves_single_placeholder(self):
        """Test resolving a single placeholder."""
        checklist = "[ ] Read {agent_file} carefully"
        placeholders = {"agent_file": ".cafe/agents/developer/Nick.md"}

        result = resolve_checklist_placeholders(checklist, placeholders)

        assert "{agent_file}" not in result
        assert ".cafe/agents/developer/Nick.md" in result

    def test_resolves_multiple_placeholders(self):
        """Test resolving multiple placeholders."""
        checklist = """
[ ] Read {spec_file_path} and {plan_file_path}
[ ] Check {agent_file} for guidelines
[ ] Iteration: {iteration}
"""
        placeholders = {
            "spec_file_path": ".cafe/issues/issue1/spec/iteration_001/output.md",
            "plan_file_path": ".cafe/issues/issue1/plan/iteration_001/output.md",
            "agent_file": ".cafe/agents/developer/David.md",
            "iteration": "1",
        }

        result = resolve_checklist_placeholders(checklist, placeholders)

        assert "{spec_file_path}" not in result
        assert "{plan_file_path}" not in result
        assert "{agent_file}" not in result
        assert "{iteration}" not in result
        assert ".cafe/issues/issue1/spec/iteration_001/output.md" in result
        assert ".cafe/issues/issue1/plan/iteration_001/output.md" in result
        assert ".cafe/agents/developer/David.md" in result
        assert "Iteration: 1" in result

    def test_handles_missing_placeholders_gracefully(self):
        """Test handles missing placeholders by leaving them unchanged."""
        checklist = "[ ] Read {agent_file} and {unknown_var}"
        placeholders = {"agent_file": ".cafe/agents/developer/Nick.md"}

        result = resolve_checklist_placeholders(checklist, placeholders)

        assert ".cafe/agents/developer/Nick.md" in result
        assert "{unknown_var}" in result  # Should remain unchanged

    def test_handles_empty_placeholders_dict(self):
        """Test handles empty placeholders dict."""
        checklist = "[ ] Read {agent_file}"
        placeholders = {}

        result = resolve_checklist_placeholders(checklist, placeholders)

        assert result == checklist  # Should remain unchanged

    def test_coerces_non_string_runtime_context_values(self):
        """Runtime context may include lifecycle flags alongside file paths."""
        result = resolve_checklist_placeholders(
            "[ ] Publish completed: {published}", {"published": True}
        )

        assert result == "[ ] Publish completed: True"

    def test_resolves_placeholders_with_special_characters(self):
        """Test resolves placeholders in paths with special characters."""
        checklist = "[ ] Read {file_path}"
        placeholders = {"file_path": ".cafe/issues/feature-123/spec/iteration_001/output.md"}

        result = resolve_checklist_placeholders(checklist, placeholders)

        assert ".cafe/issues/feature-123/spec/iteration_001/output.md" in result


class TestGenerateChecklistFile:
    """Tests for generate_checklist_file function."""

    def test_generates_checklist_file_with_content(self, tmp_path):
        """Test generates checklist file with resolved content."""
        output_path = tmp_path / "checklist.md"
        checklist_content = """## Execution Steps Checklist

[ ] Step 1
[ ] Step 2
"""

        generate_checklist_file(str(output_path), checklist_content)

        assert output_path.exists()
        content = output_path.read_text()
        assert "## Execution Steps Checklist" in content
        assert "[ ] Step 1" in content
        assert "[ ] Step 2" in content

    def test_creates_parent_directories_if_needed(self, tmp_path):
        """Test creates parent directories if they don't exist."""
        output_path = tmp_path / "nested" / "dir" / "checklist.md"
        checklist_content = "[ ] Test content"

        generate_checklist_file(str(output_path), checklist_content)

        assert output_path.exists()
        assert output_path.read_text() == checklist_content

    def test_overwrites_existing_file(self, tmp_path):
        """Test overwrites existing file."""
        output_path = tmp_path / "checklist.md"
        output_path.write_text("Old content")

        new_content = "[ ] New content"
        generate_checklist_file(str(output_path), new_content)

        assert output_path.read_text() == new_content

    def test_preserves_completed_items_only_when_their_text_is_unchanged(self, tmp_path):
        output_path = tmp_path / "checklist.md"
        output_path.write_text(
            "[x] Keep this completion\n[x] Replace this requirement\n",
            encoding="utf-8",
        )

        generate_checklist_file(
            output_path,
            "[ ] Keep this completion\n[ ] New requirement\n",
            preserve_completed_items=True,
        )

        assert output_path.read_text(encoding="utf-8") == (
            "[x] Keep this completion\n[ ] New requirement\n"
        )

    def test_preserves_each_prior_completion_at_most_once(self, tmp_path):
        output_path = tmp_path / "checklist.md"
        output_path.write_text("[x] Verify output\n", encoding="utf-8")

        generate_checklist_file(
            output_path,
            "[ ] Verify output\n[ ] Verify output\n",
            preserve_completed_items=True,
        )

        assert output_path.read_text(encoding="utf-8") == ("[x] Verify output\n[ ] Verify output\n")

    def test_reopens_completed_item_when_its_nested_rule_changes(self, tmp_path):
        output_path = tmp_path / "checklist.md"
        output_path.write_text(
            "[x] Validate consumer review:\n  - previous receipt rule\n",
            encoding="utf-8",
        )

        generate_checklist_file(
            output_path,
            "[ ] Validate consumer review:\n  - new checkpoint rule\n",
            preserve_completed_items=True,
        )

        assert output_path.read_text(encoding="utf-8") == (
            "[ ] Validate consumer review:\n  - new checkpoint rule\n"
        )

    @pytest.mark.parametrize(
        "targeted",
        [
            "- Targeted evidence: tests passed\n",
            # Informational text never blocks resume: duplicates and indented
            # continuation under it are skipped like the field itself.
            "- Targeted evidence: web green\n- Targeted evidence: api green\n",
            "- Targeted evidence: e2e run\n  - Browser: Chromium\n",
            "",
        ],
    )
    def test_projected_completion_is_preserved_only_with_current_ledger_evidence(
        self, tmp_path, targeted
    ):
        from cafe.core.todo import parse_todo_list

        item = parse_todo_list(
            "## Todo List\n"
            "- [ ] `PLAN-001` — Source: `plan` — Work: implement — "
            "Closure: complete — Evidence: tests\n"
        )[0]
        output_path = tmp_path / "checklist.md"
        ledger_path = tmp_path / "output.md"
        row = item.checklist_row()
        output_path.write_text(row.replace("[ ]", "[x]") + "\n", encoding="utf-8")
        ledger_path.write_text(
            "## Todo Progress\n\n### PLAN-001\n\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n- Files: a.py\n"
            "- Commit: abc\n" + targeted + "- Remaining work: None.\n- Next action: Review.\n",
            encoding="utf-8",
        )

        repository = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=str(tmp_path), stderr=""
        )
        with (
            patch("cafe.utils.checklist_utils.subprocess.run", return_value=repository),
            patch("cafe.utils.checklist_utils.validate_todo_evidence_set", return_value={}),
        ):
            generate_checklist_file(
                output_path,
                row + "\n",
                preserve_completed_items=True,
                todo_ledger_path=ledger_path,
            )
        assert output_path.read_text(encoding="utf-8").startswith("[x]")

        ledger_path.write_text("## Todo Progress\n", encoding="utf-8")
        generate_checklist_file(
            output_path,
            row + "\n",
            preserve_completed_items=True,
            todo_ledger_path=ledger_path,
        )
        assert output_path.read_text(encoding="utf-8").startswith("[ ]")

    @pytest.mark.parametrize("malformed", ["- Status : failed\n", "- Status : failed   \n"])
    def test_unindented_malformed_field_after_targeted_evidence_drops_completion(
        self, tmp_path, malformed
    ):
        """Evidence mode skips indented continuation only, never a top-level bullet.

        Trailing whitespace is not indentation, so it cannot disguise the bullet
        as continuation of the preceding `Targeted evidence` text.
        """
        from cafe.core.todo import parse_todo_list

        item = parse_todo_list(
            "## Todo List\n"
            "- [ ] `PLAN-001` — Source: `plan` — Work: implement — "
            "Closure: complete — Evidence: tests\n"
        )[0]
        output_path = tmp_path / "checklist.md"
        ledger_path = tmp_path / "output.md"
        row = item.checklist_row()
        output_path.write_text(row.replace("[ ]", "[x]") + "\n", encoding="utf-8")
        ledger_path.write_text(
            "## Todo Progress\n\n### PLAN-001\n\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n- Files: a.py\n"
            "- Commit: abc\n- Targeted evidence: note\n"
            # Unindented, so it is a top-level field and breaks the field set.
            + malformed
            + "- Remaining work: None.\n- Next action: Review.\n",
            encoding="utf-8",
        )

        repository = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=str(tmp_path), stderr=""
        )
        with (
            patch("cafe.utils.checklist_utils.subprocess.run", return_value=repository),
            patch("cafe.utils.checklist_utils.validate_todo_evidence_set", return_value={}),
        ):
            generate_checklist_file(
                output_path,
                row + "\n",
                preserve_completed_items=True,
                todo_ledger_path=ledger_path,
            )
        assert output_path.read_text(encoding="utf-8").startswith("[ ]")

    @pytest.mark.parametrize(
        "repository_result",
        [
            subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr="fail"),
            subprocess.TimeoutExpired("git", 10),
        ],
    )
    def test_projected_resume_fails_closed_when_repository_discovery_fails(
        self, tmp_path, repository_result
    ):
        from cafe.core.todo import parse_todo_list

        item = parse_todo_list(
            "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: implement — "
            "Closure: done — Evidence: test\n"
        )[0]
        checklist = tmp_path / "checklist.md"
        ledger = tmp_path / "output.md"
        row = item.checklist_row()
        checklist.write_text(row.replace("[ ]", "[x]") + "\n", encoding="utf-8")
        ledger.write_text(
            "## Todo Progress\n\n### PLAN-001\n- Status: completed\n"
            f"- Source fingerprint: `{item.fingerprint}`\n"
            "- Files: `tests/test_feature.py`\n"
            f"- Commit: `{'a' * 40}`\n"
            f"- Targeted evidence: command=`pytest -q tests/test_feature.py`; exit=0; "
            f"head=`{'a' * 40}`\n"
            "- Remaining work: None.\n- Next action: Review.\n",
            encoding="utf-8",
        )
        effect = (
            repository_result
            if isinstance(repository_result, BaseException)
            else None
        )
        with patch(
            "cafe.utils.checklist_utils.subprocess.run",
            return_value=None if effect else repository_result,
            side_effect=effect,
        ) as run:
            generate_checklist_file(
                checklist,
                row + "\n",
                preserve_completed_items=True,
                todo_ledger_path=ledger,
            )
        assert checklist.read_text(encoding="utf-8").startswith("[ ]")
        assert run.call_args.kwargs["timeout"] == 10

    def test_projected_resume_reopens_complete_set_on_one_preflight_error(self, tmp_path):
        from cafe.core.todo import parse_todo_list
        from cafe.utils.checklist_validator import MAX_EVIDENCE_PATHS_PER_ITEM

        items = parse_todo_list(
            "## Todo List\n"
            "- [ ] `PLAN-001` — Source: `plan` — Work: first — Closure: done — Evidence: test\n"
            "- [ ] `PLAN-002` — Source: `plan` — Work: second — Closure: done — Evidence: test\n"
        )
        checklist = tmp_path / "checklist.md"
        ledger = tmp_path / "output.md"
        rows = [item.checklist_row() for item in items]
        checklist.write_text(
            "\n".join(row.replace("[ ]", "[x]") for row in rows) + "\n",
            encoding="utf-8",
        )
        evidence = []
        for index, item in enumerate(items):
            files = (
                ", ".join(
                    f"`tests/test_{path_index}.py`"
                    for path_index in range(MAX_EVIDENCE_PATHS_PER_ITEM + 1)
                )
                if index == 0
                else "`tests/test_sibling.py`"
            )
            evidence.append(
                f"### {item.item_id}\n- Status: completed\n"
                f"- Source fingerprint: `{item.fingerprint}`\n"
                f"- Files: {files}\n"
                f"- Commit: `{'a' * 40}`\n"
                "- Targeted evidence: command=`pytest -q tests/test_sibling.py`; "
                f"exit=0; head=`{'a' * 40}`\n"
                "- Remaining work: None.\n- Next action: Review."
            )
        ledger.write_text(
            "## Todo Progress\n\n" + "\n\n".join(evidence), encoding="utf-8"
        )
        repository = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=str(tmp_path), stderr=""
        )
        with (
            patch("cafe.utils.checklist_utils.subprocess.run", return_value=repository),
            patch("cafe.utils.checklist_validator._git") as git_call,
        ):
            generate_checklist_file(
                checklist,
                "\n".join(rows) + "\n",
                preserve_completed_items=True,
                todo_ledger_path=ledger,
            )
        assert checklist.read_text(encoding="utf-8").count("[ ]") == 2
        git_call.assert_not_called()

    def test_projected_resume_without_evidence_performs_no_repository_query(self, tmp_path):
        checklist = tmp_path / "checklist.md"
        ledger = tmp_path / "output.md"
        checklist.write_text("[x] Ordinary gate\n", encoding="utf-8")
        ledger.write_text("## Todo Progress\n", encoding="utf-8")
        with patch("cafe.utils.checklist_utils.subprocess.run") as run:
            generate_checklist_file(
                checklist,
                "[ ] Ordinary gate\n",
                preserve_completed_items=True,
                todo_ledger_path=ledger,
            )
        run.assert_not_called()
        assert checklist.read_text(encoding="utf-8") == "[x] Ordinary gate\n"

    def test_rejects_symlink_without_touching_its_target(self, tmp_path):
        victim = tmp_path / "victim.md"
        victim.write_text("do not overwrite\n", encoding="utf-8")
        output_path = tmp_path / "checklist.md"
        output_path.symlink_to(victim)

        with pytest.raises(ValueError, match="single-link regular file"):
            generate_checklist_file(output_path, "[ ] replacement\n")

        assert output_path.is_symlink()
        assert victim.read_text(encoding="utf-8") == "do not overwrite\n"

    def test_rejects_hardlink_without_touching_its_target(self, tmp_path):
        victim = tmp_path / "victim.md"
        victim.write_text("do not overwrite\n", encoding="utf-8")
        output_path = tmp_path / "checklist.md"
        os.link(victim, output_path)

        with pytest.raises(ValueError, match="single-link regular file"):
            generate_checklist_file(output_path, "[ ] replacement\n")

        assert victim.read_text(encoding="utf-8") == "do not overwrite\n"

    def test_failed_atomic_replacement_keeps_existing_checklist_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        output_path = tmp_path / "checklist.md"
        output_path.write_text("[x] existing\n", encoding="utf-8")
        monkeypatch.setattr(
            "cafe.utils.checklist_utils.os.replace",
            lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
        )

        with pytest.raises(OSError, match="replace failed"):
            generate_checklist_file(output_path, "[ ] replacement\n")

        assert output_path.read_text(encoding="utf-8") == "[x] existing\n"
        assert list(tmp_path.glob(".checklist.md.*.tmp")) == []

    def test_handles_empty_content(self, tmp_path):
        """Test handles empty content."""
        output_path = tmp_path / "checklist.md"

        generate_checklist_file(str(output_path), "")

        assert output_path.exists()
        assert output_path.read_text() == ""

    def test_handles_path_object(self, tmp_path):
        """Test handles Path object as input."""
        output_path = tmp_path / "checklist.md"
        checklist_content = "[ ] Test"

        generate_checklist_file(output_path, checklist_content)

        assert output_path.exists()
        assert output_path.read_text() == checklist_content
