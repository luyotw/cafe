"""Tests for ReviewPhase with status codes."""

from unittest.mock import MagicMock

import pytest

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.types import PhaseStatus, WorkflowMode
from aaf.phases.review_phase import ReviewPhase


class TestReviewPhaseWithStatusCodes:
    """Test ReviewPhase integration with status code system."""

    def test_approved_status_code_completes_phase(self) -> None:
        """測試 APPROVED 狀態碼完成 phase"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "APPROVED\nCode looks good!"

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "APPROVED"
        assert result.data.get("iterations") == 1

    def test_lgtm_status_code_completes_phase(self) -> None:
        """測試 LGTM 狀態碼完成 phase"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "LGTM\nLooks good to me!"

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "LGTM"

    def test_needs_changes_continues_iteration(self) -> None:
        """測試 NEEDS_CHANGES 繼續迭代"""
        agent_manager = MagicMock(spec=AgentManager)
        # First iteration needs changes, second approves
        agent_manager.execute.side_effect = [
            "NEEDS_CHANGES\n需要修正問題。",
            "Fix applied",  # Dev agent fix
            "APPROVED\nCode looks good now!",
        ]

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 2
        assert agent_manager.execute.call_count == 3  # review + fix + review

    def test_status_code_in_middle_of_response(self) -> None:
        """測試狀態碼在回應中間也能識別"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Review result:\nAPPROVED\nAll checks passed."

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "APPROVED"

    def test_max_iterations_reached(self) -> None:
        """測試達到最大迭代次數"""
        agent_manager = MagicMock(spec=AgentManager)
        # Always return NEEDS_CHANGES
        agent_manager.execute.return_value = "NEEDS_CHANGES\n需要修正問題。"

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
            max_iterations=2,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert "max reached" in result.message.lower() or "達到最大迭代次數" in result.message

    def test_case_insensitive_status_code(self) -> None:
        """測試狀態碼不區分大小寫"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "lgtm\nLooks good to me!"

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "LGTM"
