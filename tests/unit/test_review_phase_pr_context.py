"""Unit tests for ReviewPhase PR comments integration.

測試 ReviewPhase 整合 PR comments 的功能。
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from cafe.phases.review_phase import ReviewPhase
from cafe.utils.github import PRComment
from cafe.core.types import WorkflowMode


class TestReviewPhasePRCommentsIntegration:
    """測試 ReviewPhase 整合 PR comments"""

    @pytest.fixture
    def mock_components(self, tmp_path):
        """創建 mock 的 components"""
        # Create test files
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Test Spec\n\nTest specification.")

        plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Test Plan\n\nTest plan.")

        # Mock components
        agent_manager = Mock()
        permission_handler = Mock()
        git_ops = Mock()

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "spec_file": str(spec_file),
            "plan_file": str(plan_file),
            "tmp_path": tmp_path,
        }

    def test_load_pr_comments_success(self, mock_components):
        """測試成功加載 PR comments

        情境：提供有效的 PR number，成功獲取未 resolved comments
        預期：返回格式化的 comments 字串及數量
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.review_phase.format_comments_for_prompt') as mock_format:
                    # Setup mocks
                    mock_comments = [
                        PRComment(
                            id="C1",
                            body="Fix this bug",
                            author="reviewer",
                            created_at="2025-01-01T10:00:00Z",
                            path="src/main.py",
                            line=42,
                            is_resolved=False
                        )
                    ]
                    mock_get.return_value = mock_comments
                    mock_filter.return_value = mock_comments
                    mock_format.return_value = "Formatted comments"

                    # Create phase with PR number
                    phase = ReviewPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        review_agent="Richard",
                        pr_number=123,
                    )

                    # Test that PR comments are loaded
                    result, count = phase._load_pr_comments()

                    assert result == "Formatted comments"
                    assert count == 1
                    mock_get.assert_called_once_with(123)
                    mock_filter.assert_called_once_with(mock_comments)
                    mock_format.assert_called_once_with(mock_comments)

    def test_load_pr_comments_no_pr_number(self, mock_components):
        """測試沒有提供 PR number

        情境：創建 ReviewPhase 時沒有提供 pr_number
        預期：_load_pr_comments 返回空字串及 0
        """
        phase = ReviewPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            review_agent="Richard",
        )

        result, count = phase._load_pr_comments()
        assert result == ""
        assert count == 0

    def test_load_pr_comments_pr_not_found(self, mock_components):
        """測試 PR 不存在

        情境：提供的 PR number 不存在
        預期：_load_pr_comments 返回空字串並記錄錯誤
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            mock_get.side_effect = ValueError("PR #999 not found")

            phase = ReviewPhase(
                agent_manager=mock_components["agent_manager"],
                permission_handler=mock_components["permission_handler"],
                git_ops=mock_components["git_ops"],
                spec_file=mock_components["spec_file"],
                plan_file=mock_components["plan_file"],
                workflow_mode=WorkflowMode.LOCAL,
                review_agent="Richard",
                pr_number=999,
            )

            result, count = phase._load_pr_comments()
            assert result == ""
            assert count == 0

    def test_load_pr_comments_no_unresolved(self, mock_components):
        """測試 PR 沒有未 resolved 的 comments

        情境：PR 有 comments，但全部都已 resolved
        預期：返回空字串（沒有需要處理的 comments）
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.review_phase.format_comments_for_prompt') as mock_format:
                    mock_comments = [
                        PRComment(
                            id="C1",
                            body="LGTM",
                            author="reviewer",
                            created_at="2025-01-01T10:00:00Z",
                            is_resolved=True
                        )
                    ]
                    mock_get.return_value = mock_comments
                    mock_filter.return_value = []  # All resolved
                    mock_format.return_value = ""

                    phase = ReviewPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        review_agent="Richard",
                        pr_number=123,
                    )

                    result, count = phase._load_pr_comments()
                    assert result == ""
                    assert count == 0

    def test_prompt_includes_pr_comments(self, mock_components):
        """測試 prompt 包含 PR comments

        情境：有未 resolved 的 PR comments
        預期：生成的 prompt 包含 PR comments 內容
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.review_phase.format_comments_for_prompt') as mock_format:
                    mock_comments = [
                        PRComment(
                            id="C1",
                            body="Fix this bug",
                            author="user",
                            created_at="2025-01-01T10:00:00Z",
                            path="src/main.py",
                            line=42,
                            is_resolved=False
                        )
                    ]
                    mock_get.return_value = mock_comments
                    mock_filter.return_value = mock_comments
                    formatted_comments = "=== PR Comments ===\nFix this bug"
                    mock_format.return_value = formatted_comments

                    phase = ReviewPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        review_agent="Richard",
                        pr_number=123,
                    )

                    # Initialize review_dir (normally done in execute())
                    phase._initialize_history_dir()

                    # Generate prompt
                    prompt = phase._generate_prompt("")

                    # Verify PR comments are in prompt
                    assert formatted_comments in prompt
                    assert "Fix this bug" in prompt

    def test_execute_with_no_unresolved_comments_returns_completed(self, mock_components):
        """測試當 PR 沒有 unresolved comments 時 execute 直接返回 COMPLETED

        情境：提供 pr_number 但 PR 沒有 unresolved comments
        預期：execute() 返回 COMPLETED 狀態且不呼叫 agent
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                # Setup: PR exists but has no unresolved comments
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="LGTM",
                        author="reviewer",
                        created_at="2025-01-01T10:00:00Z",
                        is_resolved=True
                    )
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = []  # All resolved

                # Create phase with PR number
                phase = ReviewPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    review_agent="Richard",
                    pr_number=123,
                )

                # Execute
                result = phase.execute()

                # Verify: Should return COMPLETED without calling agent
                assert result.status.value == "completed"
                assert "no unresolved comments" in result.message.lower()
                assert result.data["pr_number"] == 123

                # Verify agent was NOT called
                mock_components["agent_manager"].execute_agent.assert_not_called()

    def test_get_latest_pr_comment_timestamp(self, mock_components):
        """測試取得最新 PR comment 的時間戳

        情境：有多個 PR comments，需要找到最新的時間戳
        預期：返回最新 comment 的 created_at 時間
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                # 3 個 comments，按時間排序
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="Old comment",
                        author="user1",
                        created_at="2025-01-01T09:00:00Z",
                        is_resolved=False
                    ),
                    PRComment(
                        id="C2",
                        body="Middle comment",
                        author="user2",
                        created_at="2025-01-02T09:00:00Z",
                        is_resolved=False
                    ),
                    PRComment(
                        id="C3",
                        body="Latest comment",
                        author="user3",
                        created_at="2025-01-03T09:00:00Z",  # 最新的
                        is_resolved=False
                    ),
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = mock_comments

                phase = ReviewPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    review_agent="Richard",
                    pr_number=123,
                )

                timestamp = phase._get_latest_pr_comment_timestamp()

                # 應該返回最新 comment 的時間
                assert timestamp is not None
                assert "2025-01-03" in timestamp.isoformat()

    def test_check_commits_since_pr_comments(self, mock_components):
        """測試檢查 PR comments 之後的 commits

        情境：有新的 commits 在 PR comments 之後
        預期：呼叫 git_ops.get_commits_since 並返回新 commits
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                # 最後一個 PR comment 的時間：2025-01-02 10:00:00
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="Latest comment",
                        author="user1",
                        created_at="2025-01-02T10:00:00Z",
                        is_resolved=False
                    ),
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = mock_comments

                phase = ReviewPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    review_agent="Richard",
                    pr_number=123,
                )

                # Mock git log 返回新的 commits
                mock_components["git_ops"].get_commits_since = Mock(return_value=[
                    {"hash": "abc123", "message": "New commit after PR comment"}
                ])

                # 檢查是否有新 commits
                has_new_commits = phase._has_commits_since_pr_comments()

                # 應該返回 True
                assert has_new_commits is True

                # 驗證呼叫 git_ops.get_commits_since
                mock_components["git_ops"].get_commits_since.assert_called_once()

    def test_execute_with_no_new_commits_returns_completed(self, mock_components):
        """測試當沒有新 commits 時 execute 直接返回 COMPLETED

        情境：提供 pr_number 但所有 commits 都在 PR comments 之前
        預期：execute() 返回 COMPLETED 狀態且不呼叫 agent
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                # PR comment 時間：2025-01-02 10:00:00
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="Latest comment",
                        author="user1",
                        created_at="2025-01-02T10:00:00Z",
                        is_resolved=False
                    )
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = mock_comments

                phase = ReviewPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    review_agent="Richard",
                    pr_number=123,
                )

                # Mock: 沒有新的 commits
                mock_components["git_ops"].get_commits_since = Mock(return_value=[])
                mock_components["git_ops"].get_current_branch = Mock(return_value="test-issue")

                result = phase.execute()

                # 應該返回 COMPLETED 因為沒有新的 commits
                assert result.status.value == "completed"
                assert "no new commits" in result.message.lower()

                # Agent 不應該被呼叫
                mock_components["agent_manager"].execute_agent.assert_not_called()

    def test_has_commits_returns_true_with_new_commits(self, mock_components):
        """測試當有新 commits 時 _has_commits_since_pr_comments 返回 True

        情境：提供 pr_number 且 git 有新 commits
        預期：_has_commits_since_pr_comments() 返回 True
        """
        with patch('cafe.phases.review_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.review_phase.filter_unresolved_comments') as mock_filter:
                # PR comment 時間：2025-01-02 10:00:00
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="Please review",
                        author="user1",
                        created_at="2025-01-02T10:00:00Z",
                        is_resolved=False
                    )
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = mock_comments

                phase = ReviewPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    review_agent="Richard",
                    pr_number=123,
                )

                # Mock: 有新的 commits
                mock_components["git_ops"].get_commits_since = Mock(return_value=[
                    {"hash": "abc123", "message": "New commit"}
                ])

                # 測試方法行為
                has_new_commits = phase._has_commits_since_pr_comments()

                # 應該返回 True（有新 commits）
                assert has_new_commits is True
