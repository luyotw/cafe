"""Tests for AnalysisPhase with status codes."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.types import PhaseStatus, WorkflowMode
from aaf.phases.analysis_phase import AnalysisPhase


class TestAnalysisPhaseWithStatusCodes:
    """Test AnalysisPhase integration with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
        assert result.data.get("iterations") == 1

    def test_rejected_status_code_fails_phase(self, tmp_path: Path) -> None:
        """測試 REJECTED 狀態碼導致 phase 失敗"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "REJECTED\n分析無法進行。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "REJECTED"

    def test_need_clarification_continues_iteration(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 繼續迭代"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        # First iteration needs clarification, second confirms
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請補充更多資訊。",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 2
        assert agent_manager.execute.call_count == 2

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "分析結果：\nCONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"

    def test_no_status_code_continues_iteration(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時繼續迭代"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        # First has no status code, second has CONFIRMED
        agent_manager.execute.side_effect = [
            "這是一般的回應，沒有狀態碼。",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不區分大小寫"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "confirmed\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
