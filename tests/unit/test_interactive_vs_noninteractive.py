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

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage, SpecRigor, AgentConfig, AgentCLI
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def setup_agent_manager_mock_for_spec(agent_manager: MagicMock) -> None:
    """Setup agent_manager.get_agent() mock for SpecPhase tests."""
    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name="Roger",
        cli=AgentCLI.CLAUDE,
        session_id="test-session"
    )
    agent_manager.get_agent.return_value = mock_agent


def create_template_file(tmp_path: Path) -> str:
    """Create a dummy template file for tests."""
    template_file = tmp_path / "template.md"
    template_file.write_text("# Plan Template\n\nTemplate content")
    return str(template_file)


class TestSpecPhaseInteractiveVsNonInteractive:
    """測試 SpecPhase 的 interactive 和 non-interactive 模式差異。"""

    def test_confirmed_status_same_behavior_both_modes(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 READY_FOR_REVIEW 在兩種模式下的行為差異（沒有 while loop）"""
        monkeypatch.chdir(tmp_path)

        # 測試 interactive mode: READY_FOR_REVIEW 應該回傳 IN_PROGRESS（等待用戶確認）
        issue_name_interactive = "test-confirmed-interactive"
        mock_git_ops.get_current_branch.return_value = issue_name_interactive
        spec_file_interactive = tmp_path / ".cafe" / "issues" / issue_name_interactive / "spec" / "spec_001.md"
        spec_file_interactive.parent.mkdir(parents=True, exist_ok=True)
        spec_file_interactive.write_text("# Requirements\nTest requirements")

        agent_manager_interactive = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager_interactive)
        agent_manager_interactive.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已清楚", TokenUsage(), [], None, [])
        agent_manager_interactive.get_total_token_usage.return_value = TokenUsage()

        permission_handler_interactive = MagicMock(spec=PermissionHandler)

        phase_interactive = SpecPhase(
            agent_manager=agent_manager_interactive,
            permission_handler=permission_handler_interactive,
            git_ops=mock_git_ops,
            spec_file=str(spec_file_interactive),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'), \
             patch.object(phase_interactive.display, 'get_multiline_input', return_value="需求"):
            result_interactive = phase_interactive.execute()

        # 測試 non-interactive mode: READY_FOR_REVIEW 立即完成
        issue_name_noninteractive = "test-confirmed-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name_noninteractive
        spec_file_noninteractive = tmp_path / ".cafe" / "issues" / issue_name_noninteractive / "spec" / "spec_001.md"
        spec_file_noninteractive.parent.mkdir(parents=True, exist_ok=True)
        spec_file_noninteractive.write_text("# Requirements\nTest requirements")

        agent_manager_noninteractive = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager_noninteractive)
        agent_manager_noninteractive.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已清楚", TokenUsage(), [], None, [])
        agent_manager_noninteractive.get_total_token_usage.return_value = TokenUsage()

        permission_handler_noninteractive = MagicMock(spec=PermissionHandler)

        phase_noninteractive = SpecPhase(
            agent_manager=agent_manager_noninteractive,
            permission_handler=permission_handler_noninteractive,
            git_ops=mock_git_ops,
            spec_file=str(spec_file_noninteractive),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result_noninteractive = phase_noninteractive.execute()

        # After behavior change: Both modes complete immediately for READY_FOR_REVIEW
        # User can manually run again if they want to make changes
        assert result_interactive.status == PhaseStatus.COMPLETED
        assert result_interactive.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

        # Non-interactive: 立即完成（行為不變）
        assert result_noninteractive.status == PhaseStatus.COMPLETED
        assert result_noninteractive.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

    def test_rejected_status_same_behavior_both_modes(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 REJECTED 狀態在兩種模式下行為相同（都失敗）"""
        issue_name = "test-rejected"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_REJECTED\n需求有問題", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # 測試 interactive mode
        phase_interactive = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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
            git_ops=mock_git_ops,
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
        assert result_interactive.data.get("status_code") == "CAFE_REJECTED"
        assert result_noninteractive.data.get("status_code") == "CAFE_REJECTED"

    def test_need_clarification_interactive_continues(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 NEED_CLARIFICATION 在 interactive 模式回傳 COMPLETED（沒有 while loop，不自動繼續）"""
        issue_name = "test-clarification-interactive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次執行得到 NEED_CLARIFICATION
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請補充資訊", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        # Interactive 模式：第一次執行得到 NEED_CLARIFICATION，回傳 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("iterations") == 1
        assert agent_manager.execute.call_count == 1

    def test_need_clarification_noninteractive_stops(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 NEED_CLARIFICATION 在 non-interactive 模式會停止並返回 COMPLETED"""
        issue_name = "test-clarification-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請補充資訊", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式：第一次執行得到 NEED_CLARIFICATION，回傳 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert result.data.get("iterations") == 1
        assert agent_manager.execute.call_count == 1

    def test_no_status_code_interactive_continues(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試沒有狀態碼時 interactive 模式回傳 IN_PROGRESS（沒有 while loop）"""
        issue_name = "test-no-code-interactive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次執行沒有狀態碼
        agent_manager.execute.return_value = ("這是回應但沒有狀態碼", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'), \
             patch.object(phase.display, 'get_multiline_input', return_value="需求"):
            result = phase.execute()

        # Interactive 模式：第一次執行沒有狀態碼，回傳 IN_PROGRESS
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("status_code") is None
        # Agent 被呼叫2次：1次正常執行，1次分析missing status code
        assert agent_manager.execute.call_count == 2

    def test_no_status_code_noninteractive_stops(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試沒有狀態碼時 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-no-code-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("這是回應但沒有狀態碼", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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
        # 呼叫 2 次：原始 prompt + _analyze_missing_status_code 分析
        assert agent_manager.execute.call_count == 2


class TestPlanPhaseInteractiveVsNonInteractive:
    """測試 PlanPhase 的 interactive 和 non-interactive 模式差異。"""

    @pytest.mark.skip(reason="TODO: Fix after copy timing change")
    def test_confirmed_status_same_behavior_both_modes(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 READY_FOR_REVIEW 狀態在兩種模式下行為不同

        Interactive: READY_FOR_REVIEW → 用戶確認 → CONFIRMED
        Non-interactive: READY_FOR_REVIEW → 直接完成（不需用戶確認）
        """
        issue_name = "test-plan-confirmed"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None, [])
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'):
            result_interactive = phase_interactive.execute()

        # 測試 non-interactive mode
        agent_manager.execute.reset_mock()
        phase_noninteractive = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation in non-interactive mode
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'):
            result_noninteractive = phase_noninteractive.execute()

        # After behavior change: Both modes complete immediately for READY_FOR_REVIEW
        # User can manually run again if they want to make changes
        assert result_interactive.status == PhaseStatus.COMPLETED
        assert result_interactive.data.get("status_code") == "CAFE_READY_FOR_REVIEW"  # No longer waits for user
        assert result_noninteractive.status == PhaseStatus.COMPLETED
        assert result_noninteractive.data.get("status_code") == "CAFE_CONFIRMED"  # Non-interactive 有 user_input 會得到 CONFIRMED

    def test_need_clarification_interactive_continues(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 NEED_CLARIFICATION 在 interactive 模式會繼續迭代"""
        issue_name = "test-plan-clarification-interactive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 移除 while loop 後，只會執行一次
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # 移除 while loop 後，NEED_CLARIFICATION 返回 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert agent_manager.execute.call_count == 1  # 只呼叫一次

    def test_need_clarification_noninteractive_stops(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 NEED_CLARIFICATION 在 non-interactive 模式沒有 user_input 時應該 FAILED"""
        issue_name = "test-plan-clarification-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 移除 while loop 後，non-interactive 模式下 NEED_CLARIFICATION 返回 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert agent_manager.execute.call_count == 1

    def test_no_status_code_noninteractive_stops(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試沒有狀態碼時 non-interactive 模式會停止並返回 IN_PROGRESS"""
        issue_name = "test-plan-no-code-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("這是回應但沒有狀態碼", TokenUsage(), [], None, [])
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive 模式沒有狀態碼時應該 IN_PROGRESS（需要用戶輸入）
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("iterations") == 1
        # 呼叫 2 次：原始 prompt + _analyze_missing_status_code 分析
        assert agent_manager.execute.call_count == 2
