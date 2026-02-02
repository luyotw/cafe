"""Tests for _ensure_default_content function."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.cli import _ensure_default_content


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """Create a temporary .cafe directory."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


class TestEnsureDefaultContent:
    """Tests for _ensure_default_content - copies agents and templates to local .cafe."""

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_calls_copy_functions(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that _ensure_default_content calls both copy functions."""
        mock_copy_agents.return_value = []
        mock_copy_templates.return_value = []

        _ensure_default_content(temp_cafe_dir)

        mock_copy_agents.assert_called_once_with(temp_cafe_dir)
        mock_copy_templates.assert_called_once_with(temp_cafe_dir)

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_creates_agents_and_templates(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that successful copies complete without errors."""
        # Mock copy results
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/developer/David.md", "custom", True),
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        _ensure_default_content(temp_cafe_dir)

        # Function should complete normally without exceptions
        mock_copy_agents.assert_called_once()
        mock_copy_templates.assert_called_once()

    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_handles_partial_failures(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that partial copy failures do not affect subsequent operations."""
        # Mock partial failure
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/pm/Custom.md", "custom", False),  # copy failed
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        # Should not raise exceptions
        _ensure_default_content(temp_cafe_dir)

        mock_copy_agents.assert_called_once()
        mock_copy_templates.assert_called_once()

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_displays_summary_message(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        mock_console: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that _ensure_default_content displays a summary message."""
        # Mock successful copy results
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/developer/David.md", "custom", True),
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", True),
        ]

        _ensure_default_content(temp_cafe_dir)

        # Verify summary message was printed (not individual files)
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "2 agent(s)" in call_args
        assert "1 template(s)" in call_args

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.cli.copy_templates_to_local")
    @patch("cafe.ui.cli.copy_agents_to_local")
    def test_ensure_default_content_displays_warning_for_failures(
        self,
        mock_copy_agents: MagicMock,
        mock_copy_templates: MagicMock,
        mock_console: MagicMock,
        temp_cafe_dir: Path,
    ) -> None:
        """Test that _ensure_default_content displays warning for failed copies."""
        # Mock with some failures
        mock_copy_agents.return_value = [
            ("agents/pm/Roger.md", "system default", True),
            ("agents/pm/Custom.md", "custom", False),  # copy failed
        ]
        mock_copy_templates.return_value = [
            ("templates/plan/default.md", "system default", False),  # copy failed
        ]

        _ensure_default_content(temp_cafe_dir)

        # Verify both success and warning messages were printed
        assert mock_console.print.call_count == 2
        calls = [call[0][0] for call in mock_console.print.call_args_list]
        assert any("1 agent(s)" in call and "0 template(s)" in call for call in calls)
        assert any("Failed to copy 2 file(s)" in call for call in calls)
