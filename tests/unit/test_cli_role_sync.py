"""Tests for cafe role sync command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_cafe_dir(tmp_path):
    """Create a temporary .cafe directory."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    return cafe_dir


class TestAgentSyncCommand:
    """Tests for cafe role sync command."""

    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.Path")
    def test_agent_sync_calls_sync_function(
        self,
        mock_path_cls: MagicMock,
        mock_sync_agents: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that role sync command calls sync_agents function."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        mock_sync_agents.return_value = (2, 0)

        result = runner.invoke(app, ["role", "sync"])

        assert result.exit_code == 0
        mock_sync_agents.assert_called_once()

    @patch("cafe.ui.cli.Path")
    def test_agent_sync_error_when_cafe_not_initialized(
        self,
        mock_path_cls: MagicMock,
        runner: CliRunner,
    ) -> None:
        """Test that role sync shows error when .cafe directory doesn't exist."""
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        result = runner.invoke(app, ["role", "sync"])

        assert result.exit_code == 1
        assert "not initialized" in result.stdout or "Error" in result.stdout

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.Path")
    def test_agent_sync_displays_success_message(
        self,
        mock_path_cls: MagicMock,
        mock_sync_agents: MagicMock,
        mock_console: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that role sync displays success message with counts."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        mock_sync_agents.return_value = (3, 0)

        result = runner.invoke(app, ["role", "sync"])

        assert result.exit_code == 0
        # Check that console.print was called with a message containing agent count
        assert mock_console.print.called
        call_args = str(mock_console.print.call_args_list)
        assert "3" in call_args or "agent" in call_args

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.Path")
    def test_agent_sync_displays_warning_on_failures(
        self,
        mock_path_cls: MagicMock,
        mock_sync_agents: MagicMock,
        mock_console: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that role sync displays warning when some copies fail."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        mock_sync_agents.return_value = (2, 1)  # 2 success, 1 failure

        result = runner.invoke(app, ["role", "sync"])

        assert result.exit_code == 0
        # Check that warning was displayed
        assert mock_console.print.call_count >= 1
