"""Tests for GitHub operations."""

import pytest
import subprocess
from unittest.mock import MagicMock, Mock, patch, call

from cafe.utils.github import GitHubOps, GitHubError


class TestGitHubOpsInit:
    """Test GitHubOps initialization."""

    @patch("subprocess.run")
    def test_init_default(self, mock_run: Mock) -> None:
        """測試使用預設參數初始化"""
        mock_run.return_value = Mock(returncode=0, stdout="gh version 2.0.0")

        gh_ops = GitHubOps()

        assert gh_ops.check_gh_installed() is True

    @patch("subprocess.run")
    def test_init_checks_gh_cli(self, mock_run: Mock) -> None:
        """測試初始化時檢查 gh CLI 是否安裝"""
        mock_run.return_value = Mock(returncode=0, stdout="gh version 2.0.0")

        gh_ops = GitHubOps()

        # Should be called at least once during init
        assert mock_run.call_count >= 1
        # First call should be version check
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "gh" in first_call_args
        assert "--version" in first_call_args


class TestGetIssue:
    """Test get_issue functionality."""

    @patch("subprocess.run")
    def test_get_issue_success(self, mock_run: Mock) -> None:
        """測試成功取得 issue 資訊"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='{"number":123,"title":"Test Issue","body":"Issue body","state":"open"}',
        )

        gh_ops = GitHubOps()
        issue = gh_ops.get_issue("123")

        assert issue["number"] == 123
        assert issue["title"] == "Test Issue"
        assert issue["body"] == "Issue body"
        assert issue["state"] == "open"

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "view" in call_args
        assert "123" in call_args

    @patch("subprocess.run")
    def test_get_issue_not_found(self, mock_run: Mock) -> None:
        """測試 issue 不存在時拋出錯誤"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "issue", "view", "999"],
            stderr="issue not found",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to get issue"):
            gh_ops.get_issue("999")

    @patch("subprocess.run")
    def test_get_issue_with_comments(self, mock_run: Mock) -> None:
        """測試取得包含 comments  issue"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='{"number":123,"title":"Test","body":"Body","comments":[{"body":"Comment 1"}]}',
        )

        gh_ops = GitHubOps()
        issue = gh_ops.get_issue("123", include_comments=True)

        assert "comments" in issue
        assert len(issue["comments"]) == 1
        assert issue["comments"][0]["body"] == "Comment 1"


class TestCreatePR:
    """Test create_pr functionality."""

    @patch("subprocess.run")
    def test_create_pr_success(self, mock_run: Mock) -> None:
        """測試成功建立 PR"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/456\n",
        )

        gh_ops = GitHubOps()
        pr_url = gh_ops.create_pr(
            title="Test PR",
            body="PR description",
            head="feature-branch",
            base="main",
        )

        assert pr_url == "https://github.com/owner/repo/pull/456"

        # 驗證呼叫參數
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "pr" in call_args
        assert "create" in call_args
        assert "--title" in call_args
        assert "Test PR" in call_args
        assert "--body" in call_args
        assert "--head" in call_args
        assert "feature-branch" in call_args
        assert "--base" in call_args
        assert "main" in call_args

    @patch("subprocess.run")
    def test_create_pr_with_issue_link(self, mock_run: Mock) -> None:
        """測試建立 PR 並連結到 issue"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/456\n",
        )

        gh_ops = GitHubOps()
        pr_url = gh_ops.create_pr(
            title="Fix #123",
            body="Fixes issue #123",
            head="issue-123",
        )

        assert pr_url == "https://github.com/owner/repo/pull/456"

    @patch("subprocess.run")
    def test_create_pr_failure(self, mock_run: Mock) -> None:
        """測試建立 PR 失敗"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "create"],
            stderr="failed to create PR",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to create PR"):
            gh_ops.create_pr("Test", "Body", "feature")


