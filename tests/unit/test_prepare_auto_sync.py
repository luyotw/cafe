"""Tests for automatic sync in cafe prepare command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


class TestPrepareAutoSync:
    """Tests for automatic sync at beginning of cafe prepare."""

    @patch("cafe.ui.init_helpers.sync_templates")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.Path")
    def test_prepare_triggers_sync_at_beginning(
        self,
        mock_path_cls: MagicMock,
        mock_sync_agents: MagicMock,
        mock_sync_templates: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Prepare refreshes templates without materializing agent snapshots."""
        # Setup .cafe/config.yaml
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)
        config_file = cafe_dir / "config.yaml"
        config_file.write_text("project: test")

        # Mock Path to return our temp directory for .cafe checks
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        # Setup mock returns for sync functions
        mock_sync_agents.return_value = (2, 0)
        mock_sync_templates.return_value = (3, 0)

        # Run prepare with minimal args to avoid going through the full flow
        # We just want to verify sync is called early
        with patch("cafe.ui.cli.prompt_text", return_value="test-issue"):
            with patch("cafe.ui.cli.GitOperations") as mock_git_ops:
                mock_git_ops.return_value.get_current_branch.return_value = "main"
                mock_git_ops.return_value.branch_exists.return_value = False

                # Mock other prepare dependencies to avoid full execution
                with patch("cafe.ui.cli._ensure_default_content"):
                    with patch("cafe.ui.cli.console"):
                        result = runner.invoke(app, ["prepare"], input="test-issue\n")

        assert result.exit_code in {0, 1}
        mock_sync_agents.assert_not_called()
        assert mock_sync_templates.called, "sync_templates should be called during prepare"

    @patch("cafe.ui.cli.console")
    @patch("cafe.ui.init_helpers.sync_templates")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.Path")
    def test_prepare_displays_sync_summary(
        self,
        mock_path_cls: MagicMock,
        mock_sync_agents: MagicMock,
        mock_sync_templates: MagicMock,
        mock_console: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Prepare reports template refresh without an agent-copy summary."""
        # Setup .cafe/config.yaml
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir(parents=True)
        config_file = cafe_dir / "config.yaml"
        config_file.write_text("project: test")

        # Mock Path to return our temp directory
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        # Setup mock returns for sync functions
        mock_sync_agents.return_value = (2, 0)
        mock_sync_templates.return_value = (3, 0)

        # Run prepare
        with patch("cafe.ui.cli.prompt_text", return_value="test-issue"):
            with patch("cafe.ui.cli.GitOperations") as mock_git_ops:
                mock_git_ops.return_value.get_current_branch.return_value = "main"
                mock_git_ops.return_value.branch_exists.return_value = False

                with patch("cafe.ui.cli._ensure_default_content"):
                    result = runner.invoke(app, ["prepare"], input="test-issue\n")

        assert mock_console.print.called
        call_args = str(mock_console.print.call_args_list)
        assert "3 template(s)" in call_args
        assert "agent(s)" not in call_args
        mock_sync_agents.assert_not_called()
