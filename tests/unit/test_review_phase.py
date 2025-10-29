"""Tests for ReviewPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.review_phase import ReviewPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestReviewPhaseBasics:
    """Test basic ReviewPhase functionality."""

    def test_init_review_phase(self) -> None:
        """測試初始化 ReviewPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.git_ops == git_ops
        assert phase.spec_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_max_iterations(self) -> None:
        """測試設定最大迭代次數"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
            max_iterations=5,
        )

        assert phase.max_iterations == 5


class TestReviewLoop:
    """Test review-fix loop."""

    def test_single_review_iteration_lgtm(self, tmp_path: Path) -> None:
        """測試單次 review 迭代通過"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # Review agent approves immediately
        agent_manager.execute.return_value = "LGTM\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 1

    def test_multiple_review_fix_iterations(self, tmp_path: Path) -> None:
        """測試多次 review-fix 迭代"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # First review finds issues, second review approves
        agent_manager.execute.side_effect = [
            "問題 1: 需要修正",  # Review 1
            "已修正",  # Fix 1
            "LGTM\nCode looks good!",  # Review 2
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 3

    def test_max_iterations_reached(self, tmp_path: Path) -> None:
        """測試達到最大迭代次數"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # Always find issues, never approve
        agent_manager.execute.return_value = "還有問題需要修正"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            max_iterations=2,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 2


class TestDiffChecking:
    """Test diff checking."""

    def test_no_diff_fails(self, tmp_path: Path) -> None:
        """測試沒有 diff 時失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = ""  # No diff

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "no changes" in result.message.lower()

    def test_diff_includes_in_review_prompt(self, tmp_path: Path) -> None:
        """測試 diff 包含在 review prompt 中"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "LGTM\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff --git a/file.py"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Check that diff was included in review prompt
        call_args = agent_manager.execute.call_args_list[0][0]
        prompt = call_args[1]
        assert "diff --git a/file.py" in prompt


class TestAgentSelection:
    """Test agent selection for review and fix."""

    def test_uses_review_agent_and_dev_agent(self, tmp_path: Path) -> None:
        """測試使用 review agent 和 dev agent"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = ["問題", "已修正", "LGTM\nCode looks good!"]

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            review_agent="Roger",
            dev_agent="David",
        )

        phase.execute()

        # Check agents were used correctly
        calls = agent_manager.execute.call_args_list
        assert calls[0][0][0] == "Roger"  # Review
        assert calls[1][0][0] == "David"  # Fix
        assert calls[2][0][0] == "Roger"  # Review again


class TestPromptGeneration:
    """Test prompt generation for review and fix."""

    def test_first_review_prompt(self, tmp_path: Path) -> None:
        """測試第一次 review 的 prompt"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "LGTM\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "第 1 輪" in prompt
        assert "程式碼變更" in prompt

    def test_subsequent_review_includes_history(self, tmp_path: Path) -> None:
        """測試後續 review 包含歷史記錄提示"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = ["問題", "已修正", "LGTM\nCode looks good!"]

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Check second review includes history hint
        second_review_call = agent_manager.execute.call_args_list[2][0]
        prompt = second_review_call[1]
        assert "第 2 輪" in prompt


class TestGitHubWorkflow:
    """Test GitHub workflow."""

    def test_github_workflow_uses_issue(self) -> None:
        """測試 GitHub workflow 使用 issue"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "LGTM\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "gh issue view 123" in prompt


class TestErrorHandling:
    """Test error handling."""

    def test_git_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 git 錯誤時 phase 失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.side_effect = Exception("Git error")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git error" in result.message
