"""Unit tests for DevelopPhase PR comments integration.

測試 DevelopPhase 整合 PR comments 的功能。
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from cafe.phases.develop_phase import DevelopPhase
from cafe.utils.github import PRComment
from cafe.core.types import WorkflowMode


class TestDevelopPhasePRCommentsIntegration:
    """測試 DevelopPhase 整合 PR comments"""

    @pytest.fixture
    def mock_components(self, tmp_path, monkeypatch):
        """創建 mock 的 components"""
        # Change to tmp_path directory
        monkeypatch.chdir(tmp_path)

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
        git_ops.get_current_branch.return_value = "test-issue"

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
        預期：返回格式化的 comments 字串
        """
        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.develop_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.develop_phase.format_comments_for_prompt') as mock_format:
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
                    phase = DevelopPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        issue_name="test-issue",
                        dev_agent="David",
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

        情境：創建 DevelopPhase 時沒有提供 pr_number
        預期：_load_pr_comments 返回空字串
        """
        phase = DevelopPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            dev_agent="David",
        )

        result, count = phase._load_pr_comments()
        assert result == ""
        assert count == 0

    def test_load_pr_comments_pr_not_found(self, mock_components):
        """測試 PR 不存在

        情境：提供的 PR number 不存在
        預期：_load_pr_comments 返回空字串並記錄錯誤
        """
        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            mock_get.side_effect = ValueError("PR #999 not found")

            phase = DevelopPhase(
                agent_manager=mock_components["agent_manager"],
                permission_handler=mock_components["permission_handler"],
                git_ops=mock_components["git_ops"],
                spec_file=mock_components["spec_file"],
                plan_file=mock_components["plan_file"],
                workflow_mode=WorkflowMode.LOCAL,
                issue_name="test-issue",
                dev_agent="David",
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
        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.develop_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.develop_phase.format_comments_for_prompt') as mock_format:
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

                    phase = DevelopPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        issue_name="test-issue",
                        dev_agent="David",
                        pr_number=123,
                    )

                    result, count = phase._load_pr_comments()
                    assert result == ""
                    assert count == 0

    def test_load_pr_comments_with_no_unresolved_returns_empty(self, mock_components):
        """測試當 PR 沒有 unresolved comments 時 _load_pr_comments 返回空字串

        情境：提供 pr_number 但 PR 沒有 unresolved comments
        預期：_load_pr_comments() 返回空字串和 count=0，但不阻止 develop 執行
        """
        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.develop_phase.filter_unresolved_comments') as mock_filter:
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
                phase = DevelopPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    issue_name="test-issue",
                    dev_agent="David",
                    pr_number=123,
                )

                # Load PR comments
                result, count = phase._load_pr_comments()

                # Verify: Should return empty (no unresolved comments)
                assert result == ""
                assert count == 0

    def test_load_pr_comments_filters_old_comments(self, mock_components):
        """測試 PR comments 過濾：只載入比上次 develop 更新的 comments

        情境：有 3 個 unresolved comments，其中 2 個比上次 develop 舊，1 個比較新
        預期：只返回 1 個新的 comment
        """
        import json

        # 模擬上次 develop 時間：2025-01-02 10:00:00
        last_develop_time = "2025-01-02T10:00:00Z"
        status_file = mock_components["tmp_path"] / ".cafe" / "issues" / "test-issue" / "develop" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "timestamp": last_develop_time,
            "iteration": 1
        }))

        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.develop_phase.filter_unresolved_comments') as mock_filter:
                with patch('cafe.phases.develop_phase.format_comments_for_prompt') as mock_format:
                    # 3 個 comments：2 個舊的，1 個新的
                    mock_comments = [
                        PRComment(
                            id="C1",
                            body="Old comment 1",
                            author="user1",
                            created_at="2025-01-01T09:00:00Z",  # 比 develop 舊
                            is_resolved=False
                        ),
                        PRComment(
                            id="C2",
                            body="Old comment 2",
                            author="user2",
                            created_at="2025-01-02T09:00:00Z",  # 比 develop 舊
                            is_resolved=False
                        ),
                        PRComment(
                            id="C3",
                            body="New comment",
                            author="user3",
                            created_at="2025-01-02T11:00:00Z",  # 比 develop 新
                            is_resolved=False
                        ),
                    ]
                    mock_get.return_value = mock_comments
                    mock_filter.return_value = mock_comments  # 全部都是 unresolved

                    # format_comments_for_prompt 應該只收到新的 comment
                    mock_format.return_value = "New comment"

                    phase = DevelopPhase(
                        agent_manager=mock_components["agent_manager"],
                        permission_handler=mock_components["permission_handler"],
                        git_ops=mock_components["git_ops"],
                        spec_file=mock_components["spec_file"],
                        plan_file=mock_components["plan_file"],
                        workflow_mode=WorkflowMode.LOCAL,
                        issue_name="test-issue",
                        dev_agent="David",
                        pr_number=123,
                    )

                    result, count = phase._load_pr_comments()

                    # 應該只格式化新的 comment
                    assert count == 1
                    mock_format.assert_called_once()
                    # 檢查傳給 format_comments_for_prompt 的只有新 comment
                    formatted_comments = mock_format.call_args[0][0]
                    assert len(formatted_comments) == 1
                    assert formatted_comments[0].id == "C3"

    def test_load_pr_comments_filters_old_comments_returns_empty(self, mock_components):
        """測試當 PR 只有舊的 unresolved comments 時 _load_pr_comments 返回空字串

        情境：提供 pr_number 但所有 unresolved comments 都比上次 develop 舊
        預期：_load_pr_comments() 返回空字串和 count=0（舊 comments 被過濾掉）
        """
        import json

        # 模擬上次 develop 時間
        last_develop_time = "2025-01-02T10:00:00Z"
        status_file = mock_components["tmp_path"] / ".cafe" / "issues" / "test-issue" / "develop" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "timestamp": last_develop_time,
            "iteration": 1
        }))

        with patch('cafe.phases.develop_phase.get_pr_comments') as mock_get:
            with patch('cafe.phases.develop_phase.filter_unresolved_comments') as mock_filter:
                # 所有 comments 都是舊的
                mock_comments = [
                    PRComment(
                        id="C1",
                        body="Old comment",
                        author="user1",
                        created_at="2025-01-01T09:00:00Z",  # 比 develop 舊
                        is_resolved=False
                    )
                ]
                mock_get.return_value = mock_comments
                mock_filter.return_value = mock_comments

                phase = DevelopPhase(
                    agent_manager=mock_components["agent_manager"],
                    permission_handler=mock_components["permission_handler"],
                    git_ops=mock_components["git_ops"],
                    spec_file=mock_components["spec_file"],
                    plan_file=mock_components["plan_file"],
                    workflow_mode=WorkflowMode.LOCAL,
                    issue_name="test-issue",
                    dev_agent="David",
                    pr_number=123,
                )

                # Load PR comments
                result, count = phase._load_pr_comments()

                # 應該返回空（所有 comments 都是舊的）
                assert result == ""
                assert count == 0
