"""Tests for PlanPhase with status codes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage
from cafe.phases.plan_phase import PlanPhase


class TestPlanPhaseWithStatusCodes:
    """Test PlanPhase integration with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Simulate user confirming the plan
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
        assert result.data.get("iterations") == 2  # 1st: agent returns READY_FOR_REVIEW, 2nd: user confirms

    def test_rejected_status_code_fails_phase(self, tmp_path: Path) -> None:
        """測試 REJECTED 狀態碼導致 phase 失敗"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_REJECTED\n分析無法進行。", TokenUsage(), [], None)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert result.data.get("status_code") == "CAFE_REJECTED"

    def test_need_clarification_continues_iteration(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 繼續迭代"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # First iteration needs clarification, second confirms
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n請補充更多資訊。", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user input and confirmation
        from unittest.mock import patch
        with patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c'):  # 'c' for confirm
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 3  # 1st: NEED_CLARIFICATION, 2nd: READY_FOR_REVIEW, 3rd: user confirms
        assert agent_manager.execute.call_count == 2  # Agent executes twice (not counting user confirmation)

    def test_status_code_in_middle_of_response(self, tmp_path: Path) -> None:
        """測試狀態碼在回應中間也能識別"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("分析結果：\nCAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Simulate user confirming the plan
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"

    def test_no_status_code_continues_iteration(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時繼續迭代"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # First has no status code, second has READY_FOR_REVIEW
        agent_manager.execute.side_effect = [
            ("這是一般的回應，沒有狀態碼。", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,  # Must be interactive to continue iterations
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="回應"), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    def test_case_insensitive_status_code(self, tmp_path: Path) -> None:
        """測試狀態碼不區分大小寫"""
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("cafe_ready_for_review\n實作分析已完成。", TokenUsage(), [], None)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Simulate user confirming the plan
        )

        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_CONFIRMED"
