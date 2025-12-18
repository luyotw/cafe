"""測試 phase_prompts.py 中共用 UI 函式"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor, fetch_github_issue
from cafe.ui.display import Display
from cafe.utils.github import GitHubOps, GitHubError


class TestPromptForInputMethod:
    """測試 prompt_for_input_method 函式"""

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_選擇手動輸入(self, mock_prompt_list):
        """測試用戶選擇手動輸入"""
        mock_prompt_list.return_value = "1. 手動輸入需求"
        display = Display()
        github_ops = Mock(spec=GitHubOps)

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "manual"
        assert issue_id is None

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_選擇GitHub_Issue並提供有效ID(self, mock_prompt_list, mock_prompt_text):
        """測試用戶選擇 GitHub Issue 並提供有效 Issue ID"""
        mock_prompt_list.return_value = "2. 從 GitHub Issue 抓取"
        mock_prompt_text.return_value = "123"
        display = Display()
        github_ops = Mock(spec=GitHubOps)
        github_ops.extract_issue_number.return_value = "123"

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "github"
        assert issue_id == 123
        github_ops.extract_issue_number.assert_called_once_with("123")

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_選擇GitHub_Issue並提供URL(self, mock_prompt_list, mock_prompt_text):
        """測試用戶選擇 GitHub Issue 並提供 URL"""
        mock_prompt_list.return_value = "2. 從 GitHub Issue 抓取"
        mock_prompt_text.return_value = "https://github.com/user/repo/issues/456"
        display = Display()
        github_ops = Mock(spec=GitHubOps)
        github_ops.extract_issue_number.return_value = "456"

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "github"
        assert issue_id == 456
        github_ops.extract_issue_number.assert_called_once_with(
            "https://github.com/user/repo/issues/456"
        )

    @patch("cafe.ui.phase_prompts.prompt_text")
    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_無效Issue_ID後重試(self, mock_prompt_list, mock_prompt_text):
        """測試輸入無效 Issue ID 後重新選擇"""
        mock_prompt_list.return_value = "2. 從 GitHub Issue 抓取"
        mock_prompt_text.side_effect = ["invalid", "789"]
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

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_無效選擇後重試(self, mock_prompt_list):
        """測試輸入無效選擇後重試"""
        mock_prompt_list.return_value = "1. 手動輸入需求"
        display = Display()
        github_ops = Mock(spec=GitHubOps)

        method, issue_id = prompt_for_input_method(display, github_ops)

        assert method == "manual"
        assert issue_id is None


class TestPromptForRigor:
    """測試 prompt_for_rigor 函式"""

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_使用預設值Medium(self, mock_prompt_list):
        """測試使用預設值 medium"""
        mock_prompt_list.return_value = "Medium (中) - balanced mode [預設]\n   • 詢問重要細節and關鍵場景\n   • 在速度and精確度間取得平衡\n   • 適合：一般功能開發"
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "medium"

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_明確選擇Medium(self, mock_prompt_list):
        """測試明確選擇 medium"""
        mock_prompt_list.return_value = "Medium (中) - balanced mode [預設]\n   • 詢問重要細節and關鍵場景\n   • 在速度and精確度間取得平衡\n   • 適合：一般功能開發"
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "medium"

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_選擇Low(self, mock_prompt_list):
        """測試選擇 low"""
        mock_prompt_list.return_value = "Low (低) - fast development模式\n   • 只問最關鍵資訊\n   • 允許模糊地帶, 讓開發者自行判斷\n   • 適合：快速原型、MVP、內部工具"
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "low"

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_選擇High(self, mock_prompt_list):
        """測試選擇 high"""
        mock_prompt_list.return_value = "High (高) - precise specification模式\n   • 詳細詢問所有細節and邊界情況\n   • 確保需求可測試、無模糊\n   • 適合：核心功能、API 設計、對外產品"
        display = Display()

        rigor = prompt_for_rigor(display)

        assert rigor == "high"

    @patch("cafe.ui.phase_prompts.prompt_list")
    def test_無效選擇後重試(self, mock_prompt_list):
        """測試輸入無效選擇後重試"""
        mock_prompt_list.return_value = "Medium (中) - balanced mode [預設]\n   • 詢問重要細節and關鍵場景\n   • 在速度and精確度間取得平衡\n   • 適合：一般功能開發"
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
        """測試 Issue 沒有 body 情況"""
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
