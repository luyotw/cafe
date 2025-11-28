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
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, WorkflowMode, TokenUsage
from cafe.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def create_template_file(tmp_path: Path) -> str:
    """Create a dummy template file for tests."""
    template_file = tmp_path / "template.md"
    template_file.write_text("# Plan Template\n\nTemplate content")
    return str(template_file)


class TestPlanPhaseUserConfirmation:
    """測試 PlanPhase 用戶確認流程。"""

    def test_confirmed_interactive_waits_for_user_confirmation(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 READY_FOR_REVIEW 時 interactive 模式等待用戶確認"""
        issue_name = "test-confirm"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        # Mock user choosing 'c' (confirm) - but this won't be used anymore
        with patch('builtins.input', return_value='c') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # After removing while loop and changing behavior to complete immediately,
        # READY_FOR_REVIEW in both interactive and non-interactive modes returns COMPLETED
        # User confirmation is no longer automatic - if user wants to modify, they run again manually
        assert not mock_input.called, "不再提示用戶確認"
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        assert agent_manager.execute.call_count == 1, "只應呼叫 agent 一次"

    def test_confirmed_interactive_user_rejects(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試用戶拒絕計畫"""
        issue_name = "test-reject"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        # Mock user choosing 'r' (reject) - but this won't be used anymore
        with patch('builtins.input', return_value='r') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # After changing behavior, READY_FOR_REVIEW returns COMPLETED immediately
        # User can manually run again if they want to make changes
        assert not mock_input.called, "不再提示用戶確認"
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        assert agent_manager.execute.call_count == 1, "只應呼叫 agent 一次"

    def test_confirmed_interactive_user_requests_modification(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試用戶要求修改，agent 應被呼叫第二次"""
        issue_name = "test-modify"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # After removing while loop, only first call happens in single execute()
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        # Mock user choosing 'm' (modify) - but this won't be used anymore
        with patch('builtins.input', return_value='m') as mock_input, \
             patch.object(phase.display, 'get_multiline_input', return_value="請加上錯誤處理") as mock_multiline, \
             patch('builtins.print'):
            result = phase.execute()

        # After changing behavior, READY_FOR_REVIEW returns COMPLETED immediately
        # User modification happens by running the command again manually with their feedback
        assert mock_input.call_count == 0, "不再提示用戶確認"
        assert not mock_multiline.called, "不再請求修改意見"
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        assert agent_manager.execute.call_count == 1

    def test_confirmed_noninteractive_completes_immediately(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 non-interactive 模式下 READY_FOR_REVIEW 直接完成，不需要用戶確認"""
        issue_name = "test-noninteractive"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,  # Non-interactive mode
            user_input="confirm",  # Provide user confirmation via parameter
            template_path=create_template_file(tmp_path),
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Should complete with user confirmation provided via parameter
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 1

    def test_need_clarification_does_not_need_confirmation(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 NEED_CLARIFICATION 狀態不需要用戶確認，直接進入下一輪"""
        issue_name = "test-clarification"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # After removing while loop, only first call happens
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None)
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
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        # Mock user input for clarification
        with patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c') as mock_input, \
             patch('builtins.print'):
            result = phase.execute()

        # After removing while loop, NEED_CLARIFICATION returns COMPLETED (no automatic continuation)
        # User needs to run command again manually if they want to continue
        # No input() prompt after NEED_CLARIFICATION, only get_multiline_input
        # The 'c' from input() is never called because we don't reach READY_FOR_REVIEW
        assert mock_input.call_count == 0, "NEED_CLARIFICATION 不應該提示確認"
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert agent_manager.execute.call_count == 1
