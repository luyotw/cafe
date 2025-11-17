"""Tests for ReviewPhase with status codes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage
from cafe.phases.review_phase import ReviewPhase


class TestReviewPhaseWithStatusCodes:
    """Test ReviewPhase integration with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path, AgentCLI) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
        assert "passed" in result.message.lower()

    def test_needs_changes_status_code(self, tmp_path: Path) -> None:
        """測試 NEEDS_CHANGES 狀態碼（單輪執行）"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEEDS_CHANGES\n需要修正問題。", TokenUsage(), [], None)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEEDS_CHANGES"
        # Should only execute once (no iteration loop)
        assert agent_manager.execute.call_count == 1

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Review result:\nCAFE_CONFIRMED\nAll checks passed.", TokenUsage(), [], None)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不區分大小寫"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("cafe_confirmed\nLooks good to me!", TokenUsage(), [], None)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
