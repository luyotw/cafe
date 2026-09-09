"""Tests for checklist utilities."""

import os

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
            "iteration": "1"
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
