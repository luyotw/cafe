"""測試 phase_prompts.py 中的共用 UI 函式"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor, fetch_github_issue
from cafe.ui.display import Display
from cafe.utils.github import GitHubOps, GitHubError


class TestPromptForInputMethod:
    """測試 prompt_for_input_method 函式"""

    @patch("builtins.input", side_effect=["1"])
    def test_選擇手動輸入(self, mock_input):
        """測試用戶選擇手動輸入"""
        display = Display()
        github_ops = Mock(spec=GitHubOps)

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "manual"
        assert issue_id is None

    @patch("builtins.input", side_effect=["2", "123"])
    def test_選擇GitHub_Issue並提供有效ID(self, mock_input):
        """測試用戶選擇 GitHub Issue 並提供有效的 Issue ID"""
        display = Display()
        github_ops = Mock(spec=GitHubOps)
        github_ops.extract_issue_number.return_value = "123"

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "github"
        assert issue_id == 123
        github_ops.extract_issue_number.assert_called_once_with("123")

    @patch("builtins.input", side_effect=["2", "https://github.com/user/repo/issues/456"])
    def test_選擇GitHub_Issue並提供URL(self, mock_input):
        """測試用戶選擇 GitHub Issue 並提供 URL"""
        display = Display()
        github_ops = Mock(spec=GitHubOps)
        github_ops.extract_issue_number.return_value = "456"

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "github"
        assert issue_id == 456
        github_ops.extract_issue_number.assert_called_once_with(
            "https://github.com/user/repo/issues/456"
        )

    @patch("builtins.input", side_effect=["2", "invalid", "2", "789"])
    def test_無效Issue_ID後重試(self, mock_input):
        """測試輸入無效 Issue ID 後重新選擇"""
        display = Display()
        github_ops = Mock(spec=GitHubOps)
        # First call raises error, second call succeeds
        github_ops.extract_issue_number.side_effect = [
            ValueError("Invalid issue ID"),
            "789",
        ]

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "github"
        assert issue_id == 789

    @patch("builtins.input", side_effect=["3", "1"])
    def test_無效選擇後重試(self, mock_input):
        """測試輸入無效選擇後重試"""
        display = Display()
        github_ops = Mock(spec=GitHubOps)

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "manual"
        assert issue_id is None


class TestPromptForRigor:
    """測試 prompt_for_rigor 函式"""

    @patch("builtins.input", return_value="")
    def test_使用預設值Medium(self, mock_input):
        """測試按 Enter 使用預設值 medium"""
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "medium"

    @patch("builtins.input", return_value="2")
    def test_明確選擇Medium(self, mock_input):
        """測試明確選擇 medium"""
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "medium"

    @patch("builtins.input", return_value="1")
    def test_選擇Low(self, mock_input):
        """測試選擇 low"""
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "low"

    @patch("builtins.input", return_value="3")
    def test_選擇High(self, mock_input):
        """測試選擇 high"""
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "high"

    @patch("builtins.input", side_effect=["4", "2"])
    def test_無效選擇後重試(self, mock_input):
        """測試輸入無效選擇後重試"""
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "medium"


class TestFetchGithubIssue:
    """測試 fetch_github_issue 函式"""

    def test_成功抓取Issue(self):
        """測試成功抓取 GitHub Issue"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Test Issue",
            "body": "This is a test issue body.",
        }

        content = fetch_github_issue(github_ops, 123)

        assert content == "# Test Issue\n\nThis is a test issue body."
        github_ops.check_gh_auth.assert_called_once()
        github_ops.get_issue.assert_called_once_with("123", include_comments=False)

    def test_Issue沒有body(self):
        """測試 Issue 沒有 body 的情況"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Test Issue",
            "body": "",
        }

        content = fetch_github_issue(github_ops, 456)

        # When title exists but body is empty, returns "# title\n\n"
        assert content == "# Test Issue\n\n"
        github_ops.get_issue.assert_called_once_with("456", include_comments=False)

    def test_Issue只有body沒有title(self):
        """測試 Issue 只有 body 沒有 title"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "",
            "body": "Body without title",
        }

        content = fetch_github_issue(github_ops, 789)

        assert content == "Body without title"

    def test_gh_CLI未認證(self):
        """測試 gh CLI 未認證時拋出錯誤"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = False

        with pytest.raises(RuntimeError) as exc_info:
            fetch_github_issue(github_ops, 123)

        assert "gh CLI is not authenticated" in str(exc_info.value)
        github_ops.get_issue.assert_not_called()

    def test_抓取Issue失敗(self):
        """測試抓取 Issue 失敗時拋出錯誤"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.side_effect = GitHubError("Issue not found")

        with pytest.raises(GitHubError):
            fetch_github_issue(github_ops, 999)