class TestAddComment:
    """Test add_comment functionality."""

    @patch("subprocess.run")
    def test_add_issue_comment_success(self, mock_run: Mock) -> None:
        """測試成功在 issue 新增評論"""
        mock_run.return_value = Mock(returncode=0, stdout="")

        gh_ops = GitHubOps()
        gh_ops.add_issue_comment("123", "This is a comment")

        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "comment" in call_args
        assert "123" in call_args
        assert "--body" in call_args
        assert "This is a comment" in call_args

    @patch("subprocess.run")
    def test_add_pr_comment_success(self, mock_run: Mock) -> None:
        """測試成功在 PR 新增評論"""
        mock_run.return_value = Mock(returncode=0, stdout="")

        gh_ops = GitHubOps()
        gh_ops.add_pr_comment("456", "PR comment")

        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "pr" in call_args
        assert "comment" in call_args
        assert "456" in call_args

    @patch("subprocess.run")
    def test_add_comment_failure(self, mock_run: Mock) -> None:
        """測試新增評論失敗"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "issue", "comment"],
            stderr="failed to add comment",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to add issue comment"):
            gh_ops.add_issue_comment("123", "Comment")


class TestGetPRStatus:
    """Test get_pr_status functionality."""

    @patch("subprocess.run")
    def test_get_pr_status_open(self, mock_run: Mock) -> None:
        """測試取得 open 狀態 PR"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='{"number":456,"state":"OPEN","mergeable":"MERGEABLE","title":"Test PR"}',
        )

        gh_ops = GitHubOps()
        pr_status = gh_ops.get_pr_status("456")

        assert pr_status["number"] == 456
        assert pr_status["state"] == "OPEN"
        assert pr_status["mergeable"] == "MERGEABLE"
        assert pr_status["title"] == "Test PR"

    @patch("subprocess.run")
    def test_get_pr_status_merged(self, mock_run: Mock) -> None:
        """測試取得已合併 PR"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='{"number":456,"state":"MERGED","mergeable":"UNKNOWN"}',
        )

        gh_ops = GitHubOps()
        pr_status = gh_ops.get_pr_status("456")

        assert pr_status["state"] == "MERGED"

    @patch("subprocess.run")
    def test_get_pr_status_not_found(self, mock_run: Mock) -> None:
        """測試 PR 不存在"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "view"],
            stderr="PR not found",
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to get PR status"):
            gh_ops.get_pr_status("999")


class TestCheckGHInstalled:
    """Test check_gh_installed functionality."""

    @patch("subprocess.run")
    def test_gh_installed(self, mock_run: Mock) -> None:
        """測試 gh CLI 已安裝"""
        mock_run.return_value = Mock(returncode=0, stdout="gh version 2.0.0")

        gh_ops = GitHubOps()

        assert gh_ops.check_gh_installed() is True

    @patch("subprocess.run")
    def test_gh_not_installed(self, mock_run: Mock) -> None:
        """測試 gh CLI 未安裝"""
        mock_run.side_effect = FileNotFoundError("gh not found")

        gh_ops = GitHubOps()

        assert gh_ops.check_gh_installed() is False

    @patch("subprocess.run")
    def test_gh_version_check_error(self, mock_run: Mock) -> None:
        """測試 gh CLI 版本檢查錯誤"""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=127,
            cmd=["gh", "--version"],
        )

        gh_ops = GitHubOps()

        assert gh_ops.check_gh_installed() is False


