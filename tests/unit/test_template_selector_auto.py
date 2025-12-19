"""Tests for template selector with auto option."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cafe.ui.template_selector import select_template


class TestTemplateSelectorWithAuto:
    """Test template selector with auto option."""

    def test_select_template_includes_auto_by_default(self):
        """Test template selection always includes auto option."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "2. default"
            result = select_template(templates, template_paths)

            assert result == "default"
            mock_prompt.assert_called_once()
            # Verify choices include auto as first option
            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            assert choices[0] == "1. auto"

    def test_select_auto_option(self):
        """Test selecting auto option."""
        templates = ["default", "simple", "bug"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            result = select_template(templates, template_paths)

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
            result = select_template(templates, template_paths)

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
            select_template(templates, template_paths)

            call_args = mock_prompt.call_args
            choices = call_args[0][1]
            # First choice should be auto
            assert choices[0].endswith("auto")
            # Other choices should be templates
            assert any("default" in choice for choice in choices)
            assert any("simple" in choice for choice in choices)
            assert any("bug" in choice for choice in choices)

    def test_select_template_empty_list_returns_none(self):
        """Test template selection returns None with empty template list."""
        templates = []
        template_paths = {}

        result = select_template(templates, template_paths)
        assert result is None

    def test_default_choice_is_auto(self):
        """Test that default choice is auto option."""
        templates = ["default", "simple"]
        template_paths = {name: Path(f"/path/to/{name}.md") for name in templates}

        with patch("cafe.ui.template_selector.prompt_list") as mock_prompt:
            mock_prompt.return_value = "1. auto"
            select_template(templates, template_paths)

            call_args = mock_prompt.call_args
            # The default should be the first choice (auto)
            default_choice = call_args[1]["default"]
            assert default_choice.endswith("auto")
