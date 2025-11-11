"""Tests for SpecPhase."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch, call
from io import StringIO

from aaf.phases.spec_phase import SpecPhase
from aaf.agents.manager import AgentManager
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode, TokenUsage
from aaf.core.permission import PermissionHandler
from aaf.core.status_codes import PhaseStatusCode


def create_mock_pm_agent(phase: SpecPhase, content: str, status_code: str = "AAF_CONFIRMED") -> Callable:
    """Create a mock PM agent that writes to spec.md before returning status code.

    Args:
        phase: SpecPhase instance to get spec_file from
        content: Content to write to spec.md
        status_code: Status code to return (CONFIRMED, NEED_CLARIFICATION, etc.)

    Returns:
        Mock function that can be used as agent_manager.execute side_effect
    """
    def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
        # Write spec content to spec.md
        spec_file = Path(phase.spec_file)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text(content)
        return status_code

    return mock_execute


def setup_agent_manager_mocks(agent_manager: MagicMock) -> None:
    """Setup standard mocks for agent_manager used by SpecPhase.

    Args:
        agent_manager: MagicMock object to setup
    """
    # Mock get_agent for _execute_agent_iteration (from Phase base class)
    mock_agent = MagicMock()
    mock_agent.config.cli.value = "claude"
    mock_agent.config.session_id = "test_session"
    agent_manager.get_agent.return_value = mock_agent

    # Mock get_agent_config for other methods
    agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

    # Mock get_total_token_usage
    agent_manager.get_total_token_usage.return_value = TokenUsage()


class TestSpecPhaseBasics:
    """Test basic SpecPhase functionality."""

    def test_init_spec_phase(self) -> None:
        """測試初始化 SpecPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.spec_file == "spec.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestAgentSelection:
    """Test PM agent selection."""

    def test_uses_pm_agent(self, tmp_path: Path) -> None:
        """測試使用 PM agent (Roger)"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚。", TokenUsage())
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            pm_agent="Roger",
        )

        phase.execute()

        # Check that Roger was used
        call_args = agent_manager.execute.call_args[0]
        assert call_args[0] == "Roger"


class TestHistoryTracking:
    """Test conversation history tracking for agents without session support."""

    def test_history_directory_structure(self, tmp_path: Path) -> None:
        """測試歷史記錄目錄結構包含 phase 資訊"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚。", TokenUsage())

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Verify history directory path includes phase1 and is alongside requirements file
        assert phase.history_dir == tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "history"

    def test_save_iteration_history_creates_json(self, tmp_path: Path) -> None:
        """測試儲存迭代歷史會建立 JSON 檔案，包含 user_input（輪的開始）"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n請問使用者是誰？", TokenUsage())

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Manually set iteration and save history using base class method
        phase.iteration = 1
        phase._save_iteration_history(
            phase_specific_data={
                "status": PhaseStatusCode.NEED_CLARIFICATION.value,
                "user_input": "Initial requirements\n",  # 輪的開始：使用者故事
                "pm_response": "請問使用者是誰？",
                "confirmed_requirements": phase.confirmed_requirements.copy(),
                "pending_questions": phase.pending_questions.copy(),
            },
        )

        # Check JSON file exists
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Verify JSON content - 一輪 = user_input → pm_response
        with open(history_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status"] == "AAF_NEED_CLARIFICATION"
        assert data["user_input"] == "Initial requirements\n"  # 輪的開始
        assert data["pm_response"] == "請問使用者是誰？"
        # user_response is no longer stored - next iteration's user_input IS the user_response
        assert "user_response" not in data
        assert "timestamp" in data
        assert "confirmed_requirements" in data
        assert "pending_questions" in data

    def test_save_iteration_history_includes_prompt(self, tmp_path: Path) -> None:
        """測試儲存迭代歷史時會包含 prompt 欄位"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Manually set iteration and save history with prompt using base class method
        phase.iteration = 1
        test_prompt = "這是發送給 PM agent 的完整 prompt 內容\n包含了需求和 context"
        phase._save_iteration_history(
            phase_specific_data={
                "status": PhaseStatusCode.NEED_CLARIFICATION.value,
                "user_input": "Initial requirements\n",
                "pm_response": "請問使用者是誰？",
                "confirmed_requirements": phase.confirmed_requirements.copy(),
                "pending_questions": phase.pending_questions.copy(),
            },
            prompt=test_prompt,
        )

        # Check JSON file exists
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Verify JSON content includes prompt
        with open(history_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["user_input"] == "Initial requirements\n"
        assert data["prompt"] == test_prompt
        assert data["pm_response"] == "請問使用者是誰？"
        assert data["status"] == "AAF_NEED_CLARIFICATION"

    def test_save_iteration_history_includes_agent_metadata(self, tmp_path: Path) -> None:
        """測試儲存迭代歷史時會包含 agent metadata（cli, session_id, allowed_tools, denied_tools）"""
        from aaf.core.types import AgentCLI, AgentConfig

        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Manually set iteration and save history with agent metadata using base class method
        phase.iteration = 1
        test_prompt = "這是發送給 PM agent 的完整 prompt 內容"
        phase._save_iteration_history(
            phase_specific_data={
                "status": PhaseStatusCode.NEED_CLARIFICATION.value,
                "user_input": "Initial requirements\n",
                "pm_response": "請問使用者是誰？",
                "confirmed_requirements": phase.confirmed_requirements.copy(),
                "pending_questions": phase.pending_questions.copy(),
            },
            prompt=test_prompt,
            agent_cli="copilot",
            agent_session_id="test-session-456",
            allowed_tools=["write", "read"],
            denied_tools=None,
        )

        # Check JSON file exists
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Verify JSON content includes agent metadata
        with open(history_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["user_input"] == "Initial requirements\n"
        assert data["prompt"] == test_prompt
        assert data["pm_response"] == "請問使用者是誰？"
        assert data["status"] == "AAF_NEED_CLARIFICATION"
        assert data["cli"] == "copilot"
        assert data["session_id"] == "test-session-456"
        assert data["allowed_tools"] == ["write", "read"]
        assert data["denied_tools"] is None

    def test_update_context_file_creates_markdown(self, tmp_path: Path) -> None:
        """測試更新 context.md 檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Set up conversation state
        phase.iteration = 2
        phase.confirmed_requirements = ["功能1: 用戶登入", "功能2: 用戶註冊"]
        phase.pending_questions = ["密碼規則是什麼？", "是否需要 email 驗證？"]

        # Create history file for iteration 1
        phase.history_dir.mkdir(parents=True, exist_ok=True)
        import json
        iteration_1_file = phase.history_dir / "iteration_001.json"
        iteration_1_file.write_text(json.dumps({
            "iteration": 1,
            "pm_response": "請問需要哪些功能？",
            "user_input": "需要登入和註冊",
            "response": "請問需要哪些功能？",
            "status_code": "AAF_NEED_CLARIFICATION",
        }, ensure_ascii=False, indent=2))

        # Update context file
        phase._update_context_file()

        # Check context file exists
        context_file = phase.history_dir / "context.md"
        assert context_file.exists()

        # Verify content
        content = context_file.read_text(encoding="utf-8")
        assert "# 需求澄清歷史" in content
        assert "## 已確定的需求" in content
        assert "功能1: 用戶登入" in content
        assert "功能2: 用戶註冊" in content
        assert "## 待解答的問題" in content
        assert "密碼規則是什麼？" in content
        assert "是否需要 email 驗證？" in content
        assert "## 對話歷史" in content
        assert "第 1 輪" in content
        assert "目前是第 2 輪" in content

    def test_context_file_shows_restriction_after_iteration_4(self, tmp_path: Path) -> None:
        """測試第 4 輪後 context.md 顯示問題限制"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Set iteration to 4
        phase.iteration = 4
        phase._update_context_file()

        # Check restriction message
        context_file = phase.history_dir / "context.md"
        content = context_file.read_text(encoding="utf-8")
        assert "只能針對現有問題繼續追問，不可提出新問題" in content

    def test_issue_name_derived_from_spec_file(self, tmp_path: Path) -> None:
        """測試 issue_name 從 spec_file 自動推導"""
        spec_file = tmp_path / ".aaf" / "issues" / "my-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Verify issue_name is derived from directory structure
        assert phase.issue_name == "my-feature"
        assert phase.history_dir == tmp_path / ".aaf" / "issues" / "my-feature" / "spec" / "history"

    def test_prompt_includes_context_file_after_iteration_1(self, tmp_path: Path) -> None:
        """測試第 2 輪後 prompt 包含 context 檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚", TokenUsage())

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Create history using base class method
        phase.iteration = 1
        phase._save_iteration_history(
            phase_specific_data={
                "status": PhaseStatusCode.NEED_CLARIFICATION.value,
                "user_input": "Q1",
                "pm_response": "A1",
                "confirmed_requirements": phase.confirmed_requirements.copy(),
                "pending_questions": phase.pending_questions.copy(),
            },
        )
        phase.iteration = 2

        # Generate prompt for iteration 2
        prompt = phase._generate_prompt()

        # Should include reference to context file
        expected_path = str(tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "history" / "context.md")
        assert "context.md" in prompt or expected_path in prompt

    def test_iteration_4_prompt_includes_restriction(self, tmp_path: Path) -> None:
        """測試第 4 輪 prompt 包含問題限制"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Set iteration to 4
        phase.iteration = 4

        # Generate prompt
        prompt = phase._generate_prompt()

        # Should include restriction message
        assert "待解答的問題" in prompt and "不可以提出新的問題" in prompt


class TestNonInteractiveModeIteration1:
    """Test non-interactive mode - first iteration (user story input)."""

    def test_first_call_with_user_story_returns_in_progress(self, tmp_path: Path) -> None:
        """第1次呼叫：提供 user story，PM 提問，回傳 IN_PROGRESS"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n需要澄清需求。\n\n## 待釐清的問題\n1. 問題一", TokenUsage())
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Mock stdin with user story
        user_story = "身為開發者，我想要有一個指令可以顯示 IP"
        with patch('sys.stdin', StringIO(user_story + "\nEND\n")):
            result = phase.execute()

        # Should return IN_PROGRESS after first iteration
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data["status_code"] == PhaseStatusCode.NEED_CLARIFICATION.value
        assert result.data["iterations"] == 1

        # Agent should be called once
        agent_manager.execute.assert_called_once()

    def test_first_call_creates_spec_file_from_stdin(self, tmp_path: Path) -> None:
        """第1次呼叫應該從 stdin 讀取 user story 並建立檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n需要澄清", TokenUsage())
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        user_story = "測試需求"
        with patch('sys.stdin', StringIO(user_story + "\nEND\n")):
            phase.execute()

        # Spec file should be created with user story
        assert spec_file.exists()
        content = spec_file.read_text()
        assert user_story in content

    def test_first_call_pm_confirms_immediately(self, tmp_path: Path) -> None:
        """第1次呼叫：PM 直接確認需求（不提問），回傳 COMPLETED"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚。", TokenUsage())
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        user_story = "簡單的需求"
        with patch('sys.stdin', StringIO(user_story + "\nEND\n")), \
             patch('builtins.print'):  # Suppress token usage output
            result = phase.execute()

        # Should complete immediately
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == PhaseStatusCode.CONFIRMED.value
        assert result.data["iterations"] == 1


class TestNonInteractiveModeErrorHandling:
    """Test error handling in non-interactive mode."""

    def test_no_stdin_input_on_first_call_fails(self, tmp_path: Path) -> None:
        """第1次呼叫：沒有 stdin 輸入應該失敗"""
        spec_file = tmp_path / "nonexistent.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Empty stdin
        with patch('sys.stdin', StringIO("\n")):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "No user story" in result.message

    def test_no_stdin_input_on_second_call_fails(self, tmp_path: Path) -> None:
        """第2+次呼叫：沒有用戶回答應該失敗"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial")

        # Setup history
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "AAF_NEED_CLARIFICATION\n問題",
            "user_response": "",
            "status_code": "AAF_NEED_CLARIFICATION"
        }))

        (issue_dir / "spec" / "status.json").write_text(json.dumps({
            "phase": "spec",
            "status": "in_progress",
            "status_code": "AAF_NEED_CLARIFICATION",
            "iteration": 1
        }))

        spec_file.write_text("Test")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_id="spec",
        )

        # Empty stdin on iteration 2 (non-interactive, so stdin not used)
        # Without user_input, should return IN_PROGRESS
        with patch('sys.stdin', StringIO("\n")):
            result = phase.execute()

        assert result.status == PhaseStatus.IN_PROGRESS
        assert "waiting for user clarification" in result.message


