"""Tests for PlanPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.plan_phase import PlanPhase
from aaf.agents.manager import AgentManager
from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestPlanPhaseBasics:
    """Test basic PlanPhase functionality."""

    def test_init_plan_phase(self) -> None:
        """測試初始化 PlanPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.spec_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestLocalWorkflow:
    """Test local workflow implementation analysis."""

    def test_execute_local_workflow_with_dev_guide(self, tmp_path: Path) -> None:
        """測試執行 local workflow 有開發指南"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nSome requirements")

        # Create plan.md with dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nDevelopment guide here")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,  # Non-interactive for this test
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_missing_dev_guide_prompts_user_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試缺少開發指南時在互動模式下提示用戶輸入"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user input for dev guide and confirmation
        with patch.object(phase.display, 'get_multiline_input', return_value="這是開發指南內容"):
            with patch('builtins.input', return_value='c'):  # Confirm the plan
                result = phase.execute()

        # Should create plan.md with dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        assert plan_file.exists()
        content = plan_file.read_text()
        assert "## 開發指南" in content
        assert "這是開發指南內容" in content

        # Should proceed with execution
        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_missing_dev_guide_fails_in_non_interactive_mode(self, tmp_path: Path) -> None:
        """測試缺少開發指南時在非互動模式下失敗"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "開發指南" in result.message

    def test_multiple_iterations_until_confirmed(self, tmp_path: Path) -> None:
        """測試多次迭代直到確認"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "分析中...",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,  # Non-interactive for this test
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2


class TestGitHubWorkflow:
    """Test GitHub workflow implementation analysis."""

    def test_execute_github_workflow(self) -> None:
        """測試執行 GitHub workflow"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            interactive=False,  # Non-interactive for this test
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # Should use gh issue view in prompt
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]
        assert "gh issue view 123" in prompt

    def test_github_workflow_uses_issue_id(self) -> None:
        """測試 GitHub workflow 使用 issue ID"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
            interactive=False,  # Non-interactive for this test
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        assert "456" in call_args[1]


class TestPromptGeneration:
    """Test prompt generation for different iterations."""

    def test_first_iteration_prompt(self, tmp_path: Path) -> None:
        """測試第一次迭代的 prompt"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "spec.md" in prompt
        assert "第 1 輪" in prompt

    def test_subsequent_iteration_includes_history(self, tmp_path: Path) -> None:
        """測試後續迭代包含迭代資訊"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "分析中",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
        )

        phase.execute()

        # Check second call includes iteration info
        second_call = agent_manager.execute.call_args_list[1][0]
        prompt = second_call[1]
        assert "第 2 輪" in prompt


class TestAgentSelection:
    """Test developer agent selection."""

    def test_uses_dev_agent(self, tmp_path: Path) -> None:
        """測試使用 Dev agent (David)"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            dev_agent="David",
        )

        phase.execute()

        # Check that David was used
        call_args = agent_manager.execute.call_args[0]
        assert call_args[0] == "David"


class TestErrorHandling:
    """Test error handling."""

    def test_missing_requirements_file_fails(self) -> None:
        """測試缺少需求檔案時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="/nonexistent/requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_github_mode_without_issue_id_fails(self) -> None:
        """測試 GitHub mode 沒有 issue_id 時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_agent_execution_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 agent 執行錯誤時 phase 失敗"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message


