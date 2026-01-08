"""Tests for checklist utilities."""

import pytest
from pathlib import Path
from cafe.utils.checklist_utils import (
    extract_checklist_sections,
    resolve_checklist_placeholders,
    generate_checklist_file
)


class TestExtractChecklistSections:
    """Tests for extract_checklist_sections function."""

    def test_extracts_single_checklist_section(self):
        """Test extracting a single checklist section from prompt string."""
        prompt = """
# Some Title

## Execution Steps Checklist

[ ] Step 1
[ ] Step 2
[ ] Step 3

## Other Section

Some other content
"""
        result = extract_checklist_sections(prompt)

        assert "## Execution Steps Checklist" in result
        assert "[ ] Step 1" in result
        assert "[ ] Step 2" in result
        assert "[ ] Step 3" in result
        assert "## Other Section" not in result
        assert "Some other content" not in result

    def test_extracts_multiple_checklist_sections(self):
        """Test extracting multiple checklist sections."""
        prompt = """
## Execution Steps Checklist

[ ] Do this
[ ] Do that

## Important Notes Checklist

[ ] ✅ Remember this
[ ] ✅ Don't forget that

## Regular Section

Not a checklist
"""
        result = extract_checklist_sections(prompt)

        assert "## Execution Steps Checklist" in result
        assert "[ ] Do this" in result
        assert "## Important Notes Checklist" in result
        assert "[ ] ✅ Remember this" in result
        assert "## Regular Section" not in result

    def test_extracts_agent_guidelines_checklist(self):
        """Test extracting agent guidelines checklist."""
        prompt = """
## Agent Guidelines Checklist

[ ] Adhere to coding standards
[ ] Write comments in natural language
[ ] Follow commit message style

## Other Content
"""
        result = extract_checklist_sections(prompt)

        assert "## Agent Guidelines Checklist" in result
        assert "[ ] Adhere to coding standards" in result

    def test_returns_empty_string_when_no_checklists(self):
        """Test returns empty string when no checklist sections found."""
        prompt = """
# Title

Some regular content without checklists.
"""
        result = extract_checklist_sections(prompt)
        assert result == ""

    def test_preserves_checklist_formatting(self):
        """Test preserves markdown formatting in checklists."""
        prompt = """
## Execution Steps Checklist

[ ] Read **bold text** and `code`
[ ] Use {variable} placeholders
[ ] Follow [link](url)
"""
        result = extract_checklist_sections(prompt)

        assert "**bold text**" in result
        assert "`code`" in result
        assert "{variable}" in result
        assert "[link](url)" in result


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
