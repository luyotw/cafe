"""Tests for restore CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import shutil

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

        yield mock_git


@pytest.fixture
def archived_issue(temp_repo_dir):
    """Create an archived issue with backup data."""
    # Create archive path
    project_path = str(temp_repo_dir.resolve()).lstrip('/').replace('/', '-')
    archive_base = Path.home() / ".cafe" / "projects" / project_path / "archived"
    archive_path = archive_base / "test-issue"
    archive_path.mkdir(parents=True, exist_ok=True)

    # Create issue.yaml in archive
    config_file = archive_path / "issue.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-issue"
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    # Create some test data
    test_file = archive_path / "test_data.txt"
    test_file.write_text("test content")

    yield archive_path

    # Cleanup
    if archive_base.exists():
        shutil.rmtree(archive_base)


@pytest.fixture
def archived_issue_with_worktree(temp_repo_dir):
    """Create an archived issue with worktree configuration."""
    # Create archive path
    project_path = str(temp_repo_dir.resolve()).lstrip('/').replace('/', '-')
    archive_base = Path.home() / ".cafe" / "projects" / project_path / "archived"
    archive_path = archive_base / "test-worktree-issue"
    archive_path.mkdir(parents=True, exist_ok=True)

    # Create issue.yaml with worktree path
    config_file = archive_path / "issue.yaml"
    config_data = {
        "base_branch": "main",
        "feature_branch": "test-worktree-issue",
        "worktree_path": ".cafe/worktrees/test-worktree-issue"
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    # Create some test data
    test_file = archive_path / "test_data.txt"
    test_file.write_text("worktree test content")

    yield archive_path

    # Cleanup
    if archive_base.exists():
        shutil.rmtree(archive_base)


class TestRestoreCommand:
    """Test restore command."""

    def test_restore_backup_not_found(self, temp_repo_dir, mock_git_ops):
        """測試備份不存在的錯誤處理"""
        result = runner.invoke(app, ["restore", "nonexistent-issue"])

        assert result.exit_code == 1
        assert "backup not found" in result.stdout.lower()

    def test_restore_success_normal_mode(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試成功 restore 的情境（備份存在、branch 正確、一般模式）"""
        # Mock user confirmation
        result = runner.invoke(app, ["restore", "test-issue"], input="y\n")

        assert result.exit_code == 0
        assert "Successfully restored" in result.stdout or "成功" in result.stdout

        # Verify issue directory was restored
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert issue_dir.exists(), "Issue directory should be restored"

        # Verify files were copied
        test_file = issue_dir / "test_data.txt"
        assert test_file.exists()
        assert test_file.read_text() == "test content"

        # Verify issue.yaml was copied
        config_file = issue_dir / "issue.yaml"
        assert config_file.exists()

    def test_restore_branch_mismatch(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試 branch 不一致時自動檢出正確分支"""
        # Set current branch to something different
        mock_git_ops.get_current_branch.return_value = "wrong-branch"
        # Mock branch_exists to return False so it will create and checkout
        mock_git_ops.branch_exists.return_value = False

        result = runner.invoke(app, ["restore", "test-issue"], input="y\n")

        assert result.exit_code == 0
        assert "Checking out" in result.stdout or "checked out" in result.stdout.lower()
        # Verify that create_branch and checkout_branch were called
        mock_git_ops.create_branch.assert_called_once_with("test-issue")
        mock_git_ops.checkout_branch.assert_called_once_with("test-issue")

    @pytest.mark.xfail(reason="_get_project_path() doesn't handle worktrees correctly - needs fix")
    def test_restore_worktree_mode_success(self, temp_repo_dir, mock_git_ops, archived_issue_with_worktree):
        """測試 worktree 模式的成功 restore（備份存在、worktree 路徑正確）"""
        # Create worktree directory structure
        worktree_path = temp_repo_dir / ".cafe" / "worktrees" / "test-worktree-issue"
        worktree_path.mkdir(parents=True, exist_ok=True)

        # Create .cafe structure in worktree (as would exist in real worktree)
        (worktree_path / ".cafe").mkdir(exist_ok=True)
        (worktree_path / ".cafe" / "issues").mkdir(exist_ok=True)

        # Change to worktree directory
        import os
        os.chdir(worktree_path)

        mock_git_ops.get_current_branch.return_value = "test-worktree-issue"

        # Mock user confirmation
        result = runner.invoke(app, ["restore", "test-worktree-issue"], input="y\n")

        assert result.exit_code == 0
        assert "Successfully restored" in result.stdout or "成功" in result.stdout

        # Verify issue directory was restored in worktree
        issue_dir = worktree_path / ".cafe" / "issues" / "test-worktree-issue"
        assert issue_dir.exists(), "Issue directory should be restored in worktree"

    def test_restore_worktree_path_mismatch(self, temp_repo_dir, mock_git_ops, archived_issue_with_worktree):
        """測試 worktree 路徑不一致時自動導航到正確目錄"""
        # Current directory is temp_repo_dir, not the worktree path
        mock_git_ops.get_current_branch.return_value = "test-worktree-issue"

        # Mock run_git to handle worktree creation
        mock_git_ops.run_git.return_value = None

        with patch(
            "cafe.ui.commands.lifecycle._ensure_worktree_cafe_excluded"
        ) as mock_exclude:
            result = runner.invoke(app, ["restore", "test-worktree-issue"], input="y\n")

        assert result.exit_code == 0
        # Should attempt to create worktree
        assert "Creating worktree" in result.stdout or "worktree" in result.stdout.lower()
        mock_exclude.assert_called_once()

    def test_restore_user_cancels_confirmation(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試使用者取消確認後退出"""
        result = runner.invoke(app, ["restore", "test-issue"], input="n\n")

        assert result.exit_code == 1
        # Issue directory should not be restored
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert not issue_dir.exists(), "Issue directory should not be restored when user cancels"

    def test_restore_user_confirms(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試使用者確認後繼續執行 restore"""
        result = runner.invoke(app, ["restore", "test-issue"], input="y\n")

        assert result.exit_code == 0
        # Issue directory should be restored
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        assert issue_dir.exists(), "Issue directory should be restored when user confirms"

    def test_restore_overwrites_existing_data(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試 restore 覆蓋現有資料"""
        # Create existing issue directory with different content
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        existing_file = issue_dir / "existing_file.txt"
        existing_file.write_text("existing content")

        result = runner.invoke(app, ["restore", "test-issue"], input="y\n")

        assert result.exit_code == 0

        # Verify old file is gone and new file exists
        assert not existing_file.exists()
        test_file = issue_dir / "test_data.txt"
        assert test_file.exists()
        assert test_file.read_text() == "test content"

    def test_restore_multiple_times(self, temp_repo_dir, mock_git_ops, archived_issue):
        """測試重複執行 restore"""
        # First restore
        result1 = runner.invoke(app, ["restore", "test-issue"], input="y\n")
        assert result1.exit_code == 0

        # Modify the restored data
        issue_dir = temp_repo_dir / ".cafe" / "issues" / "test-issue"
        test_file = issue_dir / "test_data.txt"
        test_file.write_text("modified content")

        # Second restore should overwrite the modification
        result2 = runner.invoke(app, ["restore", "test-issue"], input="y\n")
        assert result2.exit_code == 0

        # Verify content is restored to original
        assert test_file.read_text() == "test content"