class TestPlanPhaseHistory:
    """Test history recording and loading functionality (TDD)."""

    def test_saves_history_after_each_iteration(self, tmp_path: Path) -> None:
        """測試每次迭代後保存歷史記錄"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n需要更多資訊",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Use interactive mode to test full flow
        )

        # Mock user input to continue after NEED_CLARIFICATION
        with patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        # Should have created history files for both iterations
        history_dir = spec_file.parent.parent / "plan" / "history"
        assert history_dir.exists()
        assert (history_dir / "001.json").exists()
        assert (history_dir / "002.json").exists()

        # Check first iteration history content
        import json
        with open(history_dir / "001.json", 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert "需要更多資訊" in data["response"]

    def test_saves_progress_to_status_json(self, tmp_path: Path) -> None:
        """測試保存進度到 status.json"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
        )

        result = phase.execute()

        # Should have created status.json
        status_file = spec_file.parent.parent / "plan" / "status.json"
        assert status_file.exists()

        import json
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["phase"] == "plan"
        assert data["status"] == "completed"
        assert data["status_code"] == "CONFIRMED"

    def test_creates_plan_md_file(self, tmp_path: Path) -> None:
        """測試 agent 創建 plan.md 文件"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        # Mock agent to write plan.md file
        def mock_agent_writes_plan(agent_name: str, prompt: str, allowed_tools=None) -> str:
            # Agent writes plan.md
            plan_file = spec_file.parent.parent / "plan" / "plan.md"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# 實作計畫\n\n## 技術分析\n分析內容")
            return "CONFIRMED\n實作分析已完成。"

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = mock_agent_writes_plan

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
        )

        result = phase.execute()

        # Should have created plan.md
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        assert plan_file.exists()

        content = plan_file.read_text()
        assert "實作計畫" in content
        assert "技術分析" in content

    def test_init_creates_history_dir_and_attributes(self, tmp_path: Path) -> None:
        """測試 __init__ 創建 history_dir 和 conversation_history 屬性"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        # Should have history_dir attribute
        assert hasattr(phase, 'history_dir')
        assert phase.history_dir == spec_file.parent.parent / "plan" / "history"

        # Should have conversation_history attribute
        assert hasattr(phase, 'conversation_history')
        assert isinstance(phase.conversation_history, list)
        assert len(phase.conversation_history) == 0

    def test_save_history_creates_json_file(self, tmp_path: Path) -> None:
        """測試 _save_history() 創建 JSON 檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 1
        phase._save_history(
            prompt="Test prompt",
            response="Test response",
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
        )

        # Check history file was created
        history_file = phase.history_dir / "001.json"
        assert history_file.exists()

        # Check content
        import json
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert "timestamp" in data

    def test_load_history_reads_existing_files(self, tmp_path: Path) -> None:
        """測試 _load_history() 讀取現有歷史檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Create history files
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_dir.mkdir(parents=True)

        import json
        history1 = {
            "iteration": 1,
            "timestamp": "2025-10-31T10:00:00",
            "prompt": "Prompt 1",
            "response": "Response 1 [STATUS:NEED_CLARIFICATION]",
            "status_code": "NEED_CLARIFICATION",
        }

        with open(history_dir / "001.json", 'w', encoding='utf-8') as f:
            json.dump(history1, f)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        # _load_history() should be called in __init__
        assert len(phase.conversation_history) == 1
        assert phase.conversation_history[0]["iteration"] == 1
        assert phase.iteration == 1


