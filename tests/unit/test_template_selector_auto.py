"""Tests for template selector with auto option."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cafe.ui.template_selector import select_template


class TestTemplateSelectorWithAuto:
    """Test template selector with auto option."""

    def test_select_template_without_auto(self):
        """Test template selection without auto option."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. default"
            result = select_template(templates, template_paths, include_auto=False)

            assert result == "default"
            mock_prompt.assert_called_once()
            # Verify choices don't include auto
            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            assert all("auto" not in choice for choice in choices)

    def test_select_template_with_auto_option(self):
        """Test template selection with auto option included."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            result = select_template(templates, template_paths, include_auto=True)

            assert result == "auto"
            mock_prompt.assert_called_once()
            # Verify choices include auto as first option
            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            assert choices[0] == "1. auto"

    def test_select_manual_template_with_auto_option(self):
        """Test selecting a manual template when auto option is available."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "3. simple"
            result = select_template(templates, template_paths, include_auto=True)

            assert result == "simple"
            mock_prompt.assert_called_once()
            # Verify auto is first, then templates
            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            assert choices[0] == "1. auto"
            assert "3. simple" in choices

    def test_auto_option_is_first_choice(self):
        """Test that auto option appears as the first choice."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            select_template(templates, template_paths, include_auto=True)

            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            # First choice should be auto
            assert choices[0].endswith("auto")
            # Other choices should be templates
            assert any("default" in choice for choice in choices)
            assert any("simple" in choice for choice in choices)
            assert any("bug" in choice for choice in choices)

    def test_select_template_empty_list_with_auto(self):
        """Test template selection with empty template list but auto enabled."""
        templates = []
        template_paths = {}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            result = select_template(templates, template_paths, include_auto=True)

            assert result == "auto"
            # Should still work with auto option even if no templates

    def test_select_template_empty_list_without_auto(self):
        """Test template selection returns None with empty list and no auto."""
        templates = []
        template_paths = {}

        result = select_template(templates, template_paths, include_auto=False)
        assert result is None

    def test_default_choice_is_auto_when_included(self):
        """Test that default choice is auto when auto option is included."""
        templates = ["default", "simple"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            select_template(templates, template_paths, include_auto=True)

            call_args = mock_prompt.call_args
            # The default should be the first choice (auto)
            default_choice = call_args[1]["default"]
            assert default_choice.endswith("auto")
