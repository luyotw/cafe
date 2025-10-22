"""Tests for SpecPhase."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch, call

from aaf.phases.spec_phase import SpecPhase
from aaf.agents.manager import AgentManager
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler
from aaf.core.status_codes import PhaseStatusCode


def create_mock_pm_agent(phase: SpecPhase, content: str, status_code: str = "CONFIRMED") -> Callable:
    """Create a mock PM agent that writes to history/spec.md before returning status code.

    Args:
        phase: SpecPhase instance to get history_dir from
        content: Content to write to spec.md
        status_code: Status code to return (CONFIRMED, NEED_CLARIFICATION, etc.)

    Returns:
        Mock function that can be used as agent_manager.execute side_effect
    """
    def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
        # Write spec content to history/spec.md
        spec_file = phase.history_dir / "spec.md"
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


class TestLocalWorkflow:
    """Test local workflow requirements clarification."""

    def test_execute_local_workflow_single_iteration(self, tmp_path: Path) -> None:
        """測試執行 local workflow 單次迭代"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_backup_original_requirements(self, tmp_path: Path) -> None:
        """測試備份原始需求檔案"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Original requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.execute()

        backup_file = Path(f"{spec_file}.backup")
        assert backup_file.exists()
        assert backup_file.read_text() == "Original requirements"

    def test_multiple_iterations_until_confirmed(self, tmp_path: Path) -> None:
        """測試多次迭代直到確認"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Initial requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # First two iterations ask questions, third confirms
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請問：需求問題 1",
            "NEED_CLARIFICATION\n請問：需求問題 2",
            "CONFIRMED\n需求已清楚。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 3


class TestGitHubWorkflow:
    """Test GitHub workflow requirements clarification."""

    def test_execute_github_workflow(self, tmp_path: Path) -> None:
        """測試執行 GitHub workflow"""
        agent_manager = MagicMock(spec=AgentManager)

        # Simulate PM writing to history/spec.md before returning CONFIRMED
        def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
            # Write spec content to history/spec.md
            # history_dir is based on spec_file path, not issue_id
            history_dir = tmp_path / ".aaf" / "issues" / "spec" / "spec" / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            spec_file = history_dir / "spec.md"
            spec_file.write_text("需求已清楚。")
            return "CONFIRMED"

        agent_manager.execute.side_effect = mock_execute

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(tmp_path / "spec.md"),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="123",
        )

        with patch('aaf.phases.spec_phase.update_github_issue') as mock_update:
            result = phase.execute()

            assert result.status == PhaseStatus.COMPLETED
            # Should update existing issue with content from history/spec.md
            mock_update.assert_called_once_with("123", "需求已清楚。")
            # Should use gh issue view in prompt
            call_args = agent_manager.execute.call_args
            prompt = call_args[0][1]
            assert "gh issue view 123" in prompt

    def test_github_workflow_uses_issue_id(self) -> None:
        """測試 GitHub workflow 使用 issue ID"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id="456",
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        assert "456" in call_args[1]


class TestPromptGeneration:
    """Test prompt generation for different iterations."""

    def test_first_iteration_prompt(self, tmp_path: Path) -> None:
        """測試第一次迭代的 prompt"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "spec.md" in prompt
        assert "第 1 輪" in prompt

    def test_subsequent_iteration_includes_history(self, tmp_path: Path) -> None:
        """測試後續迭代包含歷史記錄"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n問題 1",
            "CONFIRMED\n需求已清楚。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.execute()

        # Check second call includes iteration info
        second_call = agent_manager.execute.call_args_list[1][0]
        prompt = second_call[1]
        assert "第 2 輪" in prompt


class TestAgentSelection:
    """Test PM agent selection."""

    def test_uses_pm_agent(self, tmp_path: Path) -> None:
        """測試使用 PM agent (Roger)"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

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


