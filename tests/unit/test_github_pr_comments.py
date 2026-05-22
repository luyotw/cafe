"""Unit tests for GitHub PR comments utilities.

測試 GitHub PR comments 獲取、過濾and格式化功能.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json
from cafe.utils.github import (
    get_pr_comments,
    get_pr_timeline_comments,
    get_all_pr_comments,
    filter_unresolved_comments,
    format_comments_for_prompt,
    get_processed_comment_ids_from_history,
    PRComment,
    GitHubError,
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


class TestGetPRTimelineComments:
    """測試獲取 PR timeline comments 功能"""

    def test_get_pr_timeline_comments_success(self):
        """測試成功獲取 PR timeline comments

        情境：gh pr view 返回有效的 timeline comments
        預期：解析並返回 PRComment 列表，comment_type 為 "timeline"
        """
        mock_pr_output = json.dumps({
            "comments": [
                {
                    "id": "IC_kwDOQCpNoM111",
                    "body": "整體來說不錯，但請加上文檔",
                    "author": {"login": "maintainer"},
                    "createdAt": "2025-01-02T09:00:00Z"
                },
                {
                    "id": "IC_kwDOQCpNoM222",
                    "body": "我同意這個改動",
                    "author": {"login": "contributor"},
                    "createdAt": "2025-01-03T10:00:00Z"
                }
            ]
        })

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_pr_output,
                stderr=""
            )

            comments = get_pr_timeline_comments(123)

            # Verify gh command was called
            assert mock_run.call_count == 1
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "pr" in call_args
            assert "view" in call_args
            assert "123" in call_args or 123 in call_args
            assert "--json" in call_args
            assert "comments" in call_args

            # Verify results
            assert len(comments) == 2
            assert comments[0].id == "IC_kwDOQCpNoM111"
            assert comments[0].body == "整體來說不錯，但請加上文檔"
            assert comments[0].author == "maintainer"
            assert comments[0].comment_type == "timeline"
            assert comments[0].path is None
            assert comments[0].line is None

            assert comments[1].id == "IC_kwDOQCpNoM222"
            assert comments[1].body == "我同意這個改動"
            assert comments[1].comment_type == "timeline"

    def test_get_pr_timeline_comments_pr_not_found(self):
        """測試 PR 不存在情況

        情境：gh pr view 返回 PR not found 錯誤
        預期：拋出 ValueError 異常
        """
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="pull request not found"
            )

            with pytest.raises(ValueError, match="PR #999 not found"):
                get_pr_timeline_comments(999)

    def test_get_pr_timeline_comments_no_comments(self):
        """測試 PR 沒有 timeline comments 情況

        情境：gh pr view 返回空 comments 列表
        預期：返回空列表
        """
        mock_pr_output = json.dumps({
            "comments": []
        })

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_pr_output,
                stderr=""
            )

            comments = get_pr_timeline_comments(123)
            assert comments == []


class TestGetAllPRComments:
    """測試獲取所有 PR comments（review + timeline）功能"""

    def test_get_all_pr_comments_success_both_types(self):
        """測試成功獲取兩種類型的 comments

        情境：review comments 和 timeline comments 都獲取成功
        預期：返回合併的 PRComment 列表
        """
        # Mock review comments
        review_comment = PRComment(
            id="R1",
            body="這行有 bug",
            author="reviewer1",
            created_at="2025-01-01T10:00:00Z",
            path="src/main.py",
            line=42,
            is_resolved=False,
            comment_type="review"
        )

        # Mock timeline comments
        timeline_comment = PRComment(
            id="T1",
            body="整體來說不錯",
            author="maintainer",
            created_at="2025-01-02T09:00:00Z",
            comment_type="timeline"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline:

            mock_review.return_value = [review_comment]
            mock_timeline.return_value = [timeline_comment]

            comments = get_all_pr_comments(123)

            # Should call both functions
            mock_review.assert_called_once_with(123)
            mock_timeline.assert_called_once_with(123)

            # Should return both types of comments
            assert len(comments) == 2
            assert comments[0].comment_type == "review"
            assert comments[1].comment_type == "timeline"

    def test_get_all_pr_comments_review_fails_timeline_succeeds(self):
        """測試 review comments 失敗但 timeline comments 成功

        情境：獲取 review comments 失敗，但 timeline comments 成功
        預期：返回 timeline comments（graceful degradation）
        """
        timeline_comment = PRComment(
            id="T1",
            body="整體來說不錯",
            author="maintainer",
            created_at="2025-01-02T09:00:00Z",
            comment_type="timeline"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline:

            # Review comments fail
            mock_review.side_effect = GitHubError("GraphQL API error")
            # Timeline comments succeed
            mock_timeline.return_value = [timeline_comment]

            comments = get_all_pr_comments(123)

            # Should still return timeline comments
            assert len(comments) == 1
            assert comments[0].comment_type == "timeline"

    def test_get_all_pr_comments_timeline_fails_review_succeeds(self):
        """測試 timeline comments 失敗但 review comments 成功

        情境：獲取 timeline comments 失敗，但 review comments 成功
        預期：返回 review comments（graceful degradation）
        """
        review_comment = PRComment(
            id="R1",
            body="這行有 bug",
            author="reviewer1",
            created_at="2025-01-01T10:00:00Z",
            path="src/main.py",
            line=42,
            is_resolved=False,
            comment_type="review"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline:

            # Review comments succeed
            mock_review.return_value = [review_comment]
            # Timeline comments fail
            mock_timeline.side_effect = ValueError("PR not found")

            comments = get_all_pr_comments(123)

            # Should still return review comments
            assert len(comments) == 1
            assert comments[0].comment_type == "review"

    def test_get_all_pr_comments_both_fail(self):
        """測試兩種 comments 都失敗

        情境：review comments 和 timeline comments 都獲取失敗
        預期：拋出 GitHubError 異常
        """
        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline:

            # Both fail
            mock_review.side_effect = GitHubError("GraphQL API error")
            mock_timeline.side_effect = ValueError("PR not found")

            with pytest.raises(GitHubError, match="Failed to get any comments"):
                get_all_pr_comments(123)

    def test_get_all_pr_comments_exclude_ids_none_returns_all(self):
        """測試 exclude_ids=None 時行為與原本相同

        情境：不傳入 exclude_ids（預設 None）
        預期：返回所有 comments，行為與原本相同
        """
        review_comment = PRComment(
            id="R1",
            body="這行有 bug",
            author="reviewer1",
            created_at="2025-01-01T10:00:00Z",
            comment_type="review"
        )
        timeline_comment = PRComment(
            id="T1",
            body="整體來說不錯",
            author="maintainer",
            created_at="2025-01-02T09:00:00Z",
            comment_type="timeline"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline, \
             patch('cafe.utils.github.get_pr_review_body_comments') as mock_review_body:

            mock_review.return_value = [review_comment]
            mock_timeline.return_value = [timeline_comment]
            mock_review_body.return_value = []

            # 不傳入 exclude_ids（預設 None）
            comments = get_all_pr_comments(123)

            assert len(comments) == 2
            assert {c.id for c in comments} == {"R1", "T1"}

    def test_get_all_pr_comments_exclude_ids_filters_some(self):
        """測試 exclude_ids 過濾部分 comments

        情境：傳入 exclude_ids，包含部分 comment ID
        預期：只返回不在 exclude_ids 中的新 comments
        """
        review_comment = PRComment(
            id="R1",
            body="舊的 review comment",
            author="reviewer1",
            created_at="2025-01-01T10:00:00Z",
            comment_type="review"
        )
        timeline_comment_old = PRComment(
            id="T1",
            body="舊的 timeline comment",
            author="maintainer",
            created_at="2025-01-02T09:00:00Z",
            comment_type="timeline"
        )
        timeline_comment_new = PRComment(
            id="T2",
            body="新的 timeline comment",
            author="reviewer2",
            created_at="2025-01-03T10:00:00Z",
            comment_type="timeline"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline, \
             patch('cafe.utils.github.get_pr_review_body_comments') as mock_review_body:

            mock_review.return_value = [review_comment]
            mock_timeline.return_value = [timeline_comment_old, timeline_comment_new]
            mock_review_body.return_value = []

            # 排除 R1 和 T1（上一輪已看過的）
            comments = get_all_pr_comments(123, exclude_ids={"R1", "T1"})

            assert len(comments) == 1
            assert comments[0].id == "T2"

    def test_get_all_pr_comments_exclude_ids_filters_all(self):
        """測試 exclude_ids 過濾所有 comments

        情境：傳入 exclude_ids，包含所有 comment ID
        預期：返回空列表（沒有新 comments）
        """
        review_comment = PRComment(
            id="R1",
            body="已看過的 review comment",
            author="reviewer1",
            created_at="2025-01-01T10:00:00Z",
            comment_type="review"
        )
        timeline_comment = PRComment(
            id="T1",
            body="已看過的 timeline comment",
            author="maintainer",
            created_at="2025-01-02T09:00:00Z",
            comment_type="timeline"
        )

        with patch('cafe.utils.github.get_pr_comments') as mock_review, \
             patch('cafe.utils.github.get_pr_timeline_comments') as mock_timeline, \
             patch('cafe.utils.github.get_pr_review_body_comments') as mock_review_body:

            mock_review.return_value = [review_comment]
            mock_timeline.return_value = [timeline_comment]
            mock_review_body.return_value = []

            # 排除所有 comment ID
            comments = get_all_pr_comments(123, exclude_ids={"R1", "T1"})

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

    def test_format_comments_with_mixed_comment_types(self):
        """測試格式化包含 review 和 timeline 兩種 comment 類型

        情境：comments 列表包含 review comments 和 timeline comments
        預期：分別在不同區段顯示兩種 comment，標題區分 review 和 timeline
        """
        comments = [
            PRComment(
                id="R1",
                body="這行有 bug，請修正",
                author="reviewer1",
                created_at="2025-01-01T10:00:00Z",
                path="src/main.py",
                line=42,
                is_resolved=False,
                comment_type="review"
            ),
            PRComment(
                id="T1",
                body="整體來說不錯，但請加上文檔",
                author="maintainer",
                created_at="2025-01-02T09:00:00Z",
                comment_type="timeline"
            ),
        ]

        result = format_comments_for_prompt(comments)

        # 應該有兩個區段
        assert "PR Review Comments" in result
        assert "PR Discussion Comments" in result

        # Review comment 應該包含 file location
        assert "src/main.py" in result
        assert "line 42" in result
        assert "這行有 bug，請修正" in result

        # Timeline comment 不應該有 location
        assert "整體來說不錯，但請加上文檔" in result

        # Both should include authors
        assert "reviewer1" in result
        assert "maintainer" in result

    def test_format_comments_includes_comment_ids(self):
        """測試格式化輸出包含 comment ID

        情境：格式化 comments 時需要輸出 comment ID 以便追蹤
        預期：每個 comment 的輸出包含 [#ID] 格式的標識
        """
        comments = [
            PRComment(
                id="123456",
                body="請修正這個 bug",
                author="reviewer1",
                created_at="2025-01-01T10:00:00Z",
                path="src/main.py",
                line=42,
                is_resolved=False,
                comment_type="review"
            ),
            PRComment(
                id="789",
                body="很棒的工作！",
                author="maintainer",
                created_at="2025-01-02T09:00:00Z",
                comment_type="timeline"
            ),
        ]

        result = format_comments_for_prompt(comments)

        # 應該包含 comment ID
        assert "[#123456]" in result or "ID: 123456" in result or "Comment ID: 123456" in result
        assert "[#789]" in result or "ID: 789" in result or "Comment ID: 789" in result

    def test_format_comments_with_only_review_comments_backward_compat(self):
        """測試只有 review comments 的情況（向後兼容）

        情境：只有 review comments，沒有 timeline comments（舊行為）
        預期：格式化輸出正常，標題只顯示 review comments 區段
        """
        comments = [
            PRComment(
                id="C1",
                body="請修正這個錯誤",
                author="reviewer1",
                created_at="2025-01-01T10:00:00Z",
                path="src/main.py",
                line=42,
                is_resolved=False,
                comment_type="review"
            ),
            PRComment(
                id="C2",
                body="這裡需要改進",
                author="reviewer2",
                created_at="2025-01-01T11:00:00Z",
                path="src/utils.py",
                line=10,
                is_resolved=False,
                comment_type="review"
            ),
        ]

        result = format_comments_for_prompt(comments)

        # 應該包含 review comments 標題
        assert "PR Review Comments" in result
        assert "2 unresolved comment" in result

        # 不應該有 timeline comments 區段（因為沒有 timeline comments）
        # 注意：這個檢查可能需要根據實際實作調整

        # 應該包含所有 review comment 內容
        assert "請修正這個錯誤" in result
        assert "這裡需要改進" in result
        assert "src/main.py" in result
        assert "src/utils.py" in result


class TestParseCommentProcessingResults:
    """測試解析 comment processing results 功能"""

    def test_parse_comment_processing_results_normal_case(self):
        """測試解析正常的 comment processing summary

        情境：Agent 回應包含完整的 Comment Processing Summary
        預期：正確解析 processed 和 skipped comments
        """
        from cafe.utils.github import parse_comment_processing_results

        agent_response = """
I have addressed the PR comments.

### Processed Comments
- [#123] Fixed the type error in main.py
- [#456] Added documentation as requested
- [#789] Refactored the function for better readability

### Skipped Comments
- [#999] "Nice work!" - Acknowledgment only, no action required
"""

        result = parse_comment_processing_results(agent_response)

        assert "processed" in result
        assert "skipped" in result

        # Check processed comments
        assert len(result["processed"]) == 3
        assert result["processed"][0]["id"] == "123"
        assert "Fixed the type error" in result["processed"][0]["description"]
        assert result["processed"][1]["id"] == "456"
        assert result["processed"][2]["id"] == "789"

        # Check skipped comments
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == "999"
        assert "Acknowledgment only" in result["skipped"][0]["reason"]

    def test_parse_comment_processing_results_missing_section(self):
        """測試缺少 Comment Processing Summary 的情況

        情境：Agent 回應沒有包含 Comment Processing Summary
        預期：返回空的 processed 和 skipped 列表
        """
        from cafe.utils.github import parse_comment_processing_results

        agent_response = """
I have made some changes to the code.
All tests are passing now.
"""

        result = parse_comment_processing_results(agent_response)

        assert result["processed"] == []
        assert result["skipped"] == []

    def test_parse_comment_processing_results_malformed_input(self):
        """測試格式錯誤的輸入

        情境：Comment Processing Summary 存在但格式不正確
        預期：盡可能解析，忽略格式錯誤的行
        """
        from cafe.utils.github import parse_comment_processing_results

        agent_response = """
### Processed Comments
- [#123] Fixed issue
- Invalid line without ID
- [#456] Another fix

### Skipped Comments
- [#789] Skipped for reason
- Also invalid
"""

        result = parse_comment_processing_results(agent_response)

        # Should parse valid lines and skip invalid ones
        assert len(result["processed"]) == 2
        assert result["processed"][0]["id"] == "123"
        assert result["processed"][1]["id"] == "456"

        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == "789"

    def test_parse_comment_processing_results_different_bullet_formats(self):
        """測試不同的 bullet 格式

        情境：Agent 使用不同的 bullet 符號（-, *, •）或沒有 bullet
        預期：所有格式都能正確解析
        """
        from cafe.utils.github import parse_comment_processing_results

        agent_response = """
### Processed Comments
- [#123] Fixed with dash bullet
* [#456] Fixed with asterisk bullet
• [#789] Fixed with bullet point
[#999] Fixed without bullet

### Skipped Comments
- [#111] Skipped with dash
* [#222] Skipped with asterisk
• [#333] Skipped with bullet point
"""

        result = parse_comment_processing_results(agent_response)

        # Should parse all different bullet formats
        assert len(result["processed"]) == 4
        assert result["processed"][0]["id"] == "123"
        assert result["processed"][1]["id"] == "456"
        assert result["processed"][2]["id"] == "789"
        assert result["processed"][3]["id"] == "999"

        assert len(result["skipped"]) == 3
        assert result["skipped"][0]["id"] == "111"
        assert result["skipped"][1]["id"] == "222"
        assert result["skipped"][2]["id"] == "333"

    def test_parse_comment_processing_results_with_github_style_ids(self):
        """測試解析帶有 GitHub 樣式 ID 的評論（包含字母、下劃線）

        情境：Agent 回應包含真實的 GitHub comment IDs（如 IC_kwDOQCpNoM7h2rLv）
        預期：正確解析這些非純數字的 IDs
        """
        from cafe.utils.github import parse_comment_processing_results

        agent_response = """
confirmed

### Processed Comments
- [#IC_kwDOQCpNoM7hfWZl] Fixed the bug in authentication flow

### Skipped Comments
- [#IC_kwDOQCpNoM7h2rLv] Test comment - no action needed, verifying the comment tracking system works correctly
"""

        result = parse_comment_processing_results(agent_response)

        # Should parse GitHub-style IDs correctly
        assert len(result["processed"]) == 1
        assert result["processed"][0]["id"] == "IC_kwDOQCpNoM7hfWZl"
        assert "Fixed the bug" in result["processed"][0]["description"]

        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == "IC_kwDOQCpNoM7h2rLv"
        assert "Test comment" in result["skipped"][0]["reason"]


class TestGetProcessedCommentIDs:
    """測試從歷史記錄中獲取已處理的 comment IDs"""

    def test_get_processed_comment_ids_from_single_iteration(self):
        """測試從單個 iteration 中獲取已處理的 comment IDs

        情境：只有一個 iteration，其中包含已處理的 comments
        預期：返回該 iteration 中所有已處理的 comment IDs
        """
        from cafe.utils.github import get_processed_comment_ids_from_history
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            # Create a mock iteration directory with iteration.json
            iteration_dir = Path(tmpdir) / "iteration_001"
            iteration_dir.mkdir(parents=True)

            context_data = {
                "iteration": 1,
                "pr_comments_processed": [
                    {"id": "123", "description": "Fixed bug"},
                    {"id": "456", "description": "Added feature"}
                ],
                "pr_comments_skipped": [
                    {"id": "789", "reason": "Not applicable"}
                ]
            }

            with open(iteration_dir / "iteration.json", "w") as f:
                json.dump(context_data, f)

            # Get processed comment IDs
            result = get_processed_comment_ids_from_history(Path(tmpdir))

            # Should include both processed and skipped comments
            assert "123" in result
            assert "456" in result
            assert "789" in result
            assert len(result) == 3

    def test_get_processed_comment_ids_from_multiple_iterations(self):
        """測試從多個 iterations 中獲取已處理的 comment IDs

        情境：有多個 iterations，每個都包含不同的已處理 comments
        預期：返回所有 iterations 中的已處理 comment IDs 集合
        """
        from cafe.utils.github import get_processed_comment_ids_from_history
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            # Create iteration 1
            iter1_dir = Path(tmpdir) / "iteration_001"
            iter1_dir.mkdir(parents=True)
            context1 = {
                "iteration": 1,
                "pr_comments_processed": [{"id": "111", "description": "Fix 1"}],
                "pr_comments_skipped": []
            }
            with open(iter1_dir / "iteration.json", "w") as f:
                json.dump(context1, f)

            # Create iteration 2
            iter2_dir = Path(tmpdir) / "iteration_002"
            iter2_dir.mkdir(parents=True)
            context2 = {
                "iteration": 2,
                "pr_comments_processed": [{"id": "222", "description": "Fix 2"}],
                "pr_comments_skipped": [{"id": "333", "reason": "Skip"}]
            }
            with open(iter2_dir / "iteration.json", "w") as f:
                json.dump(context2, f)

            # Create iteration 3
            iter3_dir = Path(tmpdir) / "iteration_003"
            iter3_dir.mkdir(parents=True)
            context3 = {
                "iteration": 3,
                "pr_comments_processed": [],
                "pr_comments_skipped": [{"id": "444", "reason": "Skip 2"}]
            }
            with open(iter3_dir / "iteration.json", "w") as f:
                json.dump(context3, f)

            # Get processed comment IDs
            result = get_processed_comment_ids_from_history(Path(tmpdir))

            # Should include all comments from all iterations
            assert "111" in result
            assert "222" in result
            assert "333" in result
            assert "444" in result
            assert len(result) == 4

    def test_get_processed_comment_ids_no_history(self):
        """測試當沒有歷史記錄時的情況

        情境：phase_dir 不存在或沒有 iteration 目錄
        預期：返回空集合
        """
        from cafe.utils.github import get_processed_comment_ids_from_history
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            result = get_processed_comment_ids_from_history(Path(tmpdir))
            assert len(result) == 0

    def test_get_processed_comment_ids_ignores_malformed_files(self):
        """測試當某些 iteration.json 文件格式錯誤時的情況

        情境：有些 iteration 的 iteration.json 存在但沒有 comment 數據
        預期：跳過格式錯誤的文件，返回有效的 comment IDs
        """
        from cafe.utils.github import get_processed_comment_ids_from_history
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            # Create iteration 1 with valid data
            iter1_dir = Path(tmpdir) / "iteration_001"
            iter1_dir.mkdir(parents=True)
            context1 = {
                "iteration": 1,
                "pr_comments_processed": [{"id": "111", "description": "Fix"}]
            }
            with open(iter1_dir / "iteration.json", "w") as f:
                json.dump(context1, f)

            # Create iteration 2 with no comment data
            iter2_dir = Path(tmpdir) / "iteration_002"
            iter2_dir.mkdir(parents=True)
            context2 = {"iteration": 2}  # Missing pr_comments_processed
            with open(iter2_dir / "iteration.json", "w") as f:
                json.dump(context2, f)

            # Get processed comment IDs
            result = get_processed_comment_ids_from_history(Path(tmpdir))

            # Should only include valid comment IDs
            assert "111" in result
            assert len(result) == 1


def test_load_pr_last_seen_comment_ids_reads_artifact(tmp_path: Path) -> None:
    from cafe.utils.github import load_pr_last_seen_comment_ids

    pr_dir = tmp_path / "pr"
    art = pr_dir / "artifacts" / "pr_last_seen_comments.json"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text(
        json.dumps({"last_seen_comment_ids": ["a", "b"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert load_pr_last_seen_comment_ids(pr_dir) == {"a", "b"}


def test_load_pr_last_seen_comment_ids_legacy_context(tmp_path: Path) -> None:
    from cafe.utils.github import load_pr_last_seen_comment_ids

    pr_dir = tmp_path / "pr"
    it = pr_dir / "iteration_001"
    it.mkdir(parents=True)
    (it / "iteration.json").write_text(
        json.dumps({"last_seen_comment_ids": ["x"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert load_pr_last_seen_comment_ids(pr_dir) == {"x"}


def test_persist_last_seen_comment_ids_round_trip(tmp_path: Path) -> None:
    from cafe.utils.github import (
        load_last_seen_comment_ids_from_artifact,
        load_pr_last_seen_comment_ids,
        persist_last_seen_comment_ids,
    )

    pr_dir = tmp_path / "pr"
    persist_last_seen_comment_ids(pr_dir, ["1", "2"])
    assert load_last_seen_comment_ids_from_artifact(pr_dir) == {"1", "2"}
    assert load_pr_last_seen_comment_ids(pr_dir) == {"1", "2"}


def test_load_last_seen_comment_ids_from_artifact_missing_returns_none(tmp_path: Path) -> None:
    from cafe.utils.github import load_last_seen_comment_ids_from_artifact

    assert load_last_seen_comment_ids_from_artifact(tmp_path / "pr") is None


class TestPostPrTodoList:
    def _setup_todo_iteration(self, issue_dir: Path, todo_content: str) -> None:
        pr_dir = issue_dir / "pr" / "iteration_001"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "user_input.md").write_text("reviewer comment", encoding="utf-8")
        (pr_dir / "output.md").write_text(todo_content, encoding="utf-8")

    def test_all_items_checked_calls_add_pr_comment(self, tmp_path: Path) -> None:
        from cafe.utils.github import GitHubOps, post_pr_todo_list

        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        self._setup_todo_iteration(
            issue_dir, "## Todo List\n\n- [x] Fix bug\n- [x] Add test\n"
        )
        github_ops = MagicMock(spec=GitHubOps)
        post_pr_todo_list(
            issue_dir=issue_dir,
            pr_number="42",
            github_ops=github_ops,
            post_todo_list=True,
        )
        github_ops.add_pr_comment.assert_called_once()
        assert "Fix bug" in github_ops.add_pr_comment.call_args[0][1]

    def test_unchecked_item_skips_comment(self, tmp_path: Path) -> None:
        from cafe.utils.github import GitHubOps, post_pr_todo_list

        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        self._setup_todo_iteration(
            issue_dir, "## Todo List\n\n- [x] Done\n- [ ] Not done\n"
        )
        github_ops = MagicMock(spec=GitHubOps)
        post_pr_todo_list(
            issue_dir=issue_dir,
            pr_number="42",
            github_ops=github_ops,
            post_todo_list=True,
        )
        github_ops.add_pr_comment.assert_not_called()

    def test_post_todo_list_false_skips(self, tmp_path: Path) -> None:
        from cafe.utils.github import GitHubOps, post_pr_todo_list

        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        self._setup_todo_iteration(issue_dir, "## Todo List\n\n- [x] Done\n")
        github_ops = MagicMock(spec=GitHubOps)
        post_pr_todo_list(
            issue_dir=issue_dir,
            pr_number="42",
            github_ops=github_ops,
            post_todo_list=False,
        )
        github_ops.add_pr_comment.assert_not_called()

    def test_picks_latest_iteration_with_user_input(self, tmp_path: Path) -> None:
        from cafe.utils.github import GitHubOps, post_pr_todo_list

        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        pr_base = issue_dir / "pr"
        old = pr_base / "iteration_001"
        old.mkdir(parents=True)
        (old / "user_input.md").write_text("old", encoding="utf-8")
        (old / "output.md").write_text("## Todo List\n\n- [ ] Old\n", encoding="utf-8")
        latest = pr_base / "iteration_003"
        latest.mkdir(parents=True)
        (latest / "user_input.md").write_text("new", encoding="utf-8")
        (latest / "output.md").write_text("## Todo List\n\n- [x] New done\n", encoding="utf-8")
        github_ops = MagicMock(spec=GitHubOps)
        post_pr_todo_list(
            issue_dir=issue_dir,
            pr_number="42",
            github_ops=github_ops,
            post_todo_list=True,
        )
        assert "New done" in github_ops.add_pr_comment.call_args[0][1]

    def test_skips_non_todo_output(self, tmp_path: Path) -> None:
        from cafe.utils.github import GitHubOps, post_pr_todo_list

        issue_dir = tmp_path / ".cafe" / "issues" / "test"
        pr_dir = issue_dir / "pr" / "iteration_001"
        pr_dir.mkdir(parents=True)
        (pr_dir / "user_input.md").write_text("comments", encoding="utf-8")
        (pr_dir / "output.md").write_text(
            "# Fix login\n\n## Summary\nBody only.", encoding="utf-8"
        )
        github_ops = MagicMock(spec=GitHubOps)
        post_pr_todo_list(
            issue_dir=issue_dir,
            pr_number="42",
            github_ops=github_ops,
            post_todo_list=True,
        )
        github_ops.add_pr_comment.assert_not_called()
