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
from cafe.core.git import GitOperations
from cafe.phases.spec_phase import SpecPhase



@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test"
    return git_ops

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

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 READY_FOR_REVIEW 狀態碼 + 用戶確認會完成 phase"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-confirmed-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已經很清楚了。", TokenUsage(), [], None, [])
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
            user_input="需求",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 沒有 while loop，interactive 模式下 READY_FOR_REVIEW 回傳 IN_PROGRESS
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        assert agent_manager.execute.call_count == 1

    def test_need_clarification_continues_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 NEED_CLARIFICATION 狀態碼回傳 IN_PROGRESS（沒有 while loop）"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-clarification-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次回應需要澄清
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請補充更多資訊。", TokenUsage(), [], None, [])
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
            user_input="補充資訊",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 沒有 while loop，第一次執行得到 NEED_CLARIFICATION 直接完成，用戶需手動再次執行
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert result.data.get("iterations") == 1

    def test_status_code_in_middle_of_response(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試狀態碼在回應中間也能識別"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-middle-status-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("需求已經很清楚了。CAFE_READY_FOR_REVIEW", TokenUsage(), [], None, [])
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
            user_input="需求",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 沒有 while loop，READY_FOR_REVIEW 回傳 IN_PROGRESS
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

    def test_no_status_code_continues_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試沒有狀態碼時回傳 IN_PROGRESS（沒有 while loop）"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-no-status-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        # 第一次沒有狀態碼
        agent_manager.execute.return_value = ("我覺得需求不夠清楚。", TokenUsage(), [], None, [])
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
            user_input="我的回答",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 沒有 while loop，沒有狀態碼回傳 IN_PROGRESS
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("status_code") is None

    def test_case_insensitive_status_code(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試狀態碼不分大小寫"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-case-insensitive-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager)
        agent_manager.execute.return_value = ("cafe_ready_for_review\n需求清楚。", TokenUsage(), [], None, [])
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
            user_input="需求",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 沒有 while loop，READY_FOR_REVIEW 回傳 IN_PROGRESS
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
