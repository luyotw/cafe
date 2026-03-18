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
    """Test fetch_github_issue function"""

    def test_fetch_issue_success(self):
        """Test successfully fetching a GitHub Issue"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Test Issue",
            "body": "This is a test issue body.",
        }
        github_ops.extract_image_urls.return_value = []

        content, image_urls = fetch_github_issue(github_ops, 123)

        assert content == "# Test Issue\n\nThis is a test issue body."
        assert image_urls == []
        github_ops.check_gh_auth.assert_called_once()
        github_ops.get_issue.assert_called_once_with("123", include_comments=True)

    def test_issue_without_body(self):
        """Test Issue without body"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Test Issue",
            "body": "",
        }

        content, image_urls = fetch_github_issue(github_ops, 456)

        # When title exists but body is empty, returns "# title\n\n"
        assert content == "# Test Issue\n\n"
        assert image_urls == []
        github_ops.get_issue.assert_called_once_with("456", include_comments=True)

    def test_issue_with_body_only(self):
        """Test Issue with body but no title"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "",
            "body": "Body without title",
        }
        github_ops.extract_image_urls.return_value = []

        content, image_urls = fetch_github_issue(github_ops, 789)

        assert content == "Body without title"
        assert image_urls == []

    def test_gh_cli_not_authenticated(self):
        """Test raises error when gh CLI is not authenticated"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = False

        with pytest.raises(RuntimeError) as exc_info:
            fetch_github_issue(github_ops, 123)

        assert "gh CLI is not authenticated" in str(exc_info.value)
        github_ops.get_issue.assert_not_called()

    def test_fetch_issue_failure(self):
        """Test raises error when fetching issue fails"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.side_effect = GitHubError("Issue not found")

        with pytest.raises(GitHubError):
            fetch_github_issue(github_ops, 999)

    def test_returns_tuple_with_image_urls(self):
        """Test returns (content, image_urls) tuple"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Test with Images",
            "body": "Description\n![img](https://example.com/image.png)",
        }
        github_ops.extract_image_urls.return_value = ["https://example.com/image.png"]

        content, image_urls = fetch_github_issue(github_ops, 123)

        assert content == "# Test with Images\n\nDescription\n![img](https://example.com/image.png)"
        assert image_urls == ["https://example.com/image.png"]
        github_ops.extract_image_urls.assert_called_once()

    def test_returns_empty_list_when_no_images(self):
        """Test image_urls is empty list when no images"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "No Images",
            "body": "Just text",
        }
        github_ops.extract_image_urls.return_value = []

        content, image_urls = fetch_github_issue(github_ops, 456)

        assert content == "# No Images\n\nJust text"
        assert image_urls == []

    def test_includes_discussion_thread_when_comments_exist(self):
        """Test that comments are appended as a discussion thread section"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Feature Request",
            "body": "Please add this feature.",
            "comments": [
                {
                    "author": {"login": "alice"},
                    "createdAt": "2026-01-01T10:00:00Z",
                    "body": "I agree this is needed.",
                },
                {
                    "author": {"login": "bob"},
                    "createdAt": "2026-01-02T12:00:00Z",
                    "body": "Here is some more context.",
                },
            ],
        }
        github_ops.extract_image_urls.return_value = []

        content, _ = fetch_github_issue(github_ops, 1)

        assert "## Discussion Thread" in content
        assert "### @alice (2026-01-01T10:00:00Z)" in content
        assert "I agree this is needed." in content
        assert "### @bob (2026-01-02T12:00:00Z)" in content
        assert "Here is some more context." in content

    def test_no_discussion_section_when_no_comments(self):
        """Test that no discussion section is added when there are no comments"""
        github_ops = Mock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_issue.return_value = {
            "title": "Simple Issue",
            "body": "Just a body.",
            "comments": [],
        }
        github_ops.extract_image_urls.return_value = []

        content, _ = fetch_github_issue(github_ops, 2)

        assert "Discussion Thread" not in content
        assert content == "# Simple Issue\n\nJust a body."
