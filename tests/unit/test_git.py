"""Tests for Git operations."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.git import GitError, GitOperations


class TestGitOperations:
    """Test GitOperations class."""

    def test_init_with_default_path(self) -> None:
        """測試使用預設路徑初始化 GitOperations"""
        git = GitOperations()
        assert git.repo_path == Path(".")

    def test_init_with_custom_path(self) -> None:
        """測試使用自訂路徑初始化 GitOperations"""
        git = GitOperations("/tmp/repo")
        assert git.repo_path == Path("/tmp/repo")

    def test_run_git_success(self) -> None:
        """測試成功執行 git 指令並回傳輸出"""
        git = GitOperations()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="main\n",
                stderr="",
                returncode=0
            )

            result = git.run_git("branch", "--show-current")

            assert result == "main"
            mock_run.assert_called_once_with(
                ["git", "branch", "--show-current"],
                cwd=Path("."),
                capture_output=True,
                text=True,
                check=True,
            )

    def test_run_git_failure(self) -> None:
        """測試 git 指令失敗時拋出 GitError"""
        git = GitOperations()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, ["git", "status"], stderr="fatal: not a git repository"
            )

            with pytest.raises(GitError, match="Git command failed"):
                git.run_git("status")

    def test_get_current_branch(self) -> None:
        """測試取得當前分支名稱"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = "feature-branch"

            branch = git.get_current_branch()

            assert branch == "feature-branch"
            mock_run.assert_called_once_with("branch", "--show-current")

    def test_create_branch(self) -> None:
        """測試建立並切換到新分支"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.create_branch("new-feature")

            mock_run.assert_called_once_with("checkout", "-b", "new-feature")

    def test_checkout_branch(self) -> None:
        """測試切換到現有分支"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.checkout_branch("main")

            mock_run.assert_called_once_with("checkout", "main")

    def test_branch_exists_true(self) -> None:
        """測試當分支存在時回傳 True"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = ""

            exists = git.branch_exists("main")

            assert exists is True
            mock_run.assert_called_once_with(
                "show-ref", "--verify", "--quiet", "refs/heads/main"
            )

    def test_branch_exists_false(self) -> None:
        """測試當分支不存在時回傳 False"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.side_effect = GitError("Branch not found")

            exists = git.branch_exists("nonexistent")

            assert exists is False

    def test_get_diff_default_params(self) -> None:
        """測試使用預設參數取得 diff"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = "diff --git a/file.txt b/file.txt"

            diff = git.get_diff()

            assert diff == "diff --git a/file.txt b/file.txt"
            mock_run.assert_called_once_with("diff", "main", "HEAD")

    def test_get_diff_custom_params(self) -> None:
        """測試使用自訂參數取得 diff"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = "some diff"

            diff = git.get_diff(base="develop", head="feature-branch")

            assert diff == "some diff"
            mock_run.assert_called_once_with("diff", "develop", "feature-branch")

    def test_commit(self) -> None:
        """測試建立 commit"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.commit("Add new feature")

            mock_run.assert_called_once_with("commit", "-m", "Add new feature")

    def test_push_with_upstream(self) -> None:
        """測試 push 到 remote 並設定 upstream"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.push("feature-branch", set_upstream=True)

            mock_run.assert_called_once_with("push", "-u", "origin", "feature-branch")

    def test_push_without_upstream(self) -> None:
        """測試 push 到 remote 不設定 upstream"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.push("feature-branch", set_upstream=False)

            mock_run.assert_called_once_with("push", "feature-branch")

    def test_push_with_force(self) -> None:
        """測試 force push 到 remote"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.push("feature-branch", set_upstream=True, force=True)

            mock_run.assert_called_once_with("push", "--force", "-u", "origin", "feature-branch")

    def test_push_force_without_upstream(self) -> None:
        """測試 force push 不設定 upstream"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.push("feature-branch", set_upstream=False, force=True)

            mock_run.assert_called_once_with("push", "--force", "feature-branch")

    def test_get_status(self) -> None:
        """測試取得 git status"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = " M file.txt\n?? new.txt"

            status = git.get_status()

            assert status == " M file.txt\n?? new.txt"
            mock_run.assert_called_once_with("status", "--porcelain")

    def test_has_uncommitted_changes_true(self) -> None:
        """測試有未提交變更時回傳 True"""
        git = GitOperations()

        with patch.object(git, "get_status") as mock_status:
            mock_status.return_value = " M file.txt"

            has_changes = git.has_uncommitted_changes()

            assert has_changes is True

    def test_has_uncommitted_changes_false(self) -> None:
        """測試沒有未提交變更時回傳 False"""
        git = GitOperations()

        with patch.object(git, "get_status") as mock_status:
            mock_status.return_value = ""

            has_changes = git.has_uncommitted_changes()

            assert has_changes is False

    def test_get_current_branch_detached_head(self) -> None:
        """測試在 detached HEAD 狀態時回傳空字串"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            # git branch --show-current 在 detached HEAD 時回傳空字串
            mock_run.return_value = ""

            branch = git.get_current_branch()

            assert branch == ""
            mock_run.assert_called_once_with("branch", "--show-current")

    def test_is_valid_branch_true(self) -> None:
        """測試當前在有效分支時回傳 True"""
        git = GitOperations()

        with patch.object(git, "get_current_branch") as mock_get:
            mock_get.return_value = "feature-branch"

            is_valid = git.is_valid_branch()

            assert is_valid is True

    def test_is_valid_branch_false_detached_head(self) -> None:
        """測試在 detached HEAD 狀態時回傳 False"""
        git = GitOperations()

        with patch.object(git, "get_current_branch") as mock_get:
            mock_get.return_value = ""

            is_valid = git.is_valid_branch()

            assert is_valid is False

    def test_is_valid_branch_false_empty_string(self) -> None:
        """測試當分支名稱為空時回傳 False"""
        git = GitOperations()

        with patch.object(git, "get_current_branch") as mock_get:
            mock_get.return_value = ""

            is_valid = git.is_valid_branch()

            assert is_valid is False