class TestPlanPhaseNeedClarification:
    """Test NEED_CLARIFICATION handling (TDD)."""

    def test_need_clarification_prompts_user_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時在互動模式下提示使用者輸入"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n需要更多資訊",
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user input (provide actual content and confirmation)
        with patch.object(phase.display, 'get_multiline_input', return_value="這是我補充的開發指南資訊") as mock_input:
            with patch('builtins.input', return_value='c'):  # Confirm the plan
                result = phase.execute()

                # Debug: print result details
                print(f"\nResult status: {result.status}")
                print(f"Result message: {result.message}")
                if hasattr(result, 'data'):
                    print(f"Result data: {result.data}")

                # Should complete after user provides clarification
                assert result.status == PhaseStatus.COMPLETED
                # Should have called agent twice (once for NEED_CLARIFICATION, once after user response)
                assert agent_manager.execute.call_count == 2
                # Should have prompted user for input
                assert mock_input.call_count == 1

    def test_need_clarification_exits_in_non_interactive_mode(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時在非互動模式下退出並等待"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "NEED_CLARIFICATION\n需要更多資訊"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
        )

        result = phase.execute()

        # Should return IN_PROGRESS status
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "需要更多資訊" in result.message or "clarification" in result.message.lower()
        # Should have NEED_CLARIFICATION status code in result data
        assert result.data["status_code"] == PhaseStatusCode.NEED_CLARIFICATION.value

    def test_need_clarification_saves_iteration_history_with_user_response(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時儲存 iteration history（包含 user response）"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n需要更多資訊",
            "CONFIRMED\n完成",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user input and confirmation
        with patch.object(phase.display, 'get_multiline_input', return_value="我的回應內容"):
            with patch('builtins.input', return_value='c'):
                result = phase.execute()

        # Should have saved iteration history with user response
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_file = history_dir / "001.json"
        assert history_file.exists()

        import json
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert "user_response" in data
        assert data["user_response"] == "我的回應內容"

    def test_need_clarification_saves_progress(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時保存進度"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "NEED_CLARIFICATION\n需要更多資訊"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
        )

        result = phase.execute()

        # Should have saved progress to status.json
        status_file = spec_file.parent.parent / "plan" / "status.json"
        assert status_file.exists()

        import json
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["phase"] == "plan"
        assert data["status"] == "in_progress"
        assert data["status_code"] == "NEED_CLARIFICATION"


class TestPlanPhaseUserConfirmation:
    """Test user confirmation when agent returns CONFIRMED (TDD)."""

    def test_confirmed_prompts_user_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 時在互動模式下提示使用者確認"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作計畫已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user choosing 'c' (confirm)
        with patch('builtins.input', return_value='c'):
            result = phase.execute()

        # Should complete after user confirms
        assert result.status == PhaseStatus.COMPLETED
        assert "confirmed" in result.message.lower()

    def test_confirmed_user_rejects(self, tmp_path: Path) -> None:
        """測試使用者拒絕計畫"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作計畫已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user choosing 'r' (reject)
        with patch('builtins.input', return_value='r'):
            result = phase.execute()

        # Should fail after user rejects
        assert result.status == PhaseStatus.FAILED
        assert "rejected" in result.message.lower()

    def test_confirmed_user_requests_modification(self, tmp_path: Path) -> None:
        """測試使用者要求修改計畫"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "CONFIRMED\n實作計畫已完成。",
            "CONFIRMED\n實作計畫已修改完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user choosing 'm' (modify) first, then 'c' (confirm)
        with patch('builtins.input', side_effect=['m', 'c']):
            with patch.object(phase.display, 'get_multiline_input', return_value="請加上錯誤處理"):
                result = phase.execute()

        # Should complete after modification and confirmation
        assert result.status == PhaseStatus.COMPLETED
        # Should have called agent twice (first plan, then modified plan)
        assert agent_manager.execute.call_count == 2

    def test_confirmed_non_interactive_mode_completes_immediately(self, tmp_path: Path) -> None:
        """測試非互動模式下 CONFIRMED 直接完成"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作計畫已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,  # Non-interactive mode
        )

        result = phase.execute()

        # Should complete immediately without user interaction
        assert result.status == PhaseStatus.COMPLETED


class TestPlanPhaseResume:
    """Test resuming from interrupted phase (TDD)."""

    def test_resume_shows_previous_plan_and_asks_user(self, tmp_path: Path) -> None:
        """測試中斷重跑時顯示上一輪的 plan.md 並詢問使用者"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create plan.md with dev guide
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide\n\n## 待確認問題\n\n需要確認技術選型")

        # Create history to simulate interrupted phase
        history_dir = plan_file.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        import json
        history_file = history_dir / "001.json"
        history_data = {
            "iteration": 1,
            "prompt": "test prompt",
            "response": "NEED_CLARIFICATION\n需要確認",
            "status_code": "NEED_CLARIFICATION",
            "timestamp": "2024-01-01T00:00:00"
        }
        history_file.write_text(json.dumps(history_data, ensure_ascii=False, indent=2))

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作計畫已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user providing response and then confirming
        with patch.object(phase.display, 'get_multiline_input', return_value="我的回答") as mock_multiline:
            with patch('builtins.input', return_value='c') as mock_input:
                result = phase.execute()

        # Should have prompted user for response before calling agent
        assert mock_multiline.call_count == 1
        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED

    def test_resume_with_confirmed_shows_plan_and_asks_confirmation(self, tmp_path: Path) -> None:
        """測試中斷重跑時，如果上一輪是 CONFIRMED，應該顯示 plan 並讓使用者選擇 c/r/m"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create plan.md with completed plan
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide\n\n## 實作步驟\n\n1. 步驟一\n2. 步驟二")

        # Create history to simulate interrupted phase with CONFIRMED
        history_dir = plan_file.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        import json
        history_file = history_dir / "001.json"
        history_data = {
            "iteration": 1,
            "prompt": "test prompt",
            "response": "CONFIRMED\n實作計畫已完成",
            "status_code": "CONFIRMED",
            "timestamp": "2024-01-01T00:00:00"
        }
        history_file.write_text(json.dumps(history_data, ensure_ascii=False, indent=2))

        agent_manager = MagicMock(spec=AgentManager)
        # Agent shouldn't be called if user confirms on resume

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,
        )

        # Mock user choosing 'c' (confirm) - should NOT ask for multiline input
        with patch('builtins.input', return_value='c') as mock_input:
            result = phase.execute()

        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED
        # Should have prompted for confirmation choice
        assert mock_input.called
        # Should NOT have called agent again (user confirmed existing plan)
        assert agent_manager.execute.call_count == 0


class TestPlanPhaseProgressTracking:
    """Test progress tracking functionality (TDD)."""

    def test_save_progress_creates_status_json(self, tmp_path: Path) -> None:
        """測試 _save_progress() 創建 status.json"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 2
        phase._save_progress(PhaseStatusCode.NEED_CLARIFICATION)

        status_file = phase.history_dir.parent / "status.json"
        assert status_file.exists()

        import json
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["phase"] == "plan"
        assert data["status"] == "in_progress"
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert data["iteration"] == 2

    def test_load_progress_returns_none_when_no_file(self, tmp_path: Path) -> None:
        """測試 _load_progress() 在沒有檔案時返回 None"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        progress = phase._load_progress()
        assert progress is None
