"""Integration tests for develop-review feedback loop."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from aaf.phases.develop_phase import DevelopPhase
from aaf.phases.review_phase import ReviewPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.types import PhaseStatus, WorkflowMode, TokenUsage


@pytest.mark.integration
class TestDevelopReviewLoop:
    """測試 develop-review 迴圈整合"""

    def test_develop_detects_review_feedback_in_second_iteration(self, tmp_path: Path) -> None:
        """測試第二次執行 develop 時能檢測並使用 review feedback"""
        # Setup: Create issue structure
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# 需求規格\n\n修正登入功能")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## 實作計畫\n- [ ] 修正登入邏輯")

        # First develop cycle - no review feedback exists
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        develop_phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )

        # Execute first develop - should use normal development prompt
        result1 = develop_phase.execute()
        assert result1.status == PhaseStatus.COMPLETED

        # Verify first prompt doesn't contain review feedback
        first_call_args = agent_manager.execute.call_args_list[0]
        first_prompt = first_call_args[0][1]
        assert "Review Feedback" not in first_prompt
        assert "請按照實作計畫執行開發工作" in first_prompt

        # Simulate review phase creating review.md
        review_file = tmp_path / ".aaf" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("""# Code Review Feedback

## Critical Issues
- Commit message 不符合專案風格
- 需要使用 `git commit --fixup=reword:<SHA>` 修正

## Minor Issues
- 程式碼註解需要改善
""")

        # Second develop cycle - review feedback exists
        agent_manager.reset_mock()
        agent_manager.execute.return_value = "Fixes applied. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Clear status and history to start fresh (simulating a new develop command)
        status_file = tmp_path / ".aaf" / "issues" / "test" / "develop" / "status.json"
        if status_file.exists():
            status_file.unlink()
        history_dir = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history"
        if history_dir.exists():
            for f in history_dir.glob("*.json"):
                f.unlink()

        develop_phase2 = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )

        # Execute second develop - should detect and use review feedback
        result2 = develop_phase2.execute()
        assert result2.status == PhaseStatus.COMPLETED

        # Verify second prompt contains review feedback instructions
        second_call_args = agent_manager.execute.call_args_list[0]
        second_prompt = second_call_args[0][1]
        assert "Review Feedback" in second_prompt
        assert "Code Review" in second_prompt
        assert str(review_file) in second_prompt
        assert "修正" in second_prompt

    def test_review_feedback_file_path_consistency(self, tmp_path: Path) -> None:
        """測試 DevelopPhase 和 ReviewPhase 使用相同的 review.md 路徑"""
        spec_file = tmp_path / ".aaf" / "issues" / "myissue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "myissue" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        # Check DevelopPhase's review file path
        develop_phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        develop_review_path = develop_phase._get_review_file_path()

        # Expected path based on the pattern used in both phases
        expected_review_path = tmp_path / ".aaf" / "issues" / "myissue" / "review" / "review.md"
        assert develop_review_path == expected_review_path

    def test_multiple_review_cycles(self, tmp_path: Path) -> None:
        """測試多次 review 循環的情境"""
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        # Cycle 1: develop -> review (rejected)
        agent_manager.execute.return_value = "Development done. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        develop1 = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )
        develop1.execute()

        # Create first review feedback
        review_file = tmp_path / ".aaf" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review 1\n\nFix issue A")

        # Cycle 2: develop (with feedback) -> review (rejected again)
        agent_manager.reset_mock()
        agent_manager.execute.return_value = "Fixed issue A. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Clear status and history to start fresh
        status_file = tmp_path / ".aaf" / "issues" / "test" / "develop" / "status.json"
        if status_file.exists():
            status_file.unlink()
        history_dir = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history"
        if history_dir.exists():
            for f in history_dir.glob("*.json"):
                f.unlink()

        develop2 = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )
        result2 = develop2.execute()
        assert result2.status == PhaseStatus.COMPLETED

        # Verify develop2 detected review feedback
        call_args = agent_manager.execute.call_args_list[0]
        prompt = call_args[0][1]
        assert "Review Feedback" in prompt

        # Update review feedback
        review_file.write_text("# Review 2\n\nFix issue B")

        # Cycle 3: develop (with new feedback)
        agent_manager.reset_mock()
        agent_manager.execute.return_value = "Fixed issue B. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Clear status and history to start fresh
        if status_file.exists():
            status_file.unlink()
        if history_dir.exists():
            for f in history_dir.glob("*.json"):
                f.unlink()

        develop3 = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )
        result3 = develop3.execute()
        assert result3.status == PhaseStatus.COMPLETED

        # Verify develop3 still detects review feedback
        call_args = agent_manager.execute.call_args_list[0]
        prompt = call_args[0][1]
        assert "Review Feedback" in prompt
        assert str(review_file) in prompt
