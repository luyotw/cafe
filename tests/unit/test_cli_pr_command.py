"""Tests for PR command CLI interactions."""

from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path, monkeypatch):
    """Create a temporary repository with spec/plan artifacts."""
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)
    with open(cafe_dir / "config.yaml", "w") as f:
        yaml.dump(
            {
                "agents": {
                    "developer": {"name": "David", "cli": "copilot"},
                }
            },
            f,
        )

    issue_dir = cafe_dir / "issues" / "test-issue"
    (issue_dir / "spec" / "iteration_001").mkdir(parents=True)
    (issue_dir / "plan" / "iteration_001").mkdir(parents=True)
    (issue_dir / "spec" / "iteration_001" / "output.md").write_text("Test spec")
    (issue_dir / "plan" / "iteration_001" / "output.md").write_text("Test plan")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mock_git(mock_git_cls):
    mock_git = MagicMock()
    mock_git.get_current_branch.return_value = "test-issue"
    mock_git.is_valid_branch.return_value = True
    mock_git_cls.return_value = mock_git


class TestPRCommand:
    @patch("cafe.ui.cli._execute_single_step_alias")
    @patch("cafe.ui.cli.GitOperations")
    def test_pr_uses_workflow_alias(self, mock_git_cls, mock_execute_alias, temp_repo_dir):
        _mock_git(mock_git_cls)
        mock_execute_alias.return_value = {
            "status_code": "CAFE_CONFIRMED",
            "iterations": 1,
            "output_file": ".cafe/issues/test-issue/pr/iteration_001/output.md",
        }

        result = runner.invoke(app, ["pr"])

        assert result.exit_code == 0
        assert "PR content completed" in result.stdout
        mock_execute_alias.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    def test_pr_rejects_legacy_flags(self, mock_git_cls, temp_repo_dir):
        _mock_git(mock_git_cls)

        result = runner.invoke(app, ["pr", "--title", "My PR"])

        assert result.exit_code == 1
        assert "no longer supports legacy phase options" in result.stdout
        assert "--title" in result.stdout

    @patch("cafe.ui.cli._execute_single_step_alias")
    @patch("cafe.ui.cli.GitOperations")
    def test_pr_routes_needs_changes_to_develop(self, mock_git_cls, mock_execute_alias, temp_repo_dir):
        _mock_git(mock_git_cls)
        mock_execute_alias.return_value = {
            "status_code": "CAFE_NEEDS_CHANGES",
            "iterations": 2,
            "output_file": ".cafe/issues/test-issue/pr/iteration_002/output.md",
        }

        result = runner.invoke(app, ["pr"])

        assert result.exit_code == 0
        assert "CAFE_NEEDS_CHANGES" in result.stdout
        assert "cafe develop" in result.stdout