class TestErrorHandling:
    """Test error handling."""

    def test_missing_spec_file_generates_from_conversation(self, tmp_path: Path) -> None:
        """測試缺少 spec 檔案時，透過對話生成"""
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

        # Mock agent to complete in one iteration
        agent_manager.execute.side_effect = create_mock_pm_agent(phase, "需求已完整", "CONFIRMED")

        result = phase.execute()

        # Should succeed and create the file
        assert result.status == PhaseStatus.COMPLETED
        assert spec_file.exists()

    def test_github_mode_without_issue_id_creates_new(self, tmp_path: Path) -> None:
        """測試 GitHub mode 沒有 issue_id 時創建新 issue"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(tmp_path / "spec.md"),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id=None,
        )

        agent_manager.execute.side_effect = create_mock_pm_agent(phase, "需求已完整", "CONFIRMED")

        with patch('aaf.phases.spec_phase.create_github_issue') as mock_create:
            mock_create.return_value = "789"

            result = phase.execute()

            # Should succeed and create new issue
            assert result.status == PhaseStatus.COMPLETED
            mock_create.assert_called_once()
            assert result.data.get("issue_id") == "789"

    def test_agent_execution_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 agent 執行錯誤時 phase 失敗"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message


class TestConversationalRequirementsGeneration:
    """Test conversational requirements generation workflow."""

    def test_generate_requirements_from_scratch_local(self, tmp_path: Path) -> None:
        """測試從無到有以對話方式生成需求文件（Local mode）"""
        spec_file = tmp_path / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Simulate conversation: ask questions -> user responds -> generate document
        call_count = [0]
        def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
            spec_file = phase.history_dir / "spec.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            call_count[0] += 1
            if call_count[0] < 3:
                spec_file.write_text(f"問題 {call_count[0]}")
                return "NEED_CLARIFICATION"
            else:
                spec_file.write_text("需求文件已生成")
                return "CONFIRMED"

        agent_manager.execute.side_effect = mock_execute

        result = phase.execute()

        # Should complete after conversation
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 3
        
        # Should create the requirements file
        assert spec_file.exists()

    def test_generate_requirements_saves_to_file(self, tmp_path: Path) -> None:
        """測試生成的需求文件正確儲存"""
        spec_file = tmp_path / "new_requirements.md"

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        agent_manager.execute.side_effect = create_mock_pm_agent(phase, "# 需求文件\n\n功能描述...", "CONFIRMED")

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # File should be created with content
        assert spec_file.exists()
        content = spec_file.read_text()
        assert "需求文件" in content or len(content) > 0

    def test_generate_requirements_github_creates_issue(self, tmp_path: Path) -> None:
        """測試 GitHub mode 創建新 issue"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(tmp_path / "spec.md"),
            workflow_mode=WorkflowMode.GITHUB,
            interactive=False,
            issue_id=None,  # No existing issue - should create new one
        )

        call_count = [0]
        def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
            spec_file = phase.history_dir / "spec.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            call_count[0] += 1
            if call_count[0] == 1:
                spec_file.write_text("請問功能目的？")
                return "NEED_CLARIFICATION"
            else:
                spec_file.write_text("需求已完整")
                return "CONFIRMED"

        agent_manager.execute.side_effect = mock_execute

        with patch('aaf.phases.spec_phase.create_github_issue') as mock_create:
            with patch('aaf.phases.spec_phase.update_github_issue') as mock_update:
                mock_create.return_value = "456"  # New issue ID

                result = phase.execute()

                assert result.status == PhaseStatus.COMPLETED
                # Should create issue on first iteration (NEED_CLARIFICATION)
                # Then update on second iteration (CONFIRMED)
                assert mock_create.call_count == 1  # Created once
                assert mock_update.call_count == 1  # Updated once
                assert result.data.get("issue_id") == "456"

    def test_prompt_includes_non_technical_emphasis(self, tmp_path: Path) -> None:
        """測試 prompt 包含不涉及技術細節的強調"""
        spec_file = tmp_path / "spec.md"

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n完成"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        phase.execute()

        # Check that prompt emphasizes non-technical approach
        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "不可涉及技術細節" in prompt or "不要提及實作方式" in prompt

    def test_no_existing_file_starts_conversation(self, tmp_path: Path) -> None:
        """測試沒有現有文件時，從對話開始"""
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

        call_count = [0]
        def mock_execute(agent_name: str, prompt: str, **kwargs) -> str:
            spec_file = phase.history_dir / "spec.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            call_count[0] += 1
            if call_count[0] == 1:
                spec_file.write_text("請描述功能需求")
                return "NEED_CLARIFICATION"
            else:
                spec_file.write_text("需求已完整")
                return "CONFIRMED"

        agent_manager.execute.side_effect = mock_execute
        
        result = phase.execute()

        # Should not fail when file doesn't exist
        assert result.status == PhaseStatus.COMPLETED
        # Should create the file
        assert spec_file.exists()


