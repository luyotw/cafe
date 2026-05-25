"""Tests for automatic sync after cafe agent edit."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


class TestAgentEditAutoSync:
    """Tests for automatic sync after agent edit."""

    @patch("cafe.ui.cli.subprocess.run")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.utils.config.get_global_cafe_dir")
    def test_agent_edit_triggers_sync_after_successful_edit(
        self,
        mock_get_global_cafe_dir: MagicMock,
        mock_prompt_list: MagicMock,
        mock_sync_agents: MagicMock,
        mock_subprocess_run: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that agent edit triggers sync after successful edit."""
        # Setup global cafe directory
        global_cafe_dir = tmp_path / ".cafe_global"
        global_cafe_dir.mkdir()
        agents_dir = global_cafe_dir / "agents" / "developer"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "Nick.md"
        agent_file.write_text("test content")

        mock_get_global_cafe_dir.return_value = global_cafe_dir
        mock_prompt_list.side_effect = ["developer", "Nick.md"]
        mock_subprocess_run.return_value = MagicMock(returncode=0)
        mock_sync_agents.return_value = (2, 0)

        # Setup local .cafe directory
        local_cafe_dir = Path(".cafe")
        local_cafe_dir.mkdir(exist_ok=True)

        result = runner.invoke(app, ["agent", "edit"])

        # Verify sync was called after edit
        assert mock_sync_agents.called

    @patch("cafe.ui.cli.subprocess.run")
    @patch("cafe.ui.init_helpers.sync_agents")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.utils.config.get_global_cafe_dir")
    def test_agent_edit_does_not_sync_if_edit_fails(
        self,
        mock_get_global_cafe_dir: MagicMock,
        mock_prompt_list: MagicMock,
        mock_sync_agents: MagicMock,
        mock_subprocess_run: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Test that agent edit does not trigger sync if edit fails."""
        # Setup global cafe directory
        global_cafe_dir = tmp_path / ".cafe_global"
        global_cafe_dir.mkdir()
        agents_dir = global_cafe_dir / "agents" / "developer"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "Nick.md"
        agent_file.write_text("test content")

        mock_get_global_cafe_dir.return_value = global_cafe_dir
        mock_prompt_list.side_effect = ["developer", "Nick.md"]
        # Simulate subprocess failure
        mock_subprocess_run.side_effect = Exception("Editor failed")

        result = runner.invoke(app, ["agent", "edit"])

        # Verify sync was NOT called when edit fails
        assert not mock_sync_agents.called
