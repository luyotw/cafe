"""測試 cafe prepare 指令自動初始化功能."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cafe.ui.cli import app


class TestPrepareAutoInitialization:
    """測試 cafe prepare 自動初始化 templates and agents."""

    @pytest.fixture
    def runner(self) -> CliRunner:
        """建立 CLI runner."""
        return CliRunner()

    @pytest.fixture
    def mock_git_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """建立 mock git repository."""
        monkeypatch.chdir(tmp_path)

        # Create .cafe directory with config.yaml (required by prepare command)
        from tests.conftest import create_minimal_config
        create_minimal_config(tmp_path)

        # Create templates and agents at repo root
        (tmp_path / "templates" / "plan").mkdir(parents=True)
        (tmp_path / "templates" / "plan" / "default.md").write_text("# Template")

        (tmp_path / "agents" / "pm").mkdir(parents=True)
        (tmp_path / "agents" / "developer").mkdir(parents=True)
        (tmp_path / "agents" / "reviewer").mkdir(parents=True)
        (tmp_path / "agents" / "pm" / "Roger.md").write_text("# Roger")
        (tmp_path / "agents" / "developer" / "David.md").write_text("# David")
        (tmp_path / "agents" / "reviewer" / "Richard.md").write_text("# Richard")

        return tmp_path

    @patch("cafe.ui.cli.GitOperations")
    # Tests removed: Agents and templates are no longer copied to project .cafe directory
    # They are now managed globally at ~/.cafe/

    @patch("cafe.ui.cli.GitOperations")
    def test_prepare_does_not_overwrite_existing_templates(
        self, mock_git_class: MagicMock, runner: CliRunner, mock_git_repo: Path
    ) -> None:
        """測試當 .cafe/templates 已存在時不覆蓋."""
        mock_git = MagicMock()
        mock_git_class.return_value = mock_git
        mock_git.is_valid_branch.return_value = True
        mock_git.get_current_branch.return_value = "main"
        mock_git.branch_exists.return_value = False
        mock_git.has_uncommitted_changes.return_value = False

        # Create existing .cafe/templates with custom content
        cafe_templates = Path(".cafe/templates/plan")
        cafe_templates.mkdir(parents=True)
        (cafe_templates / "custom.md").write_text("# Custom Template")

        # Run prepare command
        result = runner.invoke(app, ["prepare", "test-issue", "--no-check"])

        assert result.exit_code == 0

        # Verify custom content was not overwritten
        assert (cafe_templates / "custom.md").exists()
        assert (cafe_templates / "custom.md").read_text() == "# Custom Template"

    @patch("cafe.ui.cli.GitOperations")
    def test_prepare_does_not_overwrite_existing_agents(
        self, mock_git_class: MagicMock, runner: CliRunner, mock_git_repo: Path
    ) -> None:
        """測試當 .cafe/agents 已存在時不覆蓋."""
        mock_git = MagicMock()
        mock_git_class.return_value = mock_git
        mock_git.is_valid_branch.return_value = True
        mock_git.get_current_branch.return_value = "main"
        mock_git.branch_exists.return_value = False
        mock_git.has_uncommitted_changes.return_value = False

        # Create existing .cafe/agents with custom content
        cafe_agents = Path(".cafe/agents/pm")
        cafe_agents.mkdir(parents=True)
        (cafe_agents / "CustomPM.md").write_text("# Custom PM")

        # Run prepare command
        result = runner.invoke(app, ["prepare", "test-issue", "--no-check"])

        assert result.exit_code == 0

        # Verify custom content was not overwritten
        assert (cafe_agents / "CustomPM.md").exists()
        assert (cafe_agents / "CustomPM.md").read_text() == "# Custom PM"
