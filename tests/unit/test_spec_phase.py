"""Tests for SpecPhase."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch, call
from io import StringIO

from cafe.phases.spec_phase import SpecPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode, TokenUsage
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def create_mock_pm_agent(phase: SpecPhase, content: str, status_code: str = "CAFE_CONFIRMED") -> Callable:
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

    def test_init_spec_phase(self, mock_git_ops: MagicMock) -> None:
        """測試初始化 SpecPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.spec_file == "spec.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self, mock_git_ops: MagicMock) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestAgentSelection:
    """Test PM agent selection."""

    def test_uses_pm_agent(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """測試使用 PM agent (Roger)"""
        mock_git_ops.get_current_branch.return_value = "test-feature"

        # Change working directory to tmp_path so relative paths work
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n需求已清楚。", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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

    def test_history_directory_structure(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試歷史記錄目錄結構包含 phase 資訊"""
        mock_git_ops.get_current_branch.return_value = "test-feature"

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n需求已清楚。", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Verify history directory path (now relative to project root)
        assert str(phase.history_dir) == ".cafe/issues/test-feature/spec/history"

    def test_save_iteration_history_creates_json(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試儲存迭代歷史會建立 JSON 檔案，包含 user_input（輪的開始）"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請問使用者是誰？", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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
        assert data["status"] == "CAFE_NEED_CLARIFICATION"
        assert data["user_input"] == "Initial requirements\n"  # 輪的開始
        assert data["pm_response"] == "請問使用者是誰？"
        # user_response is no longer stored - next iteration's user_input IS the user_response
        assert "user_response" not in data
        assert "timestamp" in data
        assert "confirmed_requirements" in data
        assert "pending_questions" in data

    def test_save_iteration_history_includes_prompt(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試儲存迭代歷史時會包含 prompt 欄位"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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
        assert data["status"] == "CAFE_NEED_CLARIFICATION"

    def test_save_iteration_history_includes_agent_metadata(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試儲存迭代歷史時會包含 agent metadata（cli, session_id, allowed_tools, denied_tools）"""
        from cafe.core.types import AgentCLI, AgentConfig

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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
        assert data["status"] == "CAFE_NEED_CLARIFICATION"
        assert data["cli"] == "copilot"
        assert data["session_id"] == "test-session-456"
        assert data["allowed_tools"] == ["write", "read"]
        assert data["denied_tools"] is None

    def test_issue_name_derived_from_spec_file(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試 issue_name 從當前 branch 自動推導"""
        mock_git_ops.get_current_branch.return_value = "my-feature"

        spec_file = tmp_path / ".cafe" / "issues" / "my-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Verify issue_name is derived from current branch
        assert phase.issue_name == "my-feature"
        assert str(phase.history_dir) == ".cafe/issues/my-feature/spec/history"

    def test_iteration_2_prompt_includes_user_input(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試第 2 輪後 prompt 應該包含 user_input 而不是 context.md"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        phase.iteration = 2
        user_response = "1. 議題是人工定義的\n2. 同一組織共享議題列表\n3. 在新頁面選擇議題"

        # Generate prompt with user input
        prompt = phase._generate_prompt(user_input=user_response)

        # Should include user's response directly in prompt
        assert "議題是人工定義的" in prompt
        assert "同一組織共享議題列表" in prompt
        assert "在新頁面選擇議題" in prompt

        # Should have a section header for user response
        assert "使用者的回答" in prompt or "使用者回覆" in prompt

    def test_iteration_4_prompt_includes_restriction(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試第 4 輪 prompt 包含問題限制"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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

    def test_first_call_with_user_story_returns_in_progress(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """第1次呼叫：提供 user story，PM 提問，回傳 IN_PROGRESS（沒有 while loop，只執行一次）"""
        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        # Agent returns NEED_CLARIFICATION
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要澄清需求。\n\n## 待釐清的問題\n1. 問題一", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        # Provide user story via user_input for non-interactive mode
        user_story = "身為開發者，我想要有一個指令可以顯示 IP"

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_story,  # Use user_input parameter in non-interactive mode
        )

        result = phase.execute()

        # Should return IN_PROGRESS since agent needs clarification (no while loop anymore)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert result.data["iterations"] == 1

        # Agent should be called once
        assert agent_manager.execute.call_count == 1

    def test_first_call_creates_spec_file_from_stdin(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """第1次呼叫應該從 stdin 讀取 user story 並建立檔案"""
        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要澄清", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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

    def test_first_call_pm_confirms_immediately(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """第1次呼叫：PM 直接 READY_FOR_REVIEW（不提問），回傳 COMPLETED"""
        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已清楚。", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation for non-interactive mode
        )

        user_story = "簡單的需求"
        with patch('sys.stdin', StringIO(user_story + "\nEND\n")), \
             patch('builtins.print'):  # Suppress token usage output
            result = phase.execute()

        # Should complete immediately (READY_FOR_REVIEW + confirm = COMPLETED)
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == PhaseStatusCode.READY_FOR_REVIEW.value
        assert result.data["iterations"] == 1


class TestNonInteractiveModeErrorHandling:
    """Test error handling in non-interactive mode."""

    def test_no_stdin_input_on_first_call_fails(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """第1次呼叫：沒有 stdin 輸入應該失敗"""
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / "nonexistent.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Empty stdin
        with patch('sys.stdin', StringIO("\n")):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "No user story" in result.message

    def test_no_stdin_input_on_second_call_fails(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """第2+次呼叫：沒有用戶回答應該失敗"""
        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial")

        # Setup history - previous iteration had NEED_CLARIFICATION
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        import json
        # Previous iteration (complete with response)
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "user_input": "Initial user story",
            "response": "CAFE_NEED_CLARIFICATION\n問題",
            "status_code": "CAFE_NEED_CLARIFICATION"
        }))

        (issue_dir / "spec" / "status.json").write_text(json.dumps({
            "phase": "spec",
            "status": "in_progress",
            "status_code": "CAFE_NEED_CLARIFICATION",
            "iteration": 1
        }))

        spec_file.write_text("Test")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_id="spec",
        )

        # Empty stdin on iteration 2 (non-interactive, so stdin not used)
        # Without user_input in non-interactive mode, should FAIL
        with patch('sys.stdin', StringIO("\n")):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "non-interactive mode without user input" in result.message


class TestInteractiveModeStillWorks:
    """Verify interactive mode still works as before."""

    def test_interactive_mode_single_iteration(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """互動模式：單次迭代（沒有 while loop，回傳 IN_PROGRESS 等待確認）"""
        from cafe.core.types import SpecRigor

        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("需求已清楚")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求確認", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,  # Interactive mode
            rigor=SpecRigor.MEDIUM,  # Explicitly set rigor to avoid prompting
        )

        # Mock user choosing 'c' (confirm)
        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'), \
             patch.object(phase.display, 'get_multiline_input', return_value=''):
            result = phase.execute()

        # In interactive mode, READY_FOR_REVIEW returns IN_PROGRESS (waiting for user confirmation)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data["iterations"] == 1


class TestSkipConfirmedSpec:
    """Test skipping execution if spec is already confirmed."""

    def test_skip_execution_if_already_confirmed(self, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試如果已經 CONFIRMED 狀態就不再呼叫 agent"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        # Create status file showing CONFIRMED state
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        status_file = issue_dir / "spec" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        import json
        status_file.write_text(json.dumps({
            "phase": "spec",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 3
        }))

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
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

    def test_keyboard_interrupt_does_not_save(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """測試 Ctrl+C 時不存檔"""
        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
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
            git_ops=mock_git_ops,
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
        history_dir = tmp_path / ".cafe" / "issues" / "spec" / "spec" / "history"
        if history_dir.exists():
            iteration_files = list(history_dir.glob("iteration_*.json"))
            # Should have no new iteration files (only existing ones if any)
            assert len(iteration_files) == 0


class TestSpecPhasePromptGeneration:
    """測試 SpecPhase prompt 生成"""

    def test_prompt_does_not_include_status_code_without_prefix(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """測試 prompt 不應該包含沒有 CAFE_ 前綴的 status code 指示"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Capture the prompt
        captured_prompt = None
        def capture_prompt(agent_name: str, prompt: str, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            # Return CAFE_CONFIRMED to end the phase
            return ("CAFE_CONFIRMED", TokenUsage())

        agent_manager.execute.side_effect = capture_prompt
        setup_agent_manager_mocks(agent_manager)

        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('sys.stdin', StringIO("Test requirement\nEND\n")), \
             patch('builtins.print'):
            phase.execute()

        # Prompt should NOT contain status codes without CAFE_ prefix
        assert "只回傳：NEED_CLARIFICATION" not in captured_prompt
        assert "只回傳：CONFIRMED" not in captured_prompt
        assert "只回傳：REJECTED" not in captured_prompt

        # Prompt should reference CAFE_ prefixed codes (either directly or via status_code_prompt)
        assert "CAFE_CONFIRMED" in captured_prompt or "CAFE_NEED_CLARIFICATION" in captured_prompt


class TestInterruptedIterationResume:
    """測試中斷後恢復的完整流程."""

    def test_resume_interrupted_iteration_uses_saved_user_input(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """測試中斷後恢復時，直接使用已儲存的 user_input，不再詢問用戶."""
        import json
        from io import StringIO

        mock_git_ops.get_current_branch.return_value = "test-feature"
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        history_dir = spec_file.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        # 創建 iteration 1 的完整歷史（已完成，有 response）
        iteration1 = history_dir / "iteration_001.json"
        iteration1.write_text(json.dumps({
            "iteration": 1,
            "user_input": "Initial user story",
            "response": "CAFE_NEED_CLARIFICATION\n需要更多資訊",
            "status_code": "CAFE_NEED_CLARIFICATION"
        }))

        # 創建 iteration 2 的不完整歷史（被中斷，有 user_input 但沒有 response）
        iteration2 = history_dir / "iteration_002.json"
        saved_user_input = "1. 功能A\n2. 功能B\n3. 功能C"
        iteration2.write_text(json.dumps({
            "iteration": 2,
            "user_input": saved_user_input,
            "response": None  # 被中斷，沒有 response
        }))

        # Setup mocks
        agent_manager = MagicMock(spec=AgentManager)
        # 這次應該得到 READY_FOR_REVIEW
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已確認", TokenUsage(), [], None)
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation for non-interactive mode
        )

        # Execute phase - 應該直接使用已儲存的 user_input，不會要求用戶輸入
        with patch('sys.stdin', StringIO("")):  # 不提供任何輸入
            result = phase.execute()

        # 驗證結果
        assert result.status == PhaseStatus.COMPLETED

        # 驗證 agent 被呼叫時使用了已儲存的 user_input
        agent_manager.execute.assert_called_once()
        call_args = agent_manager.execute.call_args
        # execute 的參數是 (agent_name, prompt, ...)
        prompt = call_args[0][1]  # 第二個位置參數是 prompt

        # Prompt 應該包含已儲存的 user_input
        assert saved_user_input in prompt

        # 驗證 iteration 2 的 history 被更新（有 response 了）
        iteration2_data = json.loads(iteration2.read_text())
        assert iteration2_data["response"] is not None
        assert "CAFE_READY_FOR_REVIEW" in iteration2_data["response"]


class TestSpecPhaseFilePermissions:
    """測試 SpecPhase 的檔案權限設定"""

    def test_uses_precise_file_permissions_for_spec_file(self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch) -> None:
        """測試使用精細的檔案路徑授權 (write/edit spec.md)"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n完成", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation for non-interactive mode
        )

        # Execute
        result = phase.execute()

        # Verify allowed_tools includes precise file paths
        assert agent_manager.execute.called
        call_kwargs = agent_manager.execute.call_args
        allowed_tools = call_kwargs[1].get("allowed_tools")

        # Should have read, write(spec.md), edit(spec.md)
        assert "read" in allowed_tools
        # Check for write and edit with spec file path
        write_tools = [t for t in allowed_tools if t.startswith("write(")]
        edit_tools = [t for t in allowed_tools if t.startswith("edit(")]

        assert len(write_tools) >= 1, "Should have at least one write permission"
        assert len(edit_tools) >= 1, "Should have at least one edit permission"

        # Verify the paths point to spec.md
        spec_path_str = str(spec_file)
        assert any(spec_path_str in tool or "spec.md" in tool for tool in write_tools), \
            f"Write permission should include spec.md path, got: {write_tools}"
        assert any(spec_path_str in tool or "spec.md" in tool for tool in edit_tools), \
            f"Edit permission should include spec.md path, got: {edit_tools}"


class TestPromptForInputMethod:
    """測試 _prompt_for_input_method() 方法"""

    @patch("builtins.input")
    def test_prompt_for_input_method_manual(self, mock_input: MagicMock, tmp_path: Path, mock_git_ops: MagicMock) -> None:
        """測試選擇手動輸入（選項 1）"""
        # Setup
        mock_input.return_value = "1"
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        method, issue_id = phase._prompt_for_input_method()

        # Assert
        assert method == "manual"
        assert issue_id is None
        mock_input.assert_called_once()

    @patch("builtins.input")
    @patch("cafe.phases.spec_phase.GitHubOps")
    def test_prompt_for_input_method_github_with_number(
        self, mock_github_ops: MagicMock, mock_input: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試選擇 GitHub Issue + 輸入數字"""
        # Setup
        mock_input.side_effect = ["2", "123"]
        mock_gh_instance = MagicMock()
        mock_gh_instance.extract_issue_number.return_value = "123"
        mock_github_ops.return_value = mock_gh_instance

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        method, issue_id = phase._prompt_for_input_method()

        # Assert
        assert method == "github"
        assert issue_id == 123
        mock_gh_instance.extract_issue_number.assert_called_once_with("123")

    @patch("builtins.input")
    @patch("cafe.phases.spec_phase.GitHubOps")
    def test_prompt_for_input_method_github_with_url(
        self, mock_github_ops: MagicMock, mock_input: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試選擇 GitHub Issue + 輸入 URL"""
        # Setup
        github_url = "https://github.com/owner/repo/issues/456"
        mock_input.side_effect = ["2", github_url]
        mock_gh_instance = MagicMock()
        mock_gh_instance.extract_issue_number.return_value = "456"
        mock_github_ops.return_value = mock_gh_instance

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        method, issue_id = phase._prompt_for_input_method()

        # Assert
        assert method == "github"
        assert issue_id == 456
        mock_gh_instance.extract_issue_number.assert_called_once_with(github_url)

    @patch("builtins.input")
    def test_prompt_for_input_method_invalid_then_valid(
        self, mock_input: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試無效選擇後重試"""
        # Setup - first invalid (3), then valid (1)
        mock_input.side_effect = ["3", "1"]
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        method, issue_id = phase._prompt_for_input_method()

        # Assert
        assert method == "manual"
        assert issue_id is None
        assert mock_input.call_count == 2


class TestFetchGitHubIssue:
    """測試 _fetch_github_issue() 方法"""

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    def test_fetch_github_issue_writes_spec_file(
        self, mock_github_ops: MagicMock, mock_get_repo: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試從 GitHub Issue 抓取後會寫入 spec.md"""
        # Setup
        mock_get_repo.return_value = "owner/repo"
        mock_gh_instance = MagicMock()
        mock_gh_instance.get_issue.return_value = {
            "title": "Test Issue Title",
            "body": "Test issue body content"
        }
        mock_github_ops.return_value = mock_gh_instance

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        result = phase._fetch_github_issue(123)

        # Assert
        assert result is None  # Success
        assert spec_file.exists()
        content = spec_file.read_text()
        assert "# 初始需求" in content
        assert "Test Issue Title" in content
        assert "Test issue body content" in content

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    def test_fetch_github_issue_stores_issue_id(
        self, mock_github_ops: MagicMock, mock_get_repo: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試抓取後會儲存 issue_id 供之後貼回 comment"""
        # Setup
        mock_get_repo.return_value = "owner/repo"
        mock_gh_instance = MagicMock()
        mock_gh_instance.get_issue.return_value = {
            "title": "Test",
            "body": "Body"
        }
        mock_github_ops.return_value = mock_gh_instance

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        phase._fetch_github_issue(456)

        # Assert
        assert hasattr(phase, '_fetched_issue_id')
        assert phase._fetched_issue_id == "456"

    def test_save_issue_config_preserves_existing_fields(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試 _save_issue_config 會保留現有的 base_branch 和 feature_branch"""
        import yaml

        # Change to tmp_path to make .cafe paths work correctly
        monkeypatch.chdir(tmp_path)

        # Setup - 先建立有 base_branch 和 feature_branch 的 config.yaml
        issue_dir = Path(".cafe/issues/test-issue")
        issue_dir.mkdir(parents=True, exist_ok=True)
        config_file = issue_dir / "config.yaml"

        initial_config = {
            "base_branch": "main",
            "feature_branch": "test-issue",
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(initial_config, f, allow_unicode=True, default_flow_style=False)

        spec_file = issue_dir / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # 設定 _fetched_issue_id 以觸發儲存
        phase._fetched_issue_id = "123"

        # Execute
        phase._save_issue_config()

        # Assert - 驗證 config.yaml 包含所有欄位
        with open(config_file, 'r', encoding='utf-8') as f:
            saved_config = yaml.safe_load(f)

        assert saved_config["base_branch"] == "main"
        assert saved_config["feature_branch"] == "test-issue"
        assert saved_config["issue_id"] == "123"

    @patch("cafe.phases.spec_phase.get_github_repo_name")
    @patch("cafe.phases.spec_phase.GitHubOps")
    def test_fetch_github_issue_fails_when_not_authenticated(
        self, mock_github_ops: MagicMock, mock_get_repo: MagicMock, tmp_path: Path, mock_git_ops: MagicMock
    ) -> None:
        """測試當 gh 未登入時，_fetch_github_issue() 正確回傳失敗狀態"""
        # Setup
        mock_get_repo.return_value = "owner/repo"
        mock_gh_instance = MagicMock()
        # check_gh_auth() returns False (not authenticated)
        mock_gh_instance.check_gh_auth.return_value = False
        mock_github_ops.return_value = mock_gh_instance

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Execute
        result = phase._fetch_github_issue(123)

        # Assert
        assert result is not None
        assert result.status == PhaseStatus.FAILED
        assert "gh auth login" in result.message
        # Should not call get_issue if not authenticated
        mock_gh_instance.get_issue.assert_not_called()


class TestOriginalRequirement:
    """測試原始需求描述的保存與使用"""

    def test_captures_original_requirement_before_first_iteration(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試在第一輪迭代前會捕獲原始需求描述"""
        monkeypatch.chdir(tmp_path)

        # Setup - 建立初始 spec.md
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "# 初始需求\n\n身為用戶，我想要匯出 CSV 檔案"
        spec_file.write_text(original_content, encoding="utf-8")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock agent response to return CONFIRMED immediately
        agent_manager.execute.return_value = ("CAFE_CONFIRMED", MagicMock())

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute - 這會進入 while loop 並在第一輪前捕獲原始需求
        phase.execute()

        # Assert - 驗證原始需求被保存
        assert phase.original_requirement == original_content

    def test_original_requirement_included_in_prompt(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試 prompt 中包含原始需求描述的指示"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "身為用戶，我想要匯出 CSV 檔案"
        spec_file.write_text(original_content, encoding="utf-8")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # 手動設定 original_requirement（模擬已捕獲）
        phase.original_requirement = original_content
        phase.iteration = 1

        # Execute - 生成 prompt
        prompt = phase._generate_local_prompt(user_input="")

        # Assert - 驗證 prompt 包含原始需求描述
        assert "原始需求描述" in prompt
        assert original_content in prompt
        assert "除非用戶明確要求修改，否則絕對不可更動此部分內容" in prompt

    def test_spec_format_includes_original_requirement_section(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試 spec.md 格式指示包含「原始需求描述」區塊"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("測試需求", encoding="utf-8")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.original_requirement = "測試需求"
        phase.iteration = 1

        # Execute - 生成 prompt
        prompt = phase._generate_local_prompt(user_input="")

        # Assert - 驗證格式指示包含「原始需求描述」
        assert "「## 原始需求描述」" in prompt
        assert "完整保留" in prompt or "不可修改" in prompt

    def test_empty_original_requirement_not_shown_in_prompt(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試空的原始需求不會顯示在 prompt 中"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # 設定為空字串（模擬空檔案）
        phase.original_requirement = ""
        phase.iteration = 1

        # Execute - 生成 prompt
        prompt = phase._generate_local_prompt(user_input="")

        # Assert - 驗證不包含原始需求描述區塊
        assert "⚠️ **重要：原始需求描述**" not in prompt
        assert "除非用戶明確要求修改，否則絕對不可更動此部分內容" not in prompt

    def test_none_original_requirement_not_shown_in_prompt(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試 None 的原始需求不會顯示在 prompt 中"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # original_requirement 保持為 None（預設值）
        assert phase.original_requirement is None
        phase.iteration = 1

        # Execute - 生成 prompt
        prompt = phase._generate_local_prompt(user_input="")

        # Assert - 驗證不包含原始需求描述區塊
        assert "⚠️ **重要：原始需求描述**" not in prompt

    def test_pm_prompt_includes_no_code_modification_instruction(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試 PM prompt 中包含「不會修改程式碼」的指示"""
        monkeypatch.chdir(tmp_path)

        # Setup
        spec_file = Path(".cafe/issues/test-issue/spec/spec.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("測試需求", encoding="utf-8")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.iteration = 1

        # Execute - 生成 prompt
        prompt = phase._generate_local_prompt(user_input="")

        # Assert - 驗證包含「不會修改程式碼」相關指示
        assert "不是軟體工程師" in prompt
        assert "絕對不會自行修改程式碼" in prompt
        assert "Product Manager" in prompt or "PM" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
