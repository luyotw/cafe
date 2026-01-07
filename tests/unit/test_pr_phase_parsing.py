"""Test PR phase parsing methods for output.md format"""

import pytest
from cafe.phases.pr_phase import PRPhase


class TestParsePRTitle:
    """Test _parse_pr_title() method"""

    def test_parse_pr_title_basic(self):
        """Test extracting PR title from basic H1 heading"""
        content = "# Add user authentication\n\nSome body content here"
        title = PRPhase._parse_pr_title(content)
        assert title == "Add user authentication"

    def test_parse_pr_title_with_extra_spaces(self):
        """Test parsing title with extra spaces"""
        content = "#   Multiple spaces after hash   \n\nBody"
        title = PRPhase._parse_pr_title(content)
        assert title == "Multiple spaces after hash"

    def test_parse_pr_title_multiple_h1s(self):
        """Test that only first H1 is used as title"""
        content = "# First Title\n\nSome content\n\n# Second Title\n\nMore content"
        title = PRPhase._parse_pr_title(content)
        assert title == "First Title"

    def test_parse_pr_title_no_h1_raises_error(self):
        """Test that missing H1 raises ValueError"""
        content = "No H1 heading here\n\nJust plain text"
        with pytest.raises(ValueError, match="No H1 heading found"):
            PRPhase._parse_pr_title(content)

    def test_parse_pr_title_empty_file_raises_error(self):
        """Test that empty file raises ValueError"""
        content = ""
        with pytest.raises(ValueError, match="No H1 heading found"):
            PRPhase._parse_pr_title(content)

    def test_parse_pr_title_only_h2_raises_error(self):
        """Test that H2 without H1 raises ValueError"""
        content = "## This is H2\n\nNot H1"
        with pytest.raises(ValueError, match="No H1 heading found"):
            PRPhase._parse_pr_title(content)

    def test_parse_pr_title_with_whitespace_before(self):
        """Test parsing title with leading whitespace lines"""
        content = "\n\n# Title with leading newlines\n\nBody"
        title = PRPhase._parse_pr_title(content)
        assert title == "Title with leading newlines"


class TestParsePRBody:
    """Test _parse_pr_body() method"""

    def test_parse_pr_body_basic(self):
        """Test extracting body content after H1"""
        content = "# PR Title\n\n## Summary\nThis is the body"
        body = PRPhase._parse_pr_body(content)
        assert body == "## Summary\nThis is the body"

    def test_parse_pr_body_empty(self):
        """Test parsing when there's no content after H1"""
        content = "# PR Title\n\n"
        body = PRPhase._parse_pr_body(content)
        assert body == ""

    def test_parse_pr_body_only_title(self):
        """Test parsing when file only has title"""
        content = "# PR Title"
        body = PRPhase._parse_pr_body(content)
        assert body == ""

    def test_parse_pr_body_multiline(self):
        """Test parsing body with multiple lines"""
        content = """# Title

## Summary
First paragraph

Second paragraph

## Changes
- Change 1
- Change 2"""
        body = PRPhase._parse_pr_body(content)
        expected = """## Summary
First paragraph

Second paragraph

## Changes
- Change 1
- Change 2"""
        assert body == expected

    def test_parse_pr_body_preserves_formatting(self):
        """Test that body formatting is preserved"""
        content = "# Title\n\n```python\ncode block\n```\n\n- List item"
        body = PRPhase._parse_pr_body(content)
        assert "```python" in body
        assert "code block" in body
        assert "- List item" in body

    def test_parse_pr_body_no_h1_returns_empty(self):
        """Test that missing H1 returns empty string"""
        content = "No H1 here\n\nJust content"
        body = PRPhase._parse_pr_body(content)
        assert body == ""
