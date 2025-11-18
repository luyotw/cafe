"""測試 PlanPhase 的用戶確認流程。

正確的流程應該是：
1. Agent 回答後，在 interactive 模式下應該顯示給使用者確認
2. 只有當狀態碼是 CAFE_READY_FOR_REVIEW 時才需要用戶確認
3. 用戶選擇：
   - 確認 (c): 直接完成，**不再呼叫 agent**
   - 拒絕 (r): Phase 失敗
   - 修改 (m): 提供 feedback，進入下一輪（會再次呼叫 agent）
4. 在 non-interactive 模式下，CAFE_READY_FOR_REVIEW 直接完成，不需要確認
5. CAFE_NEED_CLARIFICATION 不需要用戶確認，直接進入下一輪
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage
from cafe.phases.plan_phase import PlanPhase


class TestPlanPhaseUserConfirmation:
    """測試 PlanPhase 用戶確認流程。"""

    def test_confirmed_interactive_waits_for_user_confirmation(self, tmp_path: Path) -> None:
        """測試 READY_FOR_REVIEW 時 interactive 模式等待用戶確認"""
        issue_name = "test-confirm"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user choosing 'c' (confirm)
        with patch('builtins.input', return_value='c') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # Should prompt user for confirmation
        assert mock_input.called, "應該提示用戶確認"

        # Should complete without calling agent again
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 1, "只應呼叫 agent 一次"

    def test_confirmed_interactive_user_rejects(self, tmp_path: Path) -> None:
        """測試用戶拒絕計畫"""
        issue_name = "test-reject"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user choosing 'r' (reject)
        with patch('builtins.input', return_value='r') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # Should fail after user rejects
        assert mock_input.called
        assert result.status == PhaseStatus.FAILED
        assert "reject" in result.message.lower()
        assert agent_manager.execute.call_count == 1, "只應呼叫 agent 一次"

    def test_confirmed_interactive_user_requests_modification(self, tmp_path: Path) -> None:
        """測試用戶要求修改，agent 應被呼叫第二次"""
        issue_name = "test-modify"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # First call: READY_FOR_REVIEW, second call after modification: READY_FOR_REVIEW again
        agent_manager.execute.side_effect = [
            ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n修改後的計畫", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user choosing 'm' (modify) first, then 'c' (confirm)
        with patch('builtins.input', side_effect=['m', 'c']) as mock_input, \
             patch.object(phase.display, 'get_multiline_input', return_value="請加上錯誤處理") as mock_multiline, \
             patch('builtins.print'):
            result = phase.execute()

        # Should prompt twice (first time 'm', second time 'c')
        assert mock_input.call_count == 2
        # Should ask for modification feedback
        assert mock_multiline.called
        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED
        # Should have called agent twice (first plan, then modified plan)
        assert agent_manager.execute.call_count == 2

    def test_confirmed_noninteractive_completes_immediately(self, tmp_path: Path) -> None:
        """測試 non-interactive 模式下 READY_FOR_REVIEW 直接完成，不需要用戶確認"""
        issue_name = "test-noninteractive"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,  # Non-interactive mode
            user_input="confirm",  # Provide user confirmation via parameter
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Should complete with user confirmation provided via parameter
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 1

    def test_need_clarification_does_not_need_confirmation(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 狀態不需要用戶確認，直接進入下一輪"""
        issue_name = "test-clarification"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user input for clarification and confirmation
        with patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # Should only prompt ONCE for confirmation (after second READY_FOR_REVIEW)
        # Not after NEED_CLARIFICATION
        assert mock_input.call_count == 1, "應該只在 READY_FOR_REVIEW 後提示確認"
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2
