"""Tests for PlanPhase."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase
from cafe.agents.manager import AgentManager
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage
from cafe.core.permission import PermissionHandler


def setup_agent_manager_mocks(agent_manager: MagicMock) -> None:
    """Setup standard mocks for agent_manager used by PlanPhase."""
    # Mock get_agent for _execute_agent_iteration (from Phase base class)
    mock_agent = MagicMock()
    mock_agent.config.cli.value = "copilot"
    mock_agent.config.session_id = "test_session"
    agent_manager.get_agent.return_value = mock_agent

    # Mock get_agent_config for other methods
    agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="copilot"))

    # Mock get_total_token_usage
    agent_manager.get_total_token_usage.return_value = TokenUsage()


class TestPlanPhaseBasics:
    """Test basic PlanPhase functionality."""

    def test_init_plan_phase(self) -> None:
        """測試初始化 PlanPhase"""
        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
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

        setup_agent_manager_mocks(agent_manager)
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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nSome requirements")

        # Create plan.md with dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nDevelopment guide here")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,  # Non-interactive for this test
            user_input="confirm",  # Provide user decision
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_missing_dev_guide_prompts_user_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試缺少開發指南時在互動模式下提示用戶輸入"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)
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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("分析中...", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Must be interactive to continue iterations
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="回應"), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2


class TestGitHubWorkflow:
    """Test GitHub workflow implementation analysis."""

    def test_execute_github_workflow(self, tmp_path: Path) -> None:
        """測試執行 GitHub workflow"""
        # Create requirements file in tmp_path
        issue_name = "test-github-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            interactive=False,  # Non-interactive for this test
            user_input="confirm",  # Provide user decision
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # Should use gh issue view in prompt
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]
        assert "gh issue view 123" in prompt

    def test_github_workflow_uses_issue_id(self, tmp_path: Path) -> None:
        """測試 GitHub workflow 使用 issue ID"""
        # Create requirements file in tmp_path
        issue_name = "test-github-issue-2"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("分析中", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]

        setup_agent_manager_mocks(agent_manager)

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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

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

        setup_agent_manager_mocks(agent_manager)
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

        setup_agent_manager_mocks(agent_manager)
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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        setup_agent_manager_mocks(agent_manager)

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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]

        setup_agent_manager_mocks(agent_manager)

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
        assert (history_dir / "iteration_001.json").exists()
        assert (history_dir / "iteration_002.json").exists()

        # Check first iteration history content
        import json
        with open(history_dir / "iteration_001.json", 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "需要更多資訊" in data["response"]

    def test_saves_progress_to_status_json(self, tmp_path: Path) -> None:
        """測試保存進度到 status.json"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage(), [], None)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,  # Non-interactive to skip user confirmation
            user_input="confirm",  # Provide user decision
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Should have created status.json
        status_file = spec_file.parent.parent / "plan" / "status.json"
        assert status_file.exists()

        import json
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["phase"] == "plan"
        assert data["status"] == "completed"
        assert data["status_code"] == "CAFE_CONFIRMED"  # Confirmed via user_input parameter

    def test_creates_plan_md_file(self, tmp_path: Path) -> None:
        """測試 agent 創建 plan.md 文件"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        # Mock agent to write plan.md file
        def mock_agent_writes_plan(agent_name: str, prompt: str, allowed_tools=None, denied_tools=None):
            # Agent writes plan.md
            plan_file = spec_file.parent.parent / "plan" / "plan.md"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# 實作計畫\n\n## 技術分析\n分析內容")
            return ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage())

        agent_manager = MagicMock(spec=AgentManager)
        # Only one call expected since non-interactive auto-confirms without calling agent again
        agent_manager.execute.side_effect = mock_agent_writes_plan

        setup_agent_manager_mocks(agent_manager)

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
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
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

    def test_save_history_creates_json_file(self, tmp_path: Path) -> None:
        """測試 _save_history() 創建 JSON 檔案，包含 user_input"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 1
        # Use base class method directly
        phase._save_iteration_history(
            phase_specific_data={
                "dev_agent": phase.dev_agent,
                "user_input": "User's dev guide input",
                "response": "Test response",
            },
            prompt="Test prompt",
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
        )

        # Check history file was created
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Check content - 一輪 = user_input → agent response
        import json
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["user_input"] == "User's dev guide input"  # 輪的開始
        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "timestamp" in data

    def test_load_history_reads_existing_files(self, tmp_path: Path) -> None:
        """測試 _load_history() 讀取現有歷史檔案"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
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
            "response": "Response 1 [STATUS:CAFE_NEED_CLARIFICATION]",
            "status_code": "CAFE_NEED_CLARIFICATION",
        }

        with open(history_dir / "iteration_001.json", 'w', encoding='utf-8') as f:
            json.dump(history1, f)

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        # _load_history() should be called in __init__ and update iteration counter
        assert phase.iteration == 1

    def test_save_history_includes_agent_metadata(self, tmp_path: Path) -> None:
        """測試 _save_history() 包含 agent metadata（cli, session_id, allowed_tools, denied_tools）"""
        from cafe.core.types import AgentCLI, AgentConfig

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 1
        # Use base class method directly
        phase._save_iteration_history(
            phase_specific_data={
                "dev_agent": phase.dev_agent,
                "user_input": "User's dev guide input",
                "response": "Test response",
            },
            prompt="Test prompt",
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
            agent_cli="claude",
            agent_session_id="session-789",
            allowed_tools=["read", "write"],
            denied_tools=["bash"],
        )

        # Check history file was created
        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        # Check content includes agent metadata
        import json
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["user_input"] == "User's dev guide input"
        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert data["cli"] == "claude"
        assert data["session_id"] == "session-789"
        assert data["allowed_tools"] == ["read", "write"]
        assert data["denied_tools"] == ["bash"]


class TestPlanPhaseNeedClarification:
    """Test NEED_CLARIFICATION handling (TDD)."""

    def test_need_clarification_prompts_user_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時在互動模式下提示使用者輸入"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成。", TokenUsage()),
        ]

        setup_agent_manager_mocks(agent_manager)

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
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

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

    def test_need_clarification_saves_iteration_history_with_user_input_and_response(self, tmp_path: Path) -> None:
        """測試每輪 history 包含 user_input（輪的開始），下一輪的 user_input 就是上一輪的 user_response"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\n初始開發指南內容")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n完成", TokenUsage()),
        ]

        setup_agent_manager_mocks(agent_manager)

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

        # Check first iteration history
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_file_1 = history_dir / "iteration_001.json"
        assert history_file_1.exists()

        import json
        with open(history_file_1, 'r', encoding='utf-8') as f:
            data1 = json.load(f)

        # 第一輪：user_input（開發指南）→ agent response（NEED_CLARIFICATION）
        assert data1["iteration"] == 1
        assert data1["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "user_input" in data1  # 輪的開始：開發指南
        assert "初始開發指南內容" in data1["user_input"]

        # Check second iteration history
        history_file_2 = history_dir / "iteration_002.json"
        assert history_file_2.exists()

        with open(history_file_2, 'r', encoding='utf-8') as f:
            data2 = json.load(f)

        # 第二輪：user_input（上輪的 user_response）→ agent response（CONFIRMED）
        assert data2["iteration"] == 2
        assert data2["status_code"] == "CAFE_READY_FOR_REVIEW"
        assert "user_input" in data2  # 輪的開始：上一輪的使用者回應
        assert data2["user_input"] == "我的回應內容"

    def test_need_clarification_saves_progress(self, tmp_path: Path) -> None:
        """測試 NEED_CLARIFICATION 時保存進度"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

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
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"


class TestPlanPhaseResume:
    """Test resuming from interrupted phase (TDD)."""

    def test_resume_shows_previous_plan_and_asks_user(self, tmp_path: Path) -> None:
        """測試中斷重跑時顯示上一輪的 plan.md 並詢問使用者"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
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
        history_file = history_dir / "iteration_001.json"
        history_data = {
            "iteration": 1,
            "prompt": "test prompt",
            "response": "CAFE_NEED_CLARIFICATION\n需要確認",
            "status_code": "CAFE_NEED_CLARIFICATION",
            "timestamp": "2024-01-01T00:00:00"
        }
        history_file.write_text(json.dumps(history_data, ensure_ascii=False, indent=2))

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作計畫已完成。", TokenUsage(), [], None)

        setup_agent_manager_mocks(agent_manager)

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

class TestPlanPhaseIterationDisplay:
    """Test plan display at iteration start."""

    def test_displays_plan_at_start_of_iteration_2(self, tmp_path: Path) -> None:
        """測試第二輪開始時顯示 plan.md 內容"""
        issue_name = "test-display-plan"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\n初始計畫")

        agent_manager = MagicMock(spec=AgentManager)
        # 第一輪：NEED_CLARIFICATION，第二輪：READY_FOR_REVIEW
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage()),
            ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage()),
        ]
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # 捕獲所有 print 輸出
        printed_output = []
        def capture_print(*args, **kwargs):
            printed_output.append(' '.join(str(arg) for arg in args))

        with patch('builtins.print', side_effect=capture_print), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # 檢查第二輪開始時有顯示「目前計畫內容」
        plan_display_headers = [line for line in printed_output if "目前計畫內容" in line]

        assert len(plan_display_headers) >= 1, "應該在第二輪開始時顯示計畫內容"
        assert any("Iteration 1" in line for line in plan_display_headers), "應該標註是 Iteration 1 的計畫"

    def test_no_plan_display_in_iteration_1(self, tmp_path: Path) -> None:
        """測試第一輪不應該顯示計畫內容（因為還沒產生）"""
        issue_name = "test-no-display-iter1"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # 第一輪就 READY_FOR_REVIEW
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage(), [], None)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        printed_output = []
        def capture_print(*args, **kwargs):
            printed_output.append(' '.join(str(arg) for arg in args))

        with patch('builtins.print', side_effect=capture_print), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # 第一輪開始時不應該有「目前計畫內容」
        plan_display_headers = [line for line in printed_output if "目前計畫內容" in line]

        assert len(plan_display_headers) == 0, "第一輪開始時不應該顯示計畫內容"


