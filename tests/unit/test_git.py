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

    def test_delete_branch(self) -> None:
        """測試刪除本地分支"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.delete_branch("feature-branch")

            mock_run.assert_called_once_with("branch", "-d", "feature-branch")

    def test_delete_branch_failure(self) -> None:
        """測試刪除分支失敗時拋出 GitError"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.side_effect = GitError("Branch deletion failed")

            with pytest.raises(GitError, match="Branch deletion failed"):
                git.delete_branch("feature-branch")

    def test_pull(self) -> None:
        """測試拉取遠端更新"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            git.pull()

            mock_run.assert_called_once_with("pull")

    def test_pull_failure(self) -> None:
        """測試 pull 失敗時拋出 GitError"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.side_effect = GitError("Pull failed")

            with pytest.raises(GitError, match="Pull failed"):
                git.pull()

    def test_has_upstream_branch_true(self) -> None:
        """測試當前分支有 upstream 時回傳 True"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.return_value = "origin/feature-branch"

            has_upstream = git.has_upstream_branch()

            assert has_upstream is True
            mock_run.assert_called_once_with("rev-parse", "--abbrev-ref", "@{u}")

    def test_has_upstream_branch_false(self) -> None:
        """測試當前分支沒有 upstream 時回傳 False"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.side_effect = GitError("No upstream branch")

            has_upstream = git.has_upstream_branch()

            assert has_upstream is False

    def test_has_upstream_branch_error(self) -> None:
        """測試 has_upstream_branch 發生錯誤時回傳 False"""
        git = GitOperations()

        with patch.object(git, "run_git") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")

            # Should not raise, should return False
            has_upstream = git.has_upstream_branch()

            assert has_upstream is False

    def test_has_unpushed_commits_true(self) -> None:
        """測試有未 push 的 commits 時回傳 True"""
        git = GitOperations()

        # Use any non-empty commit log output
        commit_output = "abc123 Fix bug"

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.return_value = commit_output

                has_unpushed = git.has_unpushed_commits()

                assert has_unpushed is True
                mock_run.assert_called_once_with("log", "@{u}..HEAD", "--oneline")

    def test_has_unpushed_commits_false(self) -> None:
        """測試沒有未 push 的 commits 時回傳 False"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.return_value = ""

                has_unpushed = git.has_unpushed_commits()

                assert has_unpushed is False

    def test_has_unpushed_commits_no_upstream(self) -> None:
        """測試沒有 upstream 分支且遠端分支不存在時，有本地 commits 則回傳 True"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                with patch.object(git, "get_current_branch") as mock_branch:
                    mock_upstream.return_value = False
                    mock_branch.return_value = "feature-branch"
                    # First call: rev-parse to check remote branch (raises GitError - no remote)
                    # Second call: rev-list to count local commits
                    mock_run.side_effect = [GitError("not found"), "3"]

                    has_unpushed = git.has_unpushed_commits()

                    assert has_unpushed is True

    def test_has_unpushed_commits_error(self) -> None:
        """測試發生錯誤時回傳 False"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.side_effect = GitError("Command failed")

                has_unpushed = git.has_unpushed_commits()

                assert has_unpushed is False

    def test_get_unpushed_commits_success(self) -> None:
        """測試成功取得未 push 的 commits"""
        from datetime import datetime, timezone, timedelta

        git = GitOperations()

        # Use relative timestamps to avoid fragile tests
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        two_hours_ago = (now - timedelta(hours=2)).isoformat()

        commit_output = f"abc1234|{one_hour_ago}|Fix bug\ndef5678|{two_hours_ago}|Add feature"

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.return_value = commit_output

                commits = git.get_unpushed_commits()

                assert len(commits) == 2
                assert commits[0]["hash"] == "abc1234"
                assert commits[0]["timestamp"] == one_hour_ago
                assert commits[0]["message"] == "Fix bug"
                assert commits[1]["hash"] == "def5678"
                assert commits[1]["timestamp"] == two_hours_ago
                assert commits[1]["message"] == "Add feature"

    def test_get_unpushed_commits_empty(self) -> None:
        """測試沒有未 push 的 commits 時回傳空列表"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.return_value = ""

                commits = git.get_unpushed_commits()

                assert commits == []

    def test_get_unpushed_commits_no_upstream(self) -> None:
        """測試沒有 upstream 分支且沒有 remote 分支時回傳空列表"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream, \
             patch.object(git, "get_current_branch") as mock_branch, \
             patch.object(git, "run_git") as mock_run:
            mock_upstream.return_value = False
            mock_branch.return_value = "test-branch"
            # Simulate remote branch doesn't exist
            mock_run.side_effect = GitError("fatal: Needed a single revision")

            commits = git.get_unpushed_commits()

            assert commits == []

    def test_get_unpushed_commits_error(self) -> None:
        """測試發生錯誤時回傳空列表"""
        git = GitOperations()

        with patch.object(git, "has_upstream_branch") as mock_upstream:
            with patch.object(git, "run_git") as mock_run:
                mock_upstream.return_value = True
                mock_run.side_effect = GitError("Command failed")

                commits = git.get_unpushed_commits()

                assert commits == []

    def test_get_latest_unpushed_commit_timestamp_success(self) -> None:
        """測試成功取得最新未 push commit 的時間戳"""
        from datetime import datetime, timezone, timedelta

        git = GitOperations()

        # Use relative timestamps to avoid fragile tests
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        two_hours_ago = (now - timedelta(hours=2)).isoformat()

        with patch.object(git, "get_unpushed_commits") as mock_get:
            mock_get.return_value = [
                {
                    "hash": "abc1234",
                    "timestamp": one_hour_ago,
                    "message": "Fix bug"
                },
                {
                    "hash": "def5678",
                    "timestamp": two_hours_ago,
                    "message": "Add feature"
                }
            ]

            timestamp = git.get_latest_unpushed_commit_timestamp()

            assert timestamp == one_hour_ago

    def test_get_latest_unpushed_commit_timestamp_none(self) -> None:
        """測試沒有未 push 的 commits 時回傳 None"""
        git = GitOperations()

        with patch.object(git, "get_unpushed_commits") as mock_get:
            mock_get.return_value = []

            timestamp = git.get_latest_unpushed_commit_timestamp()

            assert timestamp is None