class TestCheckGHAuth:
    """Test check_gh_auth functionality."""

    @patch("subprocess.run")
    def test_gh_auth_success(self, mock_run: Mock) -> None:
        """測試 gh CLI 已登入"""
        # First call for __init__ (check_gh_installed)
        # Second call for check_gh_auth
        mock_run.side_effect = [
            Mock(returncode=0, stdout="gh version 2.0.0"),  # --version check
            Mock(returncode=0, stdout="gh version 2.0.0"),  # check_gh_installed in check_gh_auth
            Mock(returncode=0, stdout="Logged in to github.com"),  # auth status
        ]

        gh_ops = GitHubOps()
        result = gh_ops.check_gh_auth()

        assert result is True
        # Verify gh auth status was called
        assert mock_run.call_count == 3
        last_call_args = mock_run.call_args_list[-1][0][0]
        assert "gh" in last_call_args
        assert "auth" in last_call_args
        assert "status" in last_call_args

    @patch("subprocess.run")
    def test_gh_auth_not_authenticated(self, mock_run: Mock) -> None:
        """測試 gh CLI 未登入"""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="gh version 2.0.0"),  # --version check in __init__
            Mock(returncode=0, stdout="gh version 2.0.0"),  # check_gh_installed in check_gh_auth
            Mock(returncode=1, stderr="Not logged in"),  # auth status
        ]

        gh_ops = GitHubOps()
        result = gh_ops.check_gh_auth()

        assert result is False

    @patch("subprocess.run")
    def test_gh_auth_not_installed(self, mock_run: Mock) -> None:
        """測試 gh CLI 未安裝時拋出錯誤"""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="gh version 2.0.0"),  # --version check in __init__
            FileNotFoundError("gh not found"),  # check_gh_installed in check_gh_auth
        ]

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="gh CLI is not installed"):
            gh_ops.check_gh_auth()

class TestExtractPRNumber:
    """Test extract_pr_number functionality."""

    def test_extract_pr_number_from_url(self) -> None:
        """測試從 URL 提取 PR 編號"""
        gh_ops = GitHubOps()

        pr_number = gh_ops.extract_pr_number(
            "https://github.com/owner/repo/pull/456"
        )

        assert pr_number == "456"

    def test_extract_pr_number_from_number(self) -> None:
        """測試直接使用 PR 編號"""
        gh_ops = GitHubOps()

        pr_number = gh_ops.extract_pr_number("456")

        assert pr_number == "456"

    def test_extract_pr_number_invalid_url(self) -> None:
        """測試無效 URL"""
        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Invalid PR URL or number"):
            gh_ops.extract_pr_number("https://invalid-url.com")

    def test_extract_pr_number_from_url_with_hash(self) -> None:
        """測試從帶有 hash  URL 提取 PR 編號"""
        gh_ops = GitHubOps()

        pr_number = gh_ops.extract_pr_number(
            "https://github.com/owner/repo/pull/456#issuecomment-123"
        )

        assert pr_number == "456"


class TestExtractIssueNumber:
    """Test extract_issue_number functionality."""

    def test_extract_issue_number_from_url(self) -> None:
        """測試從 URL 提取 Issue 編號"""
        gh_ops = GitHubOps()

        issue_number = gh_ops.extract_issue_number(
            "https://github.com/owner/repo/issues/123"
        )

        assert issue_number == "123"

    def test_extract_issue_number_from_number(self) -> None:
        """測試直接使用 Issue 編號"""
        gh_ops = GitHubOps()

        issue_number = gh_ops.extract_issue_number("123")

        assert issue_number == "123"

    def test_extract_issue_number_invalid_url(self) -> None:
        """測試無效 URL"""
        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Invalid issue URL or number"):
            gh_ops.extract_issue_number("https://invalid-url.com")

    def test_extract_issue_number_from_url_with_hash(self) -> None:
        """測試從帶有 hash  URL 提取 Issue 編號"""
        gh_ops = GitHubOps()

        issue_number = gh_ops.extract_issue_number(
            "https://github.com/owner/repo/issues/456#issuecomment-789"
        )

        assert issue_number == "456"


