"""Tests for RequirementsPhase with status code system."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseStatus, WorkflowMode
from aaf.phases.requirements_phase import RequirementsPhase


class TestRequirementsPhaseWithStatusCodes:
    """Test RequirementsPhase with status code system."""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_confirmed_status_code_completes_phase(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試 CONFIRMED 狀態碼會完成 phase"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # Agent 回應包含 CONFIRMED 狀態碼
        agent_manager.execute.return_value = "CONFIRMED\n需求已經很清楚了。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
        assert agent_manager.execute.call_count == 1

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_rejected_status_code_fails_phase(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試 REJECTED 狀態碼會失敗 phase"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # Agent 回應包含 REJECTED 狀態碼
        agent_manager.execute.return_value = "REJECTED\n需求有問題，無法進行。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "REJECTED"
        assert "rejected" in result.message.lower()

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_need_clarification_continues_iteration(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試 NEED_CLARIFICATION 狀態碼會繼續迭代"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # 第一次回應需要澄清，第二次確認
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請補充更多資訊。",
            "CONFIRMED\n需求已清楚。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2
        assert result.data.get("iterations") == 2

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_status_code_in_middle_of_response(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試狀態碼在回應中間也能識別"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # 狀態碼不在第一行
        agent_manager.execute.return_value = "需求已經很清楚了。CONFIRMED"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_no_status_code_continues_iteration(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試沒有狀態碼時會繼續迭代直到有狀態碼"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # 第一次沒有狀態碼，第二次有
        agent_manager.execute.side_effect = [
            "我覺得需求不夠清楚。",  # 沒有狀態碼
            "CONFIRMED\n現在清楚了。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    def test_case_insensitive_status_code(
        self,
        mock_write: MagicMock,
        mock_read: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        """測試狀態碼不分大小寫"""
        mock_exists.return_value = True
        mock_read.return_value = "# Requirements\nTest requirements"

        agent_manager = MagicMock(spec=AgentManager)
        # 小寫的狀態碼
        agent_manager.execute.return_value = "confirmed\n需求清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
