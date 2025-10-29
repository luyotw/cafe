"""Tests for SpecPhase with status code system.

These tests verify status code parsing and handling in interactive mode,
where the phase runs to completion in a single execute() call.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseStatus, WorkflowMode, TokenUsage
from aaf.phases.spec_phase import SpecPhase


class TestSpecPhaseWithStatusCodes:
    """Test SpecPhase with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼會完成 phase"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已經很清楚了。"
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
        assert agent_manager.execute.call_count == 1

    def test_rejected_status_code_fails_phase(self, tmp_path: Path) -> None:
        """測試 REJECTED 狀態碼會失敗 phase"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "REJECTED\n需求有問題，無法進行。"
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "REJECTED"
        assert "rejected" in result.message.lower()

    def test_need_clarification_continues_iteration(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 狀態碼會繼續迭代（互動模式）"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # 第一次回應需要澄清，第二次確認
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請補充更多資訊。",
            "CONFIRMED\n需求已清楚。",
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock the display's get_multiline_input method
        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2
        assert result.data.get("iterations") == 2

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "需求已經很清楚了。CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"

    def test_no_status_code_continues_iteration(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時會繼續迭代直到有狀態碼（互動模式）"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # 第一次沒有狀態碼，第二次有
        agent_manager.execute.side_effect = [
            "我覺得需求不夠清楚。",  # 沒有狀態碼
            "CONFIRMED\n現在清楚了。",
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock the display's get_multiline_input method
        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="我的回答"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不分大小寫"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "confirmed\n需求清楚。"
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CONFIRMED"