class TestHistoryTracking:
    """Test conversation history tracking for agents without session support."""

    def test_history_directory_structure(self, tmp_path: Path) -> None:
        """測試歷史記錄目錄結構包含 phase 資訊"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

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
        """測試儲存迭代歷史會建立 JSON 檔案"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "NEED_CLARIFICATION\n請問使用者是誰？"

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
            pm_response="請問使用者是誰？",
            user_response="一般用戶",
            status=PhaseStatusCode.NEED_CLARIFICATION,
        )

        # Check JSON file exists
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Verify JSON content
        with open(history_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status"] == "NEED_CLARIFICATION"
        assert data["pm_response"] == "請問使用者是誰？"
        assert data["user_response"] == "一般用戶"
        assert "timestamp" in data
        assert "confirmed_requirements" in data
        assert "pending_questions" in data

    def test_update_context_file_creates_markdown(self, tmp_path: Path) -> None:
        """測試更新 context.md 檔案"""
        spec_file = tmp_path / "spec.md"
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
                "status": "NEED_CLARIFICATION",
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
        spec_file = tmp_path / "spec.md"
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
        spec_file = tmp_path / "spec.md"
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
            {"iteration": 1, "pm_response": "Q1", "user_response": "A1", "status": "NEED_CLARIFICATION"},
            {"iteration": 2, "pm_response": "Q2", "user_response": "A2", "status": "NEED_CLARIFICATION"},
        ]

        # Save iteration 1
        phase1.iteration = 1
        phase1._save_iteration_history("Q1", "A1", PhaseStatusCode.NEED_CLARIFICATION)

        # Save iteration 2
        phase1.iteration = 2
        phase1.confirmed_requirements = ["功能1", "功能2"]
        phase1._save_iteration_history("Q2", "A2", PhaseStatusCode.NEED_CLARIFICATION)

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
        spec_file = tmp_path / "my-feature.md"
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

        # Verify issue_name is derived from filename
        assert phase.issue_name == "my-feature"
        assert phase.history_dir == tmp_path / ".aaf" / "issues" / "my-feature" / "spec" / "history"

    def test_prompt_includes_context_file_after_iteration_1(self, tmp_path: Path) -> None:
        """測試第 2 輪後 prompt 包含 context 檔案"""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚"

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
        spec_file = tmp_path / "spec.md"
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


class TestPhaseProgressTracking:
    """Test phase progress tracking with status.json."""

    def test_save_progress_on_confirmed(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 時保存進度到 status.json"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n\n## 需求規格\n完整需求"
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        req_file = tmp_path / "spec.md"
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(req_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Execute phase
        result = phase.execute()

        # Check status.json was created in spec/ directory (not history/)
        status_file = phase._get_status_file()
        assert status_file.exists()
        
        # Load and verify content
        with open(status_file) as f:
            status_data = json.load(f)
        
        assert status_data["phase"] == "spec"
        assert status_data["status"] == "completed"
        assert status_data["status_code"] == "CONFIRMED"
        assert "timestamp" in status_data

    def test_save_progress_on_need_clarification(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時也保存進度"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n\n問題1",
            "CONFIRMED\n\n完整需求",
        ]
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        req_file = tmp_path / "spec.md"
        req_file.write_text("初始需求")
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(req_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Mock user input
        with patch('builtins.input', return_value='END'):
            result = phase.execute()

        # Check status.json exists and has CONFIRMED status
        status_file = phase._get_status_file()
        assert status_file.exists()
        
        with open(status_file) as f:
            status_data = json.load(f)
        
        assert status_data["status"] == "completed"
        assert status_data["status_code"] == "CONFIRMED"

    def test_load_progress_from_status_json(self, tmp_path: Path) -> None:
        """測試可以從 status.json 讀取進度"""
        req_file = tmp_path / "spec.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(req_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Create a status.json manually
        status_file = phase._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)
        
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            status_code="CONFIRMED",
            timestamp=datetime.now(),
            iteration=2,
        )
        
        with open(status_file, 'w') as f:
            json.dump(progress.to_dict(), f)
        
        # Load progress
        loaded = phase._load_progress()
        
        assert loaded is not None
        assert loaded.phase == "spec"
        assert loaded.status == PhaseStatus.COMPLETED
        assert loaded.status_code == "CONFIRMED"
        assert loaded.iteration == 2

    def test_status_json_location(self, tmp_path: Path) -> None:
        """測試 status.json 在正確的位置：.aaf/issues/{issue_name}/spec/status.json"""
        req_file = tmp_path / "spec.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(req_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        expected_path = tmp_path / ".aaf" / "issues" / "spec" / "spec" / "status.json"
        assert phase._get_status_file() == expected_path
