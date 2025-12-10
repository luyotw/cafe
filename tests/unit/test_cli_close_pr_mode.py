"""Tests for cafe close command with pr.auto_create config (issue45)."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner
import yaml
import pytest

from cafe.ui.cli import app


runner = CliRunner()


@pytest.fixture
def temp_repo_dir(tmp_path, monkeypatch):
    """Create temporary repository directory."""
    # Change to temp directory directly
    monkeypatch.chdir(tmp_path)

    # Create .cafe directory
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir(parents=True)

    return tmp_path


@pytest.fixture
def mock_git_ops(monkeypatch):
    """Mock GitOperations."""
    with patch("cafe.ui.cli.GitOperations") as MockGitOps:
        mock = MagicMock()
        MockGitOps.return_value = mock
        mock.checkout_branch.return_value = None
        mock.pull.return_value = None
        mock.merge.return_value = None  # Add merge mock
        mock.delete_branch.return_value = None
        mock.get_current_branch.return_value = "test-issue"
        yield mock


@pytest.fixture
def mock_github_ops_no_pr(monkeypatch):
    """Mock GitHubOps to return no PR."""
    with patch("cafe.ui.cli.GitHubOps") as MockGitHubOps:
        mock = MagicMock()
        MockGitHubOps.return_value = mock
        mock.check_gh_auth.return_value = False
        # Important: Return None for get_pr_for_branch to indicate no PR exists
        mock.get_pr_for_branch.return_value = None
        yield mock


@pytest.fixture(autouse=True)
def cleanup_archive(temp_repo_dir):
    """Clean up archived directories after each test."""
    yield
    # Cleanup code (if needed)


class TestCloseCommandPRMode:
    """Test cafe close command with pr.auto_create config."""

    def test_close_with_pr_auto_create_true_uses_git_pull(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """測試 pr.auto_create: true 時使用 git pull"""
        # Setup issue config with pr.auto_create: true
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-issue",
            "pr": {"auto_create": True},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "test-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Verify git pull was called
        mock_git_ops.pull.assert_called_once()
        # Verify git merge was not called
        assert not hasattr(mock_git_ops, "merge") or not mock_git_ops.merge.called

    def test_close_with_pr_auto_create_false_uses_git_merge(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """測試 pr.auto_create: false 時使用 git merge"""
        # Setup issue config with pr.auto_create: false
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-issue",
            "pr": {"auto_create": False},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "test-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Verify git merge was called with feature branch
        mock_git_ops.merge.assert_called_once_with("test-issue")
        # Verify git pull was not called
        assert not mock_git_ops.pull.called

    def test_close_without_pr_config_defaults_to_git_pull(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """測試沒有 pr config 時預設使用 git pull（向下相容）"""
        # Setup issue config without pr config
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        config_file = issue_dir / "config.yaml"
        config_data = {"base_branch": "main", "feature_branch": "test-issue"}
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "test-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Verify git pull was called (default behavior)
        mock_git_ops.pull.assert_called_once()

    def test_close_worktree_mode_with_pr_auto_create_false_uses_git_merge(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """測試 worktree 模式下 pr.auto_create: false 時使用 git merge"""
        # Setup worktree
        worktree_path = temp_repo_dir / "worktrees" / "test-issue"
        worktree_path.mkdir(parents=True)

        # Setup issue config with worktree and pr.auto_create: false
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-issue",
            "worktree_path": str(worktree_path),
            "pr": {"auto_create": False},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "test-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Verify git merge was called
        mock_git_ops.merge.assert_called_once_with("test-issue")
        # Verify worktree removal was called
        mock_git_ops.remove_worktree.assert_called_once_with(str(worktree_path))
