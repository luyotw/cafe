"""測試 interactive 和 non-interactive 模式的差異。

根據規格，兩種模式的差異應該只有：
1. 輸入參數方式不同
2. interactive 執行完一輪後若不是 CONFIRMED 狀態會自動進入下一輪
   non-interactive 只會進行一輪然後返回 IN_PROGRESS

除此之外其他行為都應該一模一樣。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.types import PhaseStatus, WorkflowMode, TokenUsage, SpecRigor, AgentConfig, AgentCLI
from aaf.phases.spec_phase import SpecPhase
from aaf.phases.plan_phase import PlanPhase


def setup_agent_manager_mock_for_spec(agent_manager: MagicMock) -> None:
    """Setup agent_manager.get_agent() mock for SpecPhase tests."""
    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name="Roger",
        cli=AgentCLI.CLAUDE,
        session_id="test-session"
    )
    agent_manager.get_agent.return_value = mock_agent


class TestSpecPhaseInteractiveVsNonInteractive:
    """測試 SpecPhase 的 interactive 和 non-interactive 模式差異。"""

    def test_confirmed_status_same_behavior_both_modes(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態在兩種模式下行為相同（都完成）"""
        # 準備測試環境
        issue_name = "test-confirmed"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # 測試 interactive mode
        phase_interactive = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase_interactive.display, 'get_multiline_input', return_value="需求"):
            result_interactive = phase_interactive.execute()

        # 測試 non-interactive mode
        agent_manager.execute.reset_mock()
        phase_noninteractive = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result_noninteractive = phase_noninteractive.execute()

        # 兩種模式都應該返回 COMPLETED
        assert result_interactive.status == PhaseStatus.COMPLETED
        assert result_noninteractive.status == PhaseStatus.COMPLETED
        assert result_interactive.data.get("status_code") == "AAF_CONFIRMED"
        assert result_noninteractive.data.get("status_code") == "AAF_CONFIRMED"

    def test_rejected_status_same_behavior_both_modes(self, tmp_path: Path) -> None:
        """測試 REJECTED 狀態在兩種模式下行為相同（都失敗）"""
        issue_name = "test-rejected"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("AAF_REJECTED\n需求有問題", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # 測試 interactive mode
        phase_interactive = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase_interactive.display, 'get_multiline_input', return_value="需求"):
            result_interactive = phase_interactive.execute()

        # 測試 non-interactive mode
        agent_manager.execute.reset_mock()
        phase_noninteractive = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result_noninteractive = phase_noninteractive.execute()

        # 兩種模式都應該返回 FAILED
        assert result_interactive.status == PhaseStatus.FAILED
        assert result_noninteractive.status == PhaseStatus.FAILED
        assert result_interactive.data.get("status_code") == "AAF_REJECTED"
        assert result_noninteractive.data.get("status_code") == "AAF_REJECTED"

    def test_need_clarification_interactive_continues(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 在 interactive 模式會繼續迭代"""
        issue_name = "test-clarification-interactive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次需要澄清，第二次確認
        agent_manager.execute.side_effect = [
            ("AAF_NEED_CLARIFICATION\n請補充資訊", TokenUsage()),
            ("AAF_CONFIRMED\n需求已清楚", TokenUsage()),
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

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        # Interactive 模式應該自動進入第二輪並完成
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 2
        assert agent_manager.execute.call_count == 2

    def test_need_clarification_noninteractive_stops(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 在 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-clarification-noninteractive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n請補充資訊", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式應該停止在第一輪
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("iterations") == 1
        assert result.data.get("status_code") == "AAF_NEED_CLARIFICATION"
        assert agent_manager.execute.call_count == 1

    def test_no_status_code_interactive_continues(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時 interactive 模式會繼續迭代"""
        issue_name = "test-no-code-interactive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次沒有狀態碼，第二次確認
        agent_manager.execute.side_effect = [
            ("這是回應但沒有狀態碼", TokenUsage()),
            ("AAF_CONFIRMED\n需求已清楚", TokenUsage()),
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

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        # Interactive 模式應該自動進入第二輪並完成
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2

    def test_no_status_code_noninteractive_stops(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-no-code-noninteractive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("這是回應但沒有狀態碼", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式應該停止在第一輪
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("iterations") == 1
        assert result.data.get("status_code") is None
        assert agent_manager.execute.call_count == 1


class TestPlanPhaseInteractiveVsNonInteractive:
    """測試 PlanPhase 的 interactive 和 non-interactive 模式差異。"""

    def test_confirmed_status_same_behavior_both_modes(self, tmp_path: Path) -> None:
        """測試 READY_FOR_REVIEW 狀態在兩種模式下行為相同（都完成）"""
        issue_name = "test-plan-confirmed"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("AAF_READY_FOR_REVIEW\n計畫已完成", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        # 測試 interactive mode (需要 mock user confirmation)
        phase_interactive = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'):
            result_interactive = phase_interactive.execute()

        # 測試 non-interactive mode
        agent_manager.execute.reset_mock()
        phase_noninteractive = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation in non-interactive mode
        )

        with patch('builtins.print'):
            result_noninteractive = phase_noninteractive.execute()

        # 兩種模式都應該返回 COMPLETED
        assert result_interactive.status == PhaseStatus.COMPLETED
        assert result_noninteractive.status == PhaseStatus.COMPLETED
        assert result_interactive.data.get("status_code") == "AAF_CONFIRMED"
        assert result_noninteractive.data.get("status_code") == "AAF_CONFIRMED"

    def test_need_clarification_interactive_continues(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 在 interactive 模式會繼續迭代"""
        issue_name = "test-plan-clarification-interactive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.side_effect = [
            ("AAF_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("AAF_READY_FOR_REVIEW\n計畫已完成", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # Interactive 模式應該自動進入第二輪並完成
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 3  # 1st: NEED_CLARIFICATION, 2nd: READY_FOR_REVIEW, 3rd: user confirms
        assert agent_manager.execute.call_count == 2  # Agent executes twice (not counting user confirmation)

    def test_need_clarification_noninteractive_stops(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 在 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-plan-clarification-noninteractive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n需要更多資訊", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式應該停止在第一輪
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("iterations") == 1
        assert result.data.get("status_code") == "AAF_NEED_CLARIFICATION"
        assert agent_manager.execute.call_count == 1

    def test_no_status_code_noninteractive_stops(self, tmp_path: Path) -> None:
        """測試沒有狀態碼時 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-plan-no-code-noninteractive"
        spec_file = tmp_path / ".aaf" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("這是回應但沒有狀態碼", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式應該停止在第一輪
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("iterations") == 1
        assert result.data.get("status_code") is None
        assert agent_manager.execute.call_count == 1