class TestGetPRForBranch:
    """Test get_pr_for_branch functionality."""

    @patch("subprocess.run")
    def test_get_pr_for_branch_exists(self, mock_run: Mock) -> None:
        """測試找到 PR 情況"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='[{"number":10,"url":"https://github.com/owner/repo/pull/10","title":"Test PR","body":"Test body","state":"OPEN","isDraft":false}]',
        )

        gh_ops = GitHubOps()
        pr = gh_ops.get_pr_for_branch("feature-branch")

        assert pr is not None
        assert pr["number"] == 10
        assert pr["url"] == "https://github.com/owner/repo/pull/10"
        assert pr["state"] == "OPEN"
        assert pr["isDraft"] is False
        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "gh" in args
        assert "pr" in args
        assert "list" in args
        assert "--head" in args
        assert "feature-branch" in args
        # Verify state and isDraft are requested in JSON fields
        assert "--json" in args
        json_index = args.index("--json")
        json_fields = args[json_index + 1]
        assert "state" in json_fields
        assert "isDraft" in json_fields

    @patch("subprocess.run")
    def test_get_pr_for_branch_not_exists(self, mock_run: Mock) -> None:
        """測試沒有 PR 情況"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='[]',
        )

        gh_ops = GitHubOps()
        pr = gh_ops.get_pr_for_branch("feature-branch")

        assert pr is None

    @patch("subprocess.run")
    def test_get_pr_for_branch_error(self, mock_run: Mock) -> None:
        """測試錯誤處理"""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["gh", "pr", "list"], stderr="Error"
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to check PR for branch"):
            gh_ops.get_pr_for_branch("feature-branch")


class TestUpdatePR:
    """Test update_pr functionality."""

    @patch("subprocess.run")
    def test_update_pr_title_only(self, mock_run: Mock) -> None:
        """測試只更新 title"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_pr("10", title="New Title")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "gh" in args
        assert "pr" in args
        assert "edit" in args
        assert "10" in args
        assert "--title" in args
        assert "New Title" in args
        assert "--body" not in args

    @patch("subprocess.run")
    def test_update_pr_body_only(self, mock_run: Mock) -> None:
        """測試只更新 body"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_pr("10", body="New Body")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "--body" in args
        assert "New Body" in args
        assert "--title" not in args

    @patch("subprocess.run")
    def test_update_pr_both(self, mock_run: Mock) -> None:
        """測試同時更新 title and body"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_pr("10", title="New Title", body="New Body")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "--title" in args
        assert "New Title" in args
        assert "--body" in args
        assert "New Body" in args

    @patch("subprocess.run")
    def test_update_pr_error(self, mock_run: Mock) -> None:
        """測試更新失敗"""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["gh", "pr", "edit", "10"], stderr="PR not found"
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to update PR"):
            gh_ops.update_pr("10", title="New Title")


class TestUpdateIssue:
    """Test update_issue functionality."""

    @patch("subprocess.run")
    def test_update_issue_title_only(self, mock_run: Mock) -> None:
        """測試只更新 title"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_issue("123", title="New Title")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "gh" in args
        assert "issue" in args
        assert "edit" in args
        assert "123" in args
        assert "--title" in args
        assert "New Title" in args
        assert "--body" not in args

    @patch("subprocess.run")
    def test_update_issue_body_only(self, mock_run: Mock) -> None:
        """測試只更新 body"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_issue("123", body="New Body")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "--body" in args
        assert "New Body" in args
        assert "--title" not in args

    @patch("subprocess.run")
    def test_update_issue_both(self, mock_run: Mock) -> None:
        """測試同時更新 title and body"""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        gh_ops = GitHubOps()
        gh_ops.update_issue("123", title="New Title", body="New Body")

        # Should be called twice: once for --version check, once for actual call
        assert mock_run.call_count == 2
        # Check the actual call (last one)
        args = mock_run.call_args[0][0]
        assert "--title" in args
        assert "New Title" in args
        assert "--body" in args
        assert "New Body" in args

    @patch("subprocess.run")
    def test_update_issue_error(self, mock_run: Mock) -> None:
        """測試更新失敗"""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["gh", "issue", "edit", "123"], stderr="Issue not found"
        )

        gh_ops = GitHubOps()

        with pytest.raises(GitHubError, match="Failed to update issue"):
            gh_ops.update_issue("123", title="New Title")