class TestInteractiveModeStillWorks:
    """Verify interactive mode still works as before."""

    def test_interactive_mode_single_iteration(self, tmp_path: Path) -> None:
        """互動模式：單次確認"""
        from aaf.core.types import SpecRigor

        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("需求已清楚")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求確認", TokenUsage())
        setup_agent_manager_mocks(agent_manager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,  # Interactive mode
            rigor=SpecRigor.MEDIUM,  # Explicitly set rigor to avoid prompting
        )

        # Mock display.get_multiline_input instead of builtins.input
        with patch('builtins.print'), patch.object(phase.display, 'get_multiline_input', return_value=''):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 1


class TestSkipConfirmedSpec:
    """Test skipping execution if spec is already confirmed."""

    def test_skip_execution_if_already_confirmed(self, tmp_path: Path) -> None:
        """測試如果已經 CONFIRMED 狀態就不再呼叫 agent"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        # Create status file showing CONFIRMED state
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        status_file = issue_dir / "spec" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        import json
        status_file.write_text(json.dumps({
            "phase": "spec",
            "status": "completed",
            "status_code": "AAF_CONFIRMED",
            "iteration": 3
        }))

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        # Should skip execution and return completed
        assert result.status == PhaseStatus.COMPLETED
        assert "already completed" in result.message or "already confirmed" in result.message
        assert result.data["iterations"] == 3

        # Agent should NOT be called
        agent_manager.execute.assert_not_called()


class TestKeyboardInterrupt:
    """Test Ctrl+C handling."""

    def test_keyboard_interrupt_does_not_save(self, tmp_path: Path) -> None:
        """測試 Ctrl+C 時不存檔"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        agent_manager = MagicMock(spec=AgentManager)
        # Simulate KeyboardInterrupt when agent executes
        agent_manager.execute.side_effect = KeyboardInterrupt()
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('sys.stdin', StringIO("Test input\nEND\n")), \
             patch('builtins.print'):
            result = phase.execute()

        # Should return IN_PROGRESS (paused, can resume)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "Paused by user" in result.message

        # No iteration history should be saved
        history_dir = tmp_path / ".aaf" / "issues" / "spec" / "spec" / "history"
        if history_dir.exists():
            iteration_files = list(history_dir.glob("iteration_*.json"))
            # Should have no new iteration files (only existing ones if any)
            assert len(iteration_files) == 0


class TestSpecPhasePromptGeneration:
    """測試 SpecPhase prompt 生成"""

    def test_prompt_does_not_include_status_code_without_prefix(self, tmp_path: Path) -> None:
        """測試 prompt 不應該包含沒有 AAF_ 前綴的 status code 指示"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Capture the prompt
        captured_prompt = None
        def capture_prompt(agent_name: str, prompt: str, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            # Return AAF_CONFIRMED to end the phase
            return ("AAF_CONFIRMED", TokenUsage())

        agent_manager.execute.side_effect = capture_prompt
        setup_agent_manager_mocks(agent_manager)

        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('sys.stdin', StringIO("Test requirement\nEND\n")), \
             patch('builtins.print'):
            phase.execute()

        # Prompt should NOT contain status codes without AAF_ prefix
        assert "只回傳：NEED_CLARIFICATION" not in captured_prompt
        assert "只回傳：CONFIRMED" not in captured_prompt
        assert "只回傳：REJECTED" not in captured_prompt

        # Prompt should reference AAF_ prefixed codes (either directly or via status_code_prompt)
        assert "AAF_CONFIRMED" in captured_prompt or "AAF_NEED_CLARIFICATION" in captured_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