class TestPlanPhaseProgressTracking:
    """Test progress tracking functionality (TDD)."""

    def test_save_progress_creates_status_json(self, tmp_path: Path) -> None:
        """測試 _save_progress() 創建 status.json"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
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
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert data["iteration"] == 2

    def test_load_progress_returns_none_when_no_file(self, tmp_path: Path) -> None:
        """測試 _load_progress() 在沒有檔案時返回 None"""
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
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


class TestPlanPhaseNoStatusCode:
    """測試 agent 回傳內容但沒有 status code 的情況"""

    def test_agent_response_without_status_code_saves_to_history(self, tmp_path: Path) -> None:
        """測試 agent 回傳內容但沒有 status code 時，仍然要儲存 response 到 history

        這個測試模擬真實情況：agent 回傳了內容，但沒有包含正確的 status code
        （可能是格式錯誤或 agent 沒照指示做）。在舊版程式碼中，這會導致 response
        沒有被儲存，進而造成無限迴圈。
        """
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="copilot"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock agent to return content without status code, then interrupt
        agent_manager.execute.side_effect = [
            ("這是計畫內容，但沒有 status code", TokenUsage()),  # First call: response without status code
            KeyboardInterrupt()  # Second call: interrupt to exit
        ]

        with patch.object(phase.display, 'get_multiline_input', return_value="user feedback"), \
             patch('builtins.print'):
            with pytest.raises(KeyboardInterrupt):
                phase.execute()

        # Check that iteration 1 history was saved with response (even without status code)
        history_dir = spec_file.parent.parent / "plan" / "history"
        iteration_1 = history_dir / "iteration_001.json"
        assert iteration_1.exists()

        import json
        with open(iteration_1, 'r') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["response"] == "這是計畫內容，但沒有 status code"
        assert data["status_code"] is None


class TestPlanPhaseEmptyResponse:
    """測試 agent 回傳空字串的情況"""

    def test_agent_empty_response_should_fail_with_no_response_status(self, tmp_path: Path) -> None:
        """測試 agent 回傳空字串時應該失敗並標記為 NO_RESPONSE 狀態

        當 agent 回傳空字串（可能是執行失敗或輸出未正確捕捉），
        應該立即終止並返回 FAILED 狀態，並在 history 中記錄 CAFE_NO_RESPONSE。
        """
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="copilot"))

        # Mock agent to return empty string
        agent_manager.execute.return_value = ("", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Should fail with meaningful message
        assert result.status == PhaseStatus.FAILED
        assert "no response" in result.message.lower() or "empty" in result.message.lower()

        # Check history was saved with empty response and NO_RESPONSE status
        history_dir = spec_file.parent.parent / "plan" / "history"
        iteration_1 = history_dir / "iteration_001.json"
        assert iteration_1.exists()

        import json
        with open(iteration_1, 'r') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["response"] == ""
        assert data["status_code"] == "CAFE_NO_RESPONSE"


class TestPlanPhasePromptGeneration:
    """測試 PlanPhase 的 prompt 產生"""

    def test_prompt_includes_user_modification_request_in_iteration_2(self, tmp_path: Path) -> None:
        """測試 iteration 2 的 prompt 應該包含使用者的修改意見"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nPlan v1")

        # Create iteration 1 history with READY_FOR_REVIEW
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        
        import json
        iteration_1 = history_dir / "iteration_001.json"
        iteration_1.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2025-11-09T12:00:00",
            "user_input": "開發指南內容",
            "dev_agent": "David",
            "response": "CAFE_READY_FOR_REVIEW\n計畫已完成",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }, ensure_ascii=False))

        agent_manager = MagicMock(spec=AgentManager)

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="copilot"))
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Capture the prompt that was sent to agent
        captured_prompt = None
        def capture_prompt(agent_name, prompt, allowed_tools=None, denied_tools=None):
            nonlocal captured_prompt
            captured_prompt = prompt
            return ("CAFE_READY_FOR_REVIEW\n修改後的計畫", TokenUsage())

        agent_manager.execute.side_effect = capture_prompt

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock user choosing 'm' (modify) then 'c' (confirm)
        modification_request = "請加上錯誤處理和測試"
        with patch('builtins.input', side_effect=['m', 'c']) as mock_input, \
             patch.object(phase.display, 'get_multiline_input', return_value=modification_request), \
             patch('builtins.print'):
            phase.execute()

        # Check that the prompt includes user's modification request
        assert captured_prompt is not None
        assert modification_request in captured_prompt, \
            f"Prompt should include user's modification request.\nPrompt: {captured_prompt}"

    def test_prompt_does_not_include_contradicting_status_code_format(self, tmp_path: Path) -> None:
        """測試 prompt 不應該包含矛盾的 status code 格式指示"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Capture the prompt
        captured_prompt = None
        def capture_prompt(agent_name, prompt, allowed_tools=None, denied_tools=None):
            nonlocal captured_prompt
            captured_prompt = prompt
            return ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage())

        agent_manager.execute.side_effect = capture_prompt

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        with patch('builtins.print'):
            phase.execute()

        # Prompt should not contain "只回傳：READY_FOR_REVIEW" (without CAFE_ prefix)
        assert captured_prompt is not None
        assert "只回傳：READY_FOR_REVIEW" not in captured_prompt, \
            "Prompt should not instruct agent to return status code without CAFE_ prefix"
        assert "只回傳：NEED_CLARIFICATION" not in captured_prompt, \
            "Prompt should not instruct agent to return status code without CAFE_ prefix"

        # Should contain proper CAFE_ prefixed codes
        assert "CAFE_READY_FOR_REVIEW" in captured_prompt
        assert "CAFE_NEED_CLARIFICATION" in captured_prompt


class TestPlanPhaseUserConfirmation:
    """測試用戶確認計畫後的行為"""

    def test_user_confirmation_saves_history_and_updates_status(self, tmp_path: Path) -> None:
        """測試用戶確認計畫後應該保存 iteration history 並更新 status_code 為 CONFIRMED"""
        issue_name = "test"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nPlan")

        agent_manager = MagicMock(spec=AgentManager)
        # Agent returns READY_FOR_REVIEW
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage(), [], None)

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

        # User confirms with 'c'
        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # Should complete successfully
        assert result.status == PhaseStatus.COMPLETED

        # Should have 2 iterations: 1 for agent response, 1 for user confirmation
        history_dir = spec_file.parent.parent / "plan" / "history"
        assert history_dir.exists()

        iteration_files = sorted(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 2, f"應該有 2 個 iteration 文件，但只有 {len(iteration_files)} 個"

        # Check iteration 1: agent returns READY_FOR_REVIEW
        with open(iteration_files[0]) as f:
            iter1 = json.load(f)
        assert iter1["iteration"] == 1
        assert iter1["status_code"] == "CAFE_READY_FOR_REVIEW"

        # Check iteration 2: user confirms
        with open(iteration_files[1]) as f:
            iter2 = json.load(f)
        assert iter2["iteration"] == 2
        assert iter2["user_input"] == "confirm"  # or similar indication of user confirmation
        assert iter2["status_code"] == "CAFE_CONFIRMED"

        # Check status.json has final CONFIRMED status
        status_file = spec_file.parent.parent / "plan" / "status.json"
        assert status_file.exists()
        with open(status_file) as f:
            status_data = json.load(f)
        assert status_data["status_code"] == "CAFE_CONFIRMED"
        assert status_data["iteration"] == 2


class TestExecuteAndHandleAgentResponse:
    """測試 PlanPhase._execute_and_handle_agent_response() 方法（透過 base class）"""

    def test_returns_none_for_ready_for_review(self, tmp_path: Path) -> None:
        """測試當 agent 返回 READY_FOR_REVIEW 時返回 None（繼續循環）"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is None  # Should continue to next iteration

    def test_returns_none_for_need_clarification(self, tmp_path: Path) -> None:
        """測試當 agent 返回 NEED_CLARIFICATION 時返回 None（繼續循環）"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is None  # Should continue to next iteration

    def test_returns_failed_for_rejected(self, tmp_path: Path) -> None:
        """測試當 agent 返回 REJECTED 時返回 FAILED 狀態"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_REJECTED\n需求不明確", TokenUsage(), [], None)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is not None
        assert result.status == PhaseStatus.FAILED
        assert "rejected" in result.message.lower()

    def test_returns_failed_for_no_response(self, tmp_path: Path) -> None:
        """測試當 agent 返回空回應時返回 FAILED 狀態"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("", TokenUsage(), [], None)  # Empty response

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is not None
        assert result.status == PhaseStatus.FAILED
        assert "no response" in result.message.lower()

    def test_returns_none_for_no_status_code_interactive(self, tmp_path: Path) -> None:
        """測試沒有 status code 且 interactive 模式時返回 None（繼續循環）"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("Some response without status code", TokenUsage(), [], None)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is None  # Should continue in interactive mode

    def test_returns_in_progress_for_no_status_code_non_interactive(
        self, tmp_path: Path
    ) -> None:
        """測試沒有 status code 且 non-interactive 模式時返回 IN_PROGRESS 狀態"""
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("Some response without status code", TokenUsage(), [], None)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,  # Non-interactive mode
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        result, _ = phase._execute_and_handle_agent_response(
            agent_name=phase.dev_agent,
            user_input="請建立計畫",
            valid_status_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            allowed_tools=["write", "read"],
            complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
            continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
            phase_specific_data={"dev_agent": phase.dev_agent},
        )

        # Verify
        assert result is not None
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "No status code found" in result.message
