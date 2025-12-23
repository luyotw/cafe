"""Unit tests for GitHub PR comments utilities.

測試 GitHub PR comments 獲取、過濾and格式化功能.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json
from cafe.utils.github import (
    get_pr_comments,
    filter_unresolved_comments,
    format_comments_for_prompt,
    PRComment,
)


class TestGetPRComments:
    """測試獲取 PR comments 功能"""

    def test_get_pr_comments_success_with_resolved_status(self):
        """測試成功獲取 PR comments 並正確設置 resolved 狀態

        情境：gh api graphql 返回有效 GraphQL 響應，包含 isResolved 狀態
        預期：解析並返回 PRComment 列表，正確設置 is_resolved 欄位
        """
        # Mock gh repo view output (to get repo info)
        mock_repo_output = json.dumps({
            "owner": {"login": "testowner"},
            "name": "testrepo"
        })

        # Mock gh api graphql output
        mock_graphql_output = json.dumps({
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": True,  # 已解決
                                    "comments": {
                                        "nodes": [
                                            {
                                                "id": "PRRC_abc123",
                                                "databaseId": 123456,
                                                "body": "請修正這個 bug",
                                                "author": {"login": "reviewer1"},
                                                "createdAt": "2025-01-01T10:00:00Z",
                                                "path": "src/main.py",
                                                "line": 42
                                            }
                                        ]
                                    }
                                },
                                {
                                    "isResolved": False,  # 未解決
                                    "comments": {
                                        "nodes": [
                                            {
                                                "id": "PRRC_def456",
                                                "databaseId": 123457,
                                                "body": "這個看起來不錯",
                                                "author": {"login": "reviewer2"},
                                                "createdAt": "2025-01-01T11:00:00Z",
                                                "path": "src/utils.py",
                                                "line": 10
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        })

        with patch('subprocess.run') as mock_run:
            # Return different outputs for different calls
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=mock_repo_output, stderr=""),  # gh repo view
                MagicMock(returncode=0, stdout=mock_graphql_output, stderr="")  # gh api graphql
            ]

            comments = get_pr_comments(123)

            # Verify gh commands were called correctly
            assert mock_run.call_count == 2

            # First call should be gh repo view
            first_call_args = mock_run.call_args_list[0][0][0]
            assert "gh" in first_call_args
            assert "repo" in first_call_args
            assert "view" in first_call_args

            # Second call should be gh api graphql
            second_call_args = mock_run.call_args_list[1][0][0]
            assert "gh" in second_call_args
            assert "api" in second_call_args
            assert "graphql" in second_call_args

            # Verify results
            assert len(comments) == 2
            assert comments[0].id == "123456"
            assert comments[0].body == "請修正這個 bug"
            assert comments[0].author == "reviewer1"
            assert comments[0].is_resolved is True  # First thread is resolved

            assert comments[1].id == "123457"
            assert comments[1].body == "這個看起來不錯"
            assert comments[1].author == "reviewer2"
            assert comments[1].is_resolved is False  # Second thread is not resolved

    def test_get_pr_comments_pr_not_found(self):
        """測試 PR 不存在情況

        情境：gh api 返回錯誤（PR 不存在）
        預期：拋出 ValueError 異常
        """
        # Mock gh repo view output (successful)
        mock_repo_output = json.dumps({
            "owner": {"login": "testowner"},
            "name": "testrepo"
        })

        with patch('subprocess.run') as mock_run:
            # First call succeeds (gh repo view), second call fails (gh api)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=mock_repo_output, stderr=""),  # gh repo view
                MagicMock(returncode=1, stdout="", stderr="pull request not found")  # gh api
            ]

            with pytest.raises(ValueError, match="PR #999 not found"):
                get_pr_comments(999)

    def test_get_pr_comments_no_comments(self):
        """測試 PR 沒有 comments 情況

        情境：gh api graphql 返回空 review threads
        預期：返回空列表
        """
        # Mock gh repo view output
        mock_repo_output = json.dumps({
            "owner": {"login": "testowner"},
            "name": "testrepo"
        })

        # Mock gh api graphql output (empty review threads)
        mock_graphql_output = json.dumps({
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": []
                        }
                    }
                }
            }
        })

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=mock_repo_output, stderr=""),  # gh repo view
                MagicMock(returncode=0, stdout=mock_graphql_output, stderr="")  # gh api graphql
            ]

            comments = get_pr_comments(123)
            assert comments == []


class TestFilterUnresolvedComments:
    """測試過濾未 resolved comments 功能"""

    def test_filter_unresolved_comments_mixed(self):
        """測試過濾混合 resolved/unresolved comments

        情境：有 resolved and unresolved  comments
        預期：只返回 unresolved  comments
        """
        comments = [
            PRComment(
                id="C1",
                body="需要修正",
                author="user1",
                created_at="2025-01-01T10:00:00Z",
                path="file1.py",
                line=10,
                is_resolved=False
            ),
            PRComment(
                id="C2",
                body="已修正",
                author="user2",
                created_at="2025-01-01T11:00:00Z",
                path="file2.py",
                line=20,
                is_resolved=True
            ),
            PRComment(
                id="C3",
                body="還需要改",
                author="user3",
                created_at="2025-01-01T12:00:00Z",
                path="file3.py",
                line=30,
                is_resolved=False
            ),
        ]

        unresolved = filter_unresolved_comments(comments)

        assert len(unresolved) == 2
        assert unresolved[0].id == "C1"
        assert unresolved[1].id == "C3"
        assert all(not c.is_resolved for c in unresolved)

    def test_filter_unresolved_comments_all_resolved(self):
        """測試所有 comments 都已 resolved

        情境：所有 comments 都是 resolved
        預期：返回空列表
        """
        comments = [
            PRComment(
                id="C1",
                body="已修正",
                author="user1",
                created_at="2025-01-01T10:00:00Z",
                path="file1.py",
                line=10,
                is_resolved=True
            ),
        ]

        unresolved = filter_unresolved_comments(comments)
        assert unresolved == []

    def test_filter_unresolved_comments_empty_list(self):
        """測試空列表

        情境：輸入空列表
        預期：返回空列表
        """
        assert filter_unresolved_comments([]) == []


class TestFormatCommentsForPrompt:
    """測試格式化 comments 成 prompt 功能"""

    def test_format_comments_for_prompt_single(self):
        """測試格式化單一 comment

        情境：一個未 resolved  comment
        預期：生成格式化文字區塊
        """
        comments = [
            PRComment(
                id="C1",
                body="請修正這個 bug",
                author="reviewer1",
                created_at="2025-01-01T10:00:00Z",
                path="src/main.py",
                line=42,
                is_resolved=False
            ),
        ]

        result = format_comments_for_prompt(comments)

        assert "PR Review Comments" in result
        assert "1 unresolved comment" in result
        assert "src/main.py" in result
        assert "line 42" in result
        assert "reviewer1" in result
        assert "請修正這個 bug" in result

    def test_format_comments_for_prompt_multiple(self):
        """測試格式化多個 comments

        情境：多個未 resolved  comments
        預期：生成包含所有 comments 格式化文字
        """
        comments = [
            PRComment(
                id="C1",
                body="Comment 1",
                author="user1",
                created_at="2025-01-01T10:00:00Z",
                path="file1.py",
                line=10,
                is_resolved=False
            ),
            PRComment(
                id="C2",
                body="Comment 2",
                author="user2",
                created_at="2025-01-01T11:00:00Z",
                path="file2.py",
                line=20,
                is_resolved=False
            ),
        ]

        result = format_comments_for_prompt(comments)

        assert "2 unresolved comments" in result
        assert "Comment 1" in result
        assert "Comment 2" in result
        assert "file1.py" in result
        assert "file2.py" in result
        assert "user1" in result
        assert "user2" in result

    def test_format_comments_for_prompt_empty(self):
        """測試格式化空列表

        情境：沒有 comments
        預期：返回空字串or提示訊息
        """
        result = format_comments_for_prompt([])
        assert result == "" or "No unresolved comments" in result

    def test_format_comments_preserves_code_blocks(self):
        """測試保留 comment 中程式碼區塊

        情境：comment 包含 markdown 程式碼區塊
        預期：保留程式碼區塊格式
        """
        comments = [
            PRComment(
                id="C1",
                body="請改成：\n```python\ndef foo():\n    pass\n```",
                author="reviewer",
                created_at="2025-01-01T10:00:00Z",
                path="src/main.py",
                line=10,
                is_resolved=False
            ),
        ]

        result = format_comments_for_prompt(comments)

        assert "```python" in result
        assert "def foo():" in result
        assert "```" in result
