"""Unit tests for PR title/body parsing utilities."""

import pytest

from cafe.utils.pr import parse_pr_body, parse_pr_title


class TestParsePRTitle:
    def test_parse_pr_title_basic(self):
        content = "# Add user authentication\n\nSome body content here"
        assert parse_pr_title(content) == "Add user authentication"

    def test_parse_pr_title_with_extra_spaces(self):
        content = "#   Multiple spaces after hash   \n\nBody"
        assert parse_pr_title(content) == "Multiple spaces after hash"

    def test_parse_pr_title_multiple_h1s(self):
        content = "# First Title\n\nSome content\n\n# Second Title\n\nMore content"
        assert parse_pr_title(content) == "First Title"

    def test_parse_pr_title_no_h1_raises_error(self):
        with pytest.raises(ValueError, match="No H1 heading found"):
            parse_pr_title("No H1 heading here\n\nJust plain text")

    def test_parse_pr_title_empty_file_raises_error(self):
        with pytest.raises(ValueError, match="No H1 heading found"):
            parse_pr_title("")

    def test_parse_pr_title_only_h2_raises_error(self):
        with pytest.raises(ValueError, match="No H1 heading found"):
            parse_pr_title("## This is H2\n\nNot H1")

    def test_parse_pr_title_with_whitespace_before(self):
        content = "\n\n# Title with leading newlines\n\nBody"
        assert parse_pr_title(content) == "Title with leading newlines"


class TestParsePRBody:
    def test_parse_pr_body_basic(self):
        content = "# PR Title\n\n## Summary\nThis is the body"
        assert parse_pr_body(content) == "## Summary\nThis is the body"

    def test_parse_pr_body_empty(self):
        assert parse_pr_body("# PR Title\n\n") == ""

    def test_parse_pr_body_only_title(self):
        assert parse_pr_body("# PR Title") == ""

    def test_parse_pr_body_multiline(self):
        content = """# Title

## Summary
First paragraph

Second paragraph

## Changes
- Change 1
- Change 2"""
        expected = """## Summary
First paragraph

Second paragraph

## Changes
- Change 1
- Change 2"""
        assert parse_pr_body(content) == expected

    def test_parse_pr_body_preserves_formatting(self):
        content = "# Title\n\n```python\ncode block\n```\n\n- List item"
        body = parse_pr_body(content)
        assert "```python" in body
        assert "code block" in body
        assert "- List item" in body

    def test_parse_pr_body_no_h1_returns_empty(self):
        assert parse_pr_body("No H1 here\n\nJust content") == ""
