"""Tests for prompt utilities."""

import pytest
from pathlib import Path
from cafe.utils.prompt_utils import extract_agent_guidelines_checklist


class TestExtractAgentGuidelinesChecklist:
    """Tests for extract_agent_guidelines_checklist function."""

    def test_extracts_bullet_points_from_agent_file(self, tmp_path):
        """Test extracting bullet points and converting to checklist."""
        # Create test agent file
        agent_file = tmp_path / "test_agent.md"
        agent_file.write_text("""---
name: TestAgent
description: Test agent
---

You are a test agent. Your guidelines:

- **Guideline 1**: Do this thing
- **Guideline 2**: Do that thing
- **Guideline 3**: Follow this rule
""")

        result = extract_agent_guidelines_checklist(str(agent_file))

        assert "## Agent Guidelines Checklist" in result
        assert "[ ] **Guideline 1**: Do this thing" in result
        assert "[ ] **Guideline 2**: Do that thing" in result
        assert "[ ] **Guideline 3**: Follow this rule" in result

    def test_returns_empty_string_for_nonexistent_file(self):
        """Test returns empty string when file doesn't exist."""
        result = extract_agent_guidelines_checklist("/nonexistent/path/file.md")
        assert result == ""

    def test_returns_empty_string_for_file_without_bullet_points(self, tmp_path):
        """Test returns empty string when file has no bullet points."""
        agent_file = tmp_path / "no_bullets.md"
        agent_file.write_text("""---
name: TestAgent
---

This file has no bullet points.
Just regular text.
""")

        result = extract_agent_guidelines_checklist(str(agent_file))
        assert result == ""

    def test_handles_bullet_points_with_varying_indentation(self, tmp_path):
        """Test handles bullet points with different indentation levels."""
        agent_file = tmp_path / "indented.md"
        agent_file.write_text("""
- First guideline
  - This is indented (should not be extracted)
- Second guideline
- Third guideline
""")

        result = extract_agent_guidelines_checklist(str(agent_file))

        assert "[ ] First guideline" in result
        assert "[ ] Second guideline" in result
        assert "[ ] Third guideline" in result
        # Indented lines starting with "- " should still be extracted
        # because we check stripped lines

    def test_preserves_formatting_in_bullet_text(self, tmp_path):
        """Test preserves formatting like bold, italics in bullet text."""
        agent_file = tmp_path / "formatted.md"
        agent_file.write_text("""
- **Bold text** with regular text
- *Italic text* in guideline
- Text with `code` snippet
""")

        result = extract_agent_guidelines_checklist(str(agent_file))

        assert "[ ] **Bold text** with regular text" in result
        assert "[ ] *Italic text* in guideline" in result
        assert "[ ] Text with `code` snippet" in result

    def test_handles_empty_file(self, tmp_path):
        """Test handles empty file gracefully."""
        agent_file = tmp_path / "empty.md"
        agent_file.write_text("")

        result = extract_agent_guidelines_checklist(str(agent_file))
        assert result == ""

    def test_real_agent_file_format(self, tmp_path):
        """Test with realistic agent file format like Roger.md."""
        agent_file = tmp_path / "realistic.md"
        agent_file.write_text("""---
name: Roger
description: Experienced Product Manager
---

You are an experienced Product Manager. Your behavioral guidelines are as follows:

- **Focus on the Requirement**: Do not jump into discussions about technical implementation.
- **User Perspective**: Think about functions and scenarios from the user's point of view.
- **Clear Communication**: Ask questions in a simple and direct manner.
""")

        result = extract_agent_guidelines_checklist(str(agent_file))

        assert "## Agent Guidelines Checklist" in result
        assert "[ ] **Focus on the Requirement**: Do not jump into discussions about technical implementation." in result
        assert "[ ] **User Perspective**: Think about functions and scenarios from the user's point of view." in result
        assert "[ ] **Clear Communication**: Ask questions in a simple and direct manner." in result

    def test_only_extracts_lines_starting_with_dash_space(self, tmp_path):
        """Test only extracts lines that start with '- ' (dash followed by space)."""
        agent_file = tmp_path / "mixed.md"
        agent_file.write_text("""
- This should be extracted
-This should NOT be extracted (no space after dash)
* This should NOT be extracted (different bullet)
+ This should NOT be extracted (different bullet)
- This should be extracted too
""")

        result = extract_agent_guidelines_checklist(str(agent_file))

        assert "[ ] This should be extracted" in result
        assert "[ ] This should be extracted too" in result
        assert "This should NOT be extracted" not in result
