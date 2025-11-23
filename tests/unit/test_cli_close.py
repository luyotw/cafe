"""Tests for close CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.git import GitError
from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path, monkeypatch):
    """Create a temporary git repository directory."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Create .cafe directory
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)

    return tmp_path


@pytest.fixture
def mock_git_ops():
    """Create a mock GitOperations instance."""
    with patch('cafe.ui.cli.GitOperations') as MockGitOperations:
        mock_git = MagicMock()
        MockGitOperations.return_value = mock_git

        # Default behaviors
        mock_git.get_current_branch.return_value = "test-issue"
        mock_git.checkout_branch.return_value = None
        mock_git.delete_branch.return_value = None
        mock_git.pull.return_value = None

        yield mock_git


@pytest.fixture
def issue_with_config(temp_repo_dir):
    """Create an issue directory with config.yaml."""
    issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    config_file = issue_dir / "config.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-issue"
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    return issue_dir


class TestCloseCommand:
    """Test close command."""

    def test_close_success(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試成功執行 close 指令（AC-1）"""
        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Switching to base branch: main" in result.stdout
        assert "Deleting feature branch: test-issue" in result.stdout
        assert "Updating base branch" in result.stdout
        assert "Successfully closed issue: test-issue" in result.stdout

        # Verify git operations called in correct order
        mock_git_ops.get_current_branch.assert_called()
        mock_git_ops.checkout_branch.assert_called_once_with("main")
        mock_git_ops.delete_branch.assert_called_once_with("test-issue")
        mock_git_ops.pull.assert_called_once()

        # Verify issue directory still exists
        assert issue_with_config.exists()

    def test_close_checkout_fails(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試切換分支失敗（AC-2）"""
        mock_git_ops.checkout_branch.side_effect = GitError("Cannot checkout branch")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "Cannot checkout branch" in result.stdout

        # Verify subsequent operations not called
        mock_git_ops.delete_branch.assert_not_called()
        mock_git_ops.pull.assert_not_called()

    def test_close_delete_branch_fails(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試刪除分支失敗（AC-3）"""
        mock_git_ops.delete_branch.side_effect = GitError("Cannot delete branch")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Warning" in result.stdout
        assert "Failed to delete branch" in result.stdout

        # Verify checkout was called but pull continued
        mock_git_ops.checkout_branch.assert_called_once_with("main")
        mock_git_ops.pull.assert_called_once()

    def test_close_pull_fails(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試更新 base_branch 失敗（AC-4）"""
        mock_git_ops.pull.side_effect = GitError("Cannot pull")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Warning" in result.stdout
        assert "Failed to update base branch" in result.stdout

        # Verify previous operations were called
        mock_git_ops.checkout_branch.assert_called_once_with("main")
        mock_git_ops.delete_branch.assert_called_once_with("test-issue")

    def test_close_preserves_issue_directory(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試確認資料夾保留（AC-5）"""
        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Issue directory must still exist
        assert issue_with_config.exists()

    def test_close_without_issue_config(self, temp_repo_dir, mock_git_ops):
        """測試當 issue config 不存在時"""
        mock_git_ops.get_current_branch.return_value = "nonexistent-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "Issue config not found" in result.stdout

    def test_close_with_custom_base_branch(self, temp_repo_dir, mock_git_ops):
        """測試使用自訂 base branch"""
        # Create issue with custom base branch
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "custom-issue"
        issue_dir.mkdir(parents=True)

        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "develop",
            "feature_branch": "custom-issue"
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "custom-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Switching to base branch: develop" in result.stdout
        mock_git_ops.checkout_branch.assert_called_once_with("develop")
