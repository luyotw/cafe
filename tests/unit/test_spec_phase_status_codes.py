"""Tests for SpecPhase with status code system.

These tests verify status code parsing and handling in interactive mode,
where the phase runs to completion in a single execute() call.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage, SpecRigor, AgentConfig, AgentCLI
from cafe.phases.spec_phase import SpecPhase


def setup_agent_manager_mock_for_spec(agent_manager: MagicMock) -> None:
    """Setup agent_manager mocks for SpecPhase tests.

    SpecPhase calls agent_manager.get_agent() to get agent metadata,
    so we need to mock it to return an object with proper config.
    """
    # Create a mock agent with proper config
    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name="Roger",
        cli=AgentCLI.CLAUDE,
        session_id="test-session"
    )
    agent_manager.get_agent.return_value = mock_agent


class TestSpecPhaseWithStatusCodes:
    """Test SpecPhase with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼會完成 phase"""
        issue_name = "test-confirmed-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n需求已經很清楚了。", TokenUsage(), [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
        assert agent_manager.execute.call_count == 1

    def test_rejected_status_code_fails_phase(self, tmp_path: Path) -> None:
        """測試 REJECTED 狀態碼會失敗 phase"""
        issue_name = "test-rejected-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_REJECTED\n需求有問題，無法進行。", TokenUsage(), [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "CAFE_REJECTED"
        assert "rejected" in result.message.lower()

    def test_need_clarification_continues_iteration(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 狀態碼會繼續迭代（互動模式）"""
        issue_name = "test-clarification-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次回應需要澄清，第二次確認
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n請補充更多資訊。", TokenUsage()),
            ("CAFE_CONFIRMED\n需求已清楚。", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        # Mock the display's get_multiline_input method
        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2
        assert result.data.get("iterations") >= 2  # May have extra iteration for file load

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        issue_name = "test-middle-status-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("需求已經很清楚了。CAFE_CONFIRMED", TokenUsage(), [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"

    def test_no_status_code_continues_iteration(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時會繼續迭代直到有狀態碼（互動模式）"""
        issue_name = "test-no-status-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次沒有狀態碼，第二次有
        agent_manager.execute.side_effect = [
            ("我覺得需求不夠清楚。", TokenUsage()),  # 沒有狀態碼
            ("CAFE_CONFIRMED\n現在清楚了。", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        # Mock the display's get_multiline_input method
        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="我的回答"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不分大小寫"""
        issue_name = "test-case-insensitive-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("cafe_confirmed\n需求清楚。", TokenUsage(), [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
