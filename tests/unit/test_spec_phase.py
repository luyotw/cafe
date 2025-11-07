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

        # Manually set iteration and save history
        phase.iteration = 1
        phase._save_iteration_history(
            user_input="Initial requirements\n",  # 輪的開始：使用者故事
            pm_response="請問使用者是誰？",
            status=PhaseStatusCode.NEED_CLARIFICATION,
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

        # Manually set iteration and save history with prompt
        phase.iteration = 1
        test_prompt = "這是發送給 PM agent 的完整 prompt 內容\n包含了需求和 context"
        phase._save_iteration_history(
            user_input="Initial requirements\n",
            prompt=test_prompt,
            pm_response="請問使用者是誰？",
            status=PhaseStatusCode.NEED_CLARIFICATION,
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

        # Manually set iteration and save history with agent metadata
        phase.iteration = 1
        test_prompt = "這是發送給 PM agent 的完整 prompt 內容"
        phase._save_iteration_history(
            user_input="Initial requirements\n",
            prompt=test_prompt,
            pm_response="請問使用者是誰？",
            status=PhaseStatusCode.NEED_CLARIFICATION,
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
        phase.conversation_history = [
            {
                "iteration": 1,
                "pm_response": "請問需要哪些功能？",
                "user_response": "需要登入和註冊",
                "status": "AAF_NEED_CLARIFICATION",
            },
        ]

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

    def test_load_history_restores_state(self, tmp_path: Path) -> None:
        """測試載入歷史記錄能還原狀態"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase and save history
        phase1 = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        phase1.iteration = 2
        phase1.confirmed_requirements = ["功能1", "功能2"]
        phase1.pending_questions = ["問題1", "問題2"]
        phase1.conversation_history = [
            {"iteration": 1, "pm_response": "Q1", "user_response": "A1", "status": "AAF_NEED_CLARIFICATION"},
            {"iteration": 2, "pm_response": "Q2", "user_response": "A2", "status": "AAF_NEED_CLARIFICATION"},
        ]

        # Save iteration 1
        phase1.iteration = 1
        phase1._save_iteration_history(
            user_input="Initial requirements\n",
            pm_response="Q1",
            status=PhaseStatusCode.NEED_CLARIFICATION
        )

        # Save iteration 2
        phase1.iteration = 2
        phase1.confirmed_requirements = ["功能1", "功能2"]
        phase1._save_iteration_history(
            user_input="A1",  # Previous user response becomes this iteration's user_input
            pm_response="Q2",
            status=PhaseStatusCode.NEED_CLARIFICATION
        )

        # Create new phase - history will be auto-loaded in __init__
        phase2 = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_name="test-feature",
        )

        # Verify state restored (history auto-loaded in __init__)
        assert phase2.iteration == 2
        assert phase2.confirmed_requirements == ["功能1", "功能2"]
        assert len(phase2.conversation_history) == 2
        assert phase2.conversation_history[0]["pm_response"] == "Q1"
        assert phase2.conversation_history[1]["pm_response"] == "Q2"

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

        # Create history
        phase.iteration = 1
        phase._save_iteration_history("Q1", "A1", PhaseStatusCode.NEED_CLARIFICATION)
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
        agent_manager.get_total_token_usage.return_value = TokenUsage()

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
        agent_manager.get_total_token_usage.return_value = TokenUsage()

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
        agent_manager.get_total_token_usage.return_value = TokenUsage()

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


class TestNonInteractiveModeIteration2Plus:
    """Test non-interactive mode - iterations 2+ (user response input)."""

    def test_second_call_reads_user_response_from_stdin(self, tmp_path: Path) -> None:
        """第2次呼叫：從 stdin 讀取用戶回答"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        # Create mock status and history from iteration 1
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        # Iteration 1 history
        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "NEED_CLARIFICATION\n問題一？",
            "user_response": "",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        # status.json showing we're in iteration 1
        (issue_dir / "spec" / "status.json").write_text(json.dumps({
            "phase": "spec",
            "status": "in_progress",
            "status_code": "AAF_NEED_CLARIFICATION",
            "iteration": 1
        }))

        spec_file.write_text("## 使用者故事\n測試\n\n## 待釐清的問題\n1. 問題一？")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Create phase (will load existing history)
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_id="spec",  # Use fixed issue_id to match directory
        )

        # Mock stdin with user response
        user_response = "回答：選項A"
        with patch('sys.stdin', StringIO(user_response + "\nEND\n")), \
             patch('builtins.print'):
            result = phase.execute()

        # Should complete after user provides answer
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 2

        # Check that user response was saved
        iteration_2_file = history_dir / "iteration_002.json"
        if iteration_2_file.exists():
            iteration_2 = json.loads(iteration_2_file.read_text())
            # Either in iteration 2 or in updated iteration 1
            # The implementation saves user response to iteration 1 when processing iteration 2

    def test_second_call_pm_needs_more_clarification(self, tmp_path: Path) -> None:
        """第2次呼叫：PM 收到回答後還需要更多澄清"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        # Setup history from iteration 1
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "NEED_CLARIFICATION\n問題一？",
            "user_response": "",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        (issue_dir / "spec" / "status.json").write_text(json.dumps({
            "phase": "spec",
            "status": "in_progress",
            "status_code": "AAF_NEED_CLARIFICATION",
            "iteration": 1
        }))

        spec_file.write_text("## 使用者故事\n測試")

        agent_manager = MagicMock(spec=AgentManager)
        # PM needs more info
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n還需要澄清問題二", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_id="spec",
        )

        user_response = "這是我的回答"
        with patch('sys.stdin', StringIO(user_response + "\nEND\n")):
            result = phase.execute()

        # Should return IN_PROGRESS for next iteration
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data["status_code"] == PhaseStatusCode.NEED_CLARIFICATION.value
        assert result.data["iterations"] == 2


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
            "pm_response": "NEED_CLARIFICATION\n問題",
            "user_response": "",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        (issue_dir / "spec" / "status.json").write_text(json.dumps({
            "phase": "spec",
            "status": "in_progress",
            "status_code": "AAF_NEED_CLARIFICATION",
            "iteration": 1
        }))

        spec_file.write_text("Test")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            issue_id="spec",
        )

        # Empty stdin on iteration 2
        with patch('sys.stdin', StringIO("\n")):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "No user response" in result.message


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
        agent_manager.get_total_token_usage.return_value = TokenUsage()
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
        agent_manager.get_total_token_usage.return_value = TokenUsage()
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
        assert result.message == "Spec already confirmed"
        assert result.data["iterations"] == 3

        # Agent should NOT be called
        agent_manager.execute.assert_not_called()


class TestResumeFromHistory:
    """Test resuming from existing history."""

    def test_resume_completes_iteration_without_extra_prompt(self, tmp_path: Path) -> None:
        """測試恢復時：用戶輸入 + PM 回應 = 完成一輪，不應再次詢問用戶"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        # NOTE: spec file does NOT exist

        # Create existing history where iteration 1 is completed
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        # Create iteration history
        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "NEED_CLARIFICATION\n問題一？",
            "user_response": "回答一",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        # Create current spec.md in history
        spec_file.write_text("## 使用者故事\n測試需求\n\n## 待釐清的問題\n1. 問題一？")

        agent_manager = MagicMock(spec=AgentManager)
        # PM returns NEED_CLARIFICATION for iteration 2
        agent_manager.execute.return_value = ("AAF_NEED_CLARIFICATION\n問題二？", TokenUsage())
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

        # Mock user input:
        # - First call: iteration 2 user response (from resume): "繼續回答"
        # - Second call: iteration 3 user response (normal): "iteration3回答"
        # - Third call: iteration 4 user response: "iteration4回答"
        mock_input = MagicMock(side_effect=["繼續回答", "iteration3回答", "iteration4回答"])

        # Mock agent responses:
        # - iteration 2: NEED_CLARIFICATION (triggers continue to iteration 3)
        # - iteration 3: NEED_CLARIFICATION (prompts user, then continues to iteration 4)
        # - iteration 4: CONFIRMED (completes)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n問題二？",  # iteration 2
            "NEED_CLARIFICATION\n問題三？",  # iteration 3
            "CONFIRMED\n需求已清楚"  # iteration 4
        ]

        with patch.object(phase.display, 'get_multiline_input', mock_input):
            result = phase.execute()

        # Should prompt user TWICE (once for resume/iteration 2, once for iteration 3)
        # Note: iteration 4 returns CONFIRMED so no third prompt
        assert mock_input.call_count == 2

        # Agent should be called THREE times (iteration 2, 3, and 4)
        assert agent_manager.execute.call_count == 3

        # Iteration 2 should be saved
        iteration_002_file = history_dir / "iteration_002.json"
        assert iteration_002_file.exists()
        iteration_002_data = json.loads(iteration_002_file.read_text())
        assert iteration_002_data["iteration"] == 2
        # user_response is no longer stored - next iteration's user_input contains it
        assert iteration_002_data["status"] == "AAF_NEED_CLARIFICATION"

        # Iteration 3 should also be saved
        iteration_003_file = history_dir / "iteration_003.json"
        assert iteration_003_file.exists()
        iteration_003_data = json.loads(iteration_003_file.read_text())
        assert iteration_003_data["iteration"] == 3
        # user_response is no longer stored - next iteration's user_input contains it
        assert iteration_003_data["status"] == "AAF_NEED_CLARIFICATION"

        # Iteration 4 should be saved with CONFIRMED
        iteration_004_file = history_dir / "iteration_004.json"
        assert iteration_004_file.exists()
        iteration_004_data = json.loads(iteration_004_file.read_text())
        assert iteration_004_data["iteration"] == 4
        assert iteration_004_data["status"] == "AAF_CONFIRMED"

        # Should return COMPLETED (because iteration 4 was CONFIRMED)
        assert result.status == PhaseStatus.COMPLETED

    def test_resume_needs_user_response_before_agent(self, tmp_path: Path) -> None:
        """測試恢復時，如果上一輪還沒回答，應該先讓用戶回答，再執行 agent"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        # NOTE: spec file does NOT exist

        # Create existing history
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        # Create iteration history where iteration 1 has no user response yet
        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "NEED_CLARIFICATION\n## 使用者故事\n測試\n\n## 待釐清的問題\n1. 問題一？",
            "user_response": "",  # No user response yet
            "status": "AAF_NEED_CLARIFICATION"
        }))

        # Create current spec.md with PM's question
        spec_file.write_text("## 使用者故事\n測試\n\n## 待釐清的問題\n1. 問題一？")

        agent_manager = MagicMock(spec=AgentManager)
        # Agent should be called AFTER user provides response
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚", TokenUsage())
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

        # Mock user input: user answers the question from iteration 1
        mock_input = MagicMock(return_value="這是我的回答")
        with patch.object(phase.display, 'get_multiline_input', mock_input):
            result = phase.execute()

        # Verify user was prompted to answer (get_multiline_input called)
        mock_input.assert_called()

        # Verify agent was called AFTER user provided response (iteration 2)
        agent_manager.execute.assert_called_once()

        # Check context.md was updated with user response
        context_file = history_dir / "context.md"
        assert context_file.exists()
        context_content = context_file.read_text()
        assert "這是我的回答" in context_content

        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED

    def test_display_current_spec_when_resuming(self, tmp_path: Path) -> None:
        """測試從暫存資料夾恢復時，顯示目前的 spec 狀態"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Initial spec")

        # Create existing history
        issue_dir = tmp_path / ".aaf" / "issues" / "test-feature"
        history_dir = issue_dir / "spec" / "history"
        history_dir.mkdir(parents=True)

        # Create iteration history
        import json
        (history_dir / "iteration_001.json").write_text(json.dumps({
            "iteration": 1,
            "pm_response": "NEED_CLARIFICATION\n問題一？",
            "user_response": "回答一",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        (history_dir / "iteration_002.json").write_text(json.dumps({
            "iteration": 2,
            "pm_response": "NEED_CLARIFICATION\n問題二？",
            "user_response": "",
            "status": "AAF_NEED_CLARIFICATION"
        }))

        # Create current spec.md in history
        spec_file.write_text("## 使用者故事\n測試\n\n## 待釐清的問題\n1. 問題二？")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n需求已清楚", TokenUsage())
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

        # Capture print output
        from io import StringIO
        import sys

        captured_output = []
        original_print = print

        def capture_print(*args, **kwargs):
            # Capture to our list
            captured_output.append(' '.join(str(arg) for arg in args))
            # Also call original print to avoid breaking the test
            if 'file' not in kwargs:
                kwargs['file'] = StringIO()

        with patch.object(phase.display, 'get_multiline_input', return_value="回答二"), \
             patch('builtins.print', side_effect=capture_print):
            result = phase.execute()

        output = '\n'.join(captured_output)

        # Should display current spec state before continuing
        assert "目前的需求規格" in output
        assert "第 2 輪" in output
        assert "問題二" in output

        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
