"""Tests for close CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.git import GitError
from cafe.ui.cli import app
from cafe.utils.github import GitHubError

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
def mock_github_ops_no_pr():
    """Create a mock GitHubOps instance with no PR."""
    with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
        mock_gh = MagicMock()
        MockGitHubOps.return_value = mock_gh

        # Default: no PR exists
        mock_gh.get_pr_for_branch.return_value = None

        yield mock_gh


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

    def test_close_success(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_config):
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

    def test_close_checkout_fails(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_config):
        """測試切換分支失敗（AC-2）"""
        mock_git_ops.checkout_branch.side_effect = GitError("Cannot checkout branch")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "Cannot checkout branch" in result.stdout

        # Verify subsequent operations not called
        mock_git_ops.delete_branch.assert_not_called()
        mock_git_ops.pull.assert_not_called()

    def test_close_delete_branch_fails(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_config):
        """測試刪除分支失敗（AC-3）"""
        mock_git_ops.delete_branch.side_effect = GitError("Cannot delete branch")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Warning" in result.stdout
        assert "Failed to delete branch" in result.stdout

        # Verify checkout was called but pull continued
        mock_git_ops.checkout_branch.assert_called_once_with("main")
        mock_git_ops.pull.assert_called_once()

    def test_close_pull_fails(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_config):
        """測試更新 base_branch 失敗（AC-4）"""
        mock_git_ops.pull.side_effect = GitError("Cannot pull")

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "Warning" in result.stdout
        assert "Failed to update base branch" in result.stdout

        # Verify previous operations were called
        mock_git_ops.checkout_branch.assert_called_once_with("main")
        mock_git_ops.delete_branch.assert_called_once_with("test-issue")

    def test_close_preserves_issue_directory(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_config):
        """測試確認資料夾保留（AC-5）"""
        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # Issue directory must still exist
        assert issue_with_config.exists()

    def test_close_without_issue_config(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr):
        """測試當 issue config 不存在時"""
        mock_git_ops.get_current_branch.return_value = "nonexistent-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 1
        assert "Error" in result.stdout
        assert "Issue config not found" in result.stdout

    def test_close_with_custom_base_branch(self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr):
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


class TestCloseCommandWithPRCheck:
    """Test close command with PR checks."""

    def test_close_blocked_by_open_pr(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試當有 open PR 時無法 close"""
        with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
            mock_gh = MagicMock()
            MockGitHubOps.return_value = mock_gh

            # Mock open PR
            mock_gh.get_pr_for_branch.return_value = {
                "number": 123,
                "title": "Test PR",
                "url": "https://github.com/owner/repo/pull/123",
                "state": "OPEN",
                "isDraft": False
            }

            result = runner.invoke(app, ["close"])

            assert result.exit_code == 1
            assert "Cannot close: Open PR found" in result.stdout
            assert "PR #123" in result.stdout
            assert "Test PR" in result.stdout
            assert "State: OPEN" in result.stdout

            # Verify git operations not called
            mock_git_ops.checkout_branch.assert_not_called()
            mock_git_ops.delete_branch.assert_not_called()

    def test_close_blocked_by_draft_pr(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試當有 draft PR 時無法 close"""
        with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
            mock_gh = MagicMock()
            MockGitHubOps.return_value = mock_gh

            # Mock draft PR
            mock_gh.get_pr_for_branch.return_value = {
                "number": 456,
                "title": "Draft PR",
                "url": "https://github.com/owner/repo/pull/456",
                "state": "OPEN",
                "isDraft": True
            }

            result = runner.invoke(app, ["close"])

            assert result.exit_code == 1
            assert "Cannot close: Open PR found" in result.stdout
            assert "PR #456" in result.stdout
            assert "Draft PR" in result.stdout
            assert "(DRAFT)" in result.stdout

            # Verify git operations not called
            mock_git_ops.checkout_branch.assert_not_called()
            mock_git_ops.delete_branch.assert_not_called()

    def test_close_allowed_with_merged_pr(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試當 PR 已合併時允許 close"""
        with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
            mock_gh = MagicMock()
            MockGitHubOps.return_value = mock_gh

            # Mock merged PR
            mock_gh.get_pr_for_branch.return_value = {
                "number": 789,
                "title": "Merged PR",
                "url": "https://github.com/owner/repo/pull/789",
                "state": "MERGED",
                "isDraft": False
            }

            result = runner.invoke(app, ["close"])

            assert result.exit_code == 0
            assert "Successfully closed issue: test-issue" in result.stdout

            # Verify git operations were called
            mock_git_ops.checkout_branch.assert_called_once_with("main")
            mock_git_ops.delete_branch.assert_called_once_with("test-issue")

    def test_close_allowed_with_closed_pr(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試當 PR 已關閉時允許 close"""
        with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
            mock_gh = MagicMock()
            MockGitHubOps.return_value = mock_gh

            # Mock closed PR
            mock_gh.get_pr_for_branch.return_value = {
                "number": 999,
                "title": "Closed PR",
                "url": "https://github.com/owner/repo/pull/999",
                "state": "CLOSED",
                "isDraft": False
            }

            result = runner.invoke(app, ["close"])

            assert result.exit_code == 0
            assert "Successfully closed issue: test-issue" in result.stdout

            # Verify git operations were called
            mock_git_ops.checkout_branch.assert_called_once_with("main")
            mock_git_ops.delete_branch.assert_called_once_with("test-issue")

    def test_close_continues_when_github_unavailable(self, temp_repo_dir, mock_git_ops, issue_with_config):
        """測試當 GitHub 不可用時仍可繼續 close"""
        with patch('cafe.ui.cli.GitHubOps') as MockGitHubOps:
            # Mock GitHubOps constructor to raise error (e.g., gh CLI not installed)
            MockGitHubOps.side_effect = GitHubError("gh CLI not installed")

            result = runner.invoke(app, ["close"])

            # Should continue without PR check
            assert result.exit_code == 0
            assert "Successfully closed issue: test-issue" in result.stdout

            # Verify git operations were called
            mock_git_ops.checkout_branch.assert_called_once_with("main")
            mock_git_ops.delete_branch.assert_called_once_with("test-issue")


class TestCloseCommandWorktree:
    """Test close command with worktree support (TDD Red phase)."""

    @pytest.fixture
    def issue_with_worktree_config(self, temp_repo_dir):
        """Create an issue with worktree configuration."""
        # Create issue directory with config
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-worktree-issue"
        issue_dir.mkdir(parents=True)

        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "test-worktree-issue",
            "worktree_path": "worktrees/test-worktree-issue",
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f)

        return issue_dir

    def test_close_with_worktree_success(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_worktree_config
    ):
        """測試 worktree 模式下成功清理"""
        mock_git_ops.get_current_branch.return_value = "test-worktree-issue"

        # 確保 branch 刪除成功
        mock_git_ops.delete_branch.return_value = None

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # 驗證 branch 先被刪除
        mock_git_ops.delete_branch.assert_called_once_with("test-worktree-issue")
        # 驗證 worktree 目錄被刪除
        mock_git_ops.remove_worktree.assert_called_once_with("worktrees/test-worktree-issue")

    def test_close_with_worktree_branch_delete_fails(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_worktree_config
    ):
        """測試 branch 刪除失敗時保留 worktree"""
        mock_git_ops.get_current_branch.return_value = "test-worktree-issue"

        # 模擬 branch 刪除失敗（未合併）
        mock_git_ops.delete_branch.side_effect = GitError("The branch 'test-worktree-issue' is not fully merged")

        result = runner.invoke(app, ["close"])

        # 應該成功完成（顯示警告）
        assert result.exit_code == 0
        # 驗證 branch 刪除被嘗試
        mock_git_ops.delete_branch.assert_called_once()
        # 驗證 worktree 目錄沒有被刪除
        assert not mock_git_ops.remove_worktree.called

    def test_close_without_worktree_normal_flow(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr
    ):
        """測試沒有 worktree 時的正常流程"""
        # Create issue directory without worktree config
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "normal-issue"
        issue_dir.mkdir(parents=True)

        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": "main",
            "feature_branch": "normal-issue",
            # 沒有 worktree_path
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f)

        mock_git_ops.get_current_branch.return_value = "normal-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        # 驗證 branch 被刪除
        mock_git_ops.delete_branch.assert_called_once_with("normal-issue")
        # 驗證 worktree 方法沒有被呼叫
        assert not hasattr(mock_git_ops, 'remove_worktree') or not mock_git_ops.remove_worktree.called

    def test_close_worktree_success_message(
        self, temp_repo_dir, mock_git_ops, mock_github_ops_no_pr, issue_with_worktree_config
    ):
        """測試 worktree 清理的成功訊息"""
        mock_git_ops.get_current_branch.return_value = "test-worktree-issue"

        result = runner.invoke(app, ["close"])

        assert result.exit_code == 0
        assert "worktree" in result.stdout.lower() or "worktrees/test-worktree-issue" in result.stdout
