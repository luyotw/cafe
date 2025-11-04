"""Tests for ReviewPhase with status codes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.types import PhaseStatus, WorkflowMode
from aaf.phases.review_phase import ReviewPhase


class TestReviewPhaseWithStatusCodes:
    """Test ReviewPhase integration with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CONFIRMED\nCode looks good!", TokenUsage())

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
        assert "passed" in result.message.lower()

    def test_needs_changes_status_code(self, tmp_path: Path) -> None:
        """測試 NEEDS_CHANGES 狀態碼（單輪執行）"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("NEEDS_CHANGES\n需要修正問題。", TokenUsage())

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "NEEDS_CHANGES"
        # Should only execute once (no iteration loop)
        assert agent_manager.execute.call_count == 1

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Review result:\nCONFIRMED\nAll checks passed.", TokenUsage())

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不區分大小寫"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("confirmed\nLooks good to me!", TokenUsage())

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
