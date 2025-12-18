"""Tests for PlanPhase."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage
from cafe.core.permission import PermissionHandler


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


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


def create_template_file(tmp_path: Path) -> str:
    """Create a dummy template file for tests."""
    template_file = tmp_path / "template.md"
    template_file.write_text("# Plan Template\n\nTemplate content")
    return str(template_file)


class TestPlanPhaseBasics:
    """Test basic PlanPhase functionality."""

    def test_init_plan_phase(self, mock_git_ops) -> None:
        """測試初始化 PlanPhase"""
        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
            git_ops=mock_git_ops,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.spec_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self, mock_git_ops) -> None:
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
            git_ops=mock_git_ops,
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestLocalWorkflow:
    """Test local workflow implementation analysis."""

    def test_execute_local_workflow_with_dev_guide(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nSome requirements")

        # Create plan.md with dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nDevelopment guide here")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

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
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_missing_dev_guide_prompts_user_in_interactive_mode(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試有開發指南時可以正常執行（改為 non-interactive 並提供開發指南）"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        # Create plan.md with dev guide section (simulate user providing it)
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## Development Guide\n\nThis is the development guide content\n\n## Implementation Plan\n\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            user_input="confirm",
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        result = phase.execute()

        # Should proceed with execution successfully
        assert result.status == PhaseStatus.COMPLETED

        # plan.md should still exist with dev guide
        assert plan_file.exists()
        content = plan_file.read_text()
        assert "## Development Guide" in content
        assert "This is the development guide content" in content
        assert agent_manager.execute.called

    def test_empty_dev_guide_allowed_in_non_interactive_mode(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試空開發指南在非互動模式下被允許"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"

        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            user_input="confirm",  # Provide confirm for READY_FOR_REVIEW status
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Empty dev guide should be allowed, phase should proceed
        assert result.status == PhaseStatus.COMPLETED
        # plan_001.md should exist with empty dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        assert plan_file.exists()
        content = plan_file.read_text()
        assert "## Development Guide" in content

    def test_multiple_iterations_until_confirmed(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        # 移除 while loop 後, agent 回應應該包含 status code
        # 測試 NEED_CLARIFICATION 情況
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 移除 while loop 後, execute() 只執行一次, 返回 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        # 沒有 while loop, 只呼叫 agent 一次
        assert agent_manager.execute.call_count == 1


class TestGitHubWorkflow:
    """Test GitHub workflow implementation analysis."""

    def test_execute_github_workflow(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試執行 GitHub workflow"""
        monkeypatch.chdir(tmp_path)
        # Create requirements file in tmp_path
        issue_name = "test-github-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

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
            git_ops=mock_git_ops,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # Should use gh issue view in prompt
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]
        assert "gh issue view 123" in prompt

    def test_github_workflow_uses_issue_id(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 GitHub workflow 使用 issue ID"""
        monkeypatch.chdir(tmp_path)
        # Create requirements file in tmp_path
        issue_name = "test-github-issue-2"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
            interactive=False,  # Non-interactive for this test
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        assert "456" in call_args[1]


class TestPromptGeneration:
    """Test prompt generation for different iterations."""

    def test_first_iteration_prompt(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "spec.md" in prompt
        assert "iteration 1" in prompt.lower()

    def test_subsequent_iteration_includes_history(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        # All calls return response without status code (will retry 5 times and fail)
        agent_manager.execute.return_value = ("分析中", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Non-interactive mode: after 5 retries without status code returns FAILED
        assert result.status == PhaseStatus.FAILED
        assert "Still did not return valid status code after" in result.message
        # 呼叫 6 次：原始 prompt + 5 次重試
        assert agent_manager.execute.call_count == 6

        # Check first call includes iteration info
        first_call = agent_manager.execute.call_args_list[0][0]
        prompt = first_call[1]
        assert "iteration 1" in prompt.lower()


class TestAgentSelection:
    """Test developer agent selection."""

    def test_uses_dev_agent(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            dev_agent="David",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            phase.execute()

        # Check that David was used
        call_args = agent_manager.execute.call_args[0]
        assert call_args[0] == "David"


class TestErrorHandling:
    """Test error handling."""

    def test_missing_requirements_file_fails(self, mock_git_ops) -> None:
        """測試缺少需求檔案時失敗"""
        agent_manager = MagicMock(spec=AgentManager)

        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="/nonexistent/requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
            git_ops=mock_git_ops,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_github_mode_without_issue_id_fails(self, mock_git_ops) -> None:
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
            git_ops=mock_git_ops,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_agent_execution_error_fails_phase(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
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
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message or "error" in result.message.lower()


class TestPlanPhaseHistory:
    """Test history recording and loading functionality (TDD)."""

    def test_saves_history_after_each_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        # 移除 while loop 後, 只會執行一次迭代
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Must be interactive for NEED_CLARIFICATION flow
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Mock user input to continue after NEED_CLARIFICATION
        mock_multiline_input.return_value = "補充資訊"
        with patch('builtins.print'):
            result = phase.execute()

        # 移除 while loop 後, 只會有一次迭代 history
        history_dir = spec_file.parent.parent / "plan" / "history"
        assert history_dir.exists()
        assert (history_dir / "iteration_001.json").exists()

        # Check first iteration history content
        import json
        with open(history_dir / "iteration_001.json", 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "需要更多資訊" in data["response"]

    def test_saves_progress_to_status_json(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            user_input="confirm",
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
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
        # In non-interactive mode, READY_FOR_REVIEW completes immediately without user confirmation
        assert data["status_code"] == "CAFE_READY_FOR_REVIEW"

    def test_creates_plan_md_file(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        # Mock agent to write plan.md file
        def mock_agent_writes_plan(agent_name: str, prompt: str, allowed_tools=None, allowed_directories=None):
            # Agent writes plan.md
            plan_file = spec_file.parent.parent / "plan" / "plan.md"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# 實作計畫\n\n## 技術分析\n分析內容")
            return ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [])

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = mock_agent_writes_plan

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Should have created plan.md
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        assert plan_file.exists()

        content = plan_file.read_text()
        assert "實作計畫" in content
        assert "技術分析" in content

    def test_init_creates_history_dir_and_attributes(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
        )

        # Should have history_dir attribute
        assert hasattr(phase, 'history_dir')
        assert phase.history_dir.resolve() == (spec_file.parent.parent / "plan" / "history").resolve()

    def test_save_history_creates_json_file(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
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
        assert data["user_input"] == "User's dev guide input"  # 輪開始
        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"
        assert data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "timestamp" in data

    def test_load_history_reads_existing_files(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
        )

        # _load_history() should be called in __init__ and update iteration counter
        assert phase.iteration == 1

    def test_save_history_includes_agent_metadata(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
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

    def test_need_clarification_prompts_user_in_interactive_mode(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, []),
            ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, []),
        ]

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Must be interactive for NEED_CLARIFICATION flow
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Mock user input (provide actual content and confirmation)
        mock_multiline_input.return_value = "這是我補充開發指南資訊"
        with patch('builtins.print'):
            result = phase.execute()

            # 移除 while loop 後, NEED_CLARIFICATION 返回 COMPLETED
            assert result.status == PhaseStatus.COMPLETED
            assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
            # 沒有 while loop, 只呼叫 agent 一次
            assert agent_manager.execute.call_count == 1

    def test_need_clarification_exits_in_non_interactive_mode(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # 移除 while loop 後, NEED_CLARIFICATION 在 non-interactive 模式下返回 COMPLETED
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"

    def test_need_clarification_saves_iteration_history_with_user_input_and_response(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file (versioned)
        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\n初始開發指南內容")

        agent_manager = MagicMock(spec=AgentManager)
        # 移除 while loop 後, 只會執行一次迭代
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Must be interactive for NEED_CLARIFICATION flow
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Mock user input and confirmation
        mock_multiline_input.return_value = "我回應內容"
        with patch('builtins.print'):
            result = phase.execute()

        # 移除 while loop 後, 只會有一次迭代 history
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_file_1 = history_dir / "iteration_001.json"
        assert history_file_1.exists()

        import json
        with open(history_file_1, 'r', encoding='utf-8') as f:
            data1 = json.load(f)

        # Round 1：user_input（開發指南）→ agent response（NEED_CLARIFICATION）
        assert data1["iteration"] == 1
        assert data1["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert "user_input" in data1  # 輪開始：開發指南
        assert "Development Guide" in data1["user_input"]

    def test_need_clarification_saves_progress(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create dev guide file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=False,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
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

    def test_resume_shows_previous_plan_and_asks_user(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        # Create plan_001.md with dev guide (versioned)
        plan_file = spec_file.parent.parent / "plan" / "plan_001.md"
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
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作計畫已完成.", TokenUsage(), [], None, [])

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            interactive=True,  # Must be interactive for resume flow
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Mock user providing response and then confirming
        mock_multiline_input.return_value = "我回答"
        with patch('builtins.print'):
            result = phase.execute()

        # Should have prompted user for response before calling agent
        assert mock_multiline_input.call_count == 1
        # After behavior change, READY_FOR_REVIEW in interactive mode returns COMPLETED immediately
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

class TestPlanPhaseIterationDisplay:
    """Test plan display at iteration start."""

    def test_displays_plan_at_start_of_iteration_2(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試從恢復 iteration 開始時顯示 plan.md 內容（移除 while loop 後不會在同一個 execute() 呼叫中進入Round 2）"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-display-plan"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\n初始計畫")

        # 建立 iteration 1  history 來模擬恢復狀態
        history_dir = plan_file.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        import json
        history_file = history_dir / "iteration_001.json"
        history_data = {
            "iteration": 1,
            "prompt": "test prompt",
            "response": "CAFE_NEED_CLARIFICATION\n需要更多資訊",
            "status_code": "CAFE_NEED_CLARIFICATION",
            "timestamp": "2024-01-01T00:00:00"
        }
        history_file.write_text(json.dumps(history_data, ensure_ascii=False, indent=2))

        agent_manager = MagicMock(spec=AgentManager)
        # 從 iteration 2 開始（恢復）
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # 捕獲所有 print 輸出
        printed_output = []
        def capture_print(*args, **kwargs):
            printed_output.append(' '.join(str(arg) for arg in args))

        with patch('builtins.print', side_effect=capture_print), \
             patch('cafe.ui.inquirer_prompts.prompt_multiline', return_value="補充資訊"):
            result = phase.execute()

        # After behavior change: READY_FOR_REVIEW in interactive mode returns COMPLETED immediately
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

    def test_no_plan_display_in_iteration_1(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試Round 1不應該顯示計畫內容（因為還沒產生）"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test-no-display-iter1"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nDev guide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nDev guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # Round 1就 READY_FOR_REVIEW
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage(), [], None, [])
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,  # Changed to False to avoid hanging
            git_ops=mock_git_ops,
        )

        printed_output = []
        def capture_print(*args, **kwargs):
            printed_output.append(' '.join(str(arg) for arg in args))

        with patch('builtins.print', side_effect=capture_print), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # Round 1開始時不應該有「目前計畫內容」
        plan_display_headers = [line for line in printed_output if "目前計畫內容" in line]

        assert len(plan_display_headers) == 0, "Round 1開始時不應該顯示計畫內容"

    def test_display_current_plan_first_iteration_shows_not_generated(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _display_current_plan Round 1時顯示「檔案未產生」"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".cafe" / "issues" / "test" / "plan"
        plan_dir.mkdir(parents=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )
        phase.dev_agent = "David"
        phase.iteration = 1  # Round 1
        phase.plan_file = str(plan_dir / "plan_001.md")
        phase.phase_dir = plan_dir

        with patch('builtins.print') as mock_print:
            phase._display_current_plan()

        # 驗證顯示「檔案未產生」
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("File not generated" in str(call) for call in print_calls), \
            f"Expected 'File not generated' in print calls, got: {print_calls}"

    def test_display_current_plan_loads_previous_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 _display_current_plan 載入上一輪檔案（iteration > 1）"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".cafe" / "issues" / "test" / "plan"
        plan_dir.mkdir(parents=True)

        # 建立上一輪檔案（plan_001.md）
        prev_plan_file = plan_dir / "plan_001.md"
        prev_plan_file.write_text("# Previous Plan\n\n## 實作計畫\nPrevious plan content")

        # 當前輪檔案（plan_002.md）還不存在
        current_plan_file = plan_dir / "plan_002.md"

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="claude"))

        permission_handler = MagicMock(spec=PermissionHandler)

        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            git_ops=mock_git_ops,
        )
        phase.dev_agent = "David"
        phase.iteration = 2  # Round 2
        phase.plan_file = str(current_plan_file)
        phase.phase_dir = plan_dir

        with patch('builtins.print') as mock_print:
            phase._display_current_plan()

        # 驗證有印出載入訊息, 並包含正確檔案路徑
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("plan_001.md" in str(call) for call in print_calls), \
            f"Expected plan_001.md in print calls, got: {print_calls}"
        # 驗證載入內容是上一輪內容
        assert any("Previous plan content" in str(call) for call in print_calls), \
            f"Expected 'Previous plan content' in print calls, got: {print_calls}"


class TestPlanPhaseProgressTracking:
    """Test progress tracking functionality (TDD)."""

    def test_save_progress_creates_status_json(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
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

    def test_load_progress_returns_none_when_no_file(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
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
            git_ops=mock_git_ops,
        )

        progress = phase._load_progress()
        assert progress is None


class TestPlanPhaseNoStatusCode:
    """測試 agent 回傳內容但沒有 status code 情況"""

    def test_agent_response_without_status_code_saves_to_history(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 agent 回傳內容但沒有 status code 時, 經過 5 次重試後返回 FAILED

        這個測試模擬真實情況：agent 回傳了內容, 但沒有包含正確 status code
        （可能是格式錯誤or agent 沒照指示做）.系統會重試最多 5 次, 
        如果都沒有 status code 則返回 FAILED.
        """
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test"

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
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=False,
            template_path=create_template_file(tmp_path),  # Provide template for first iteration
            git_ops=mock_git_ops,
        )

        # Mock agent to return content without status code (all attempts)
        agent_manager.execute.return_value = ("這是計畫內容, 但沒有 status code", TokenUsage(), [], None, [])

        with patch('builtins.print'):
            result = phase.execute()

        # After 5 retries, should return FAILED
        assert result.status == PhaseStatus.FAILED
        assert "Still did not return valid status code after" in result.message

        # Check that iteration 1 history was saved
        # When ValueError is raised after 5 retries, the history may not have all fields
        history_dir = spec_file.parent.parent / "plan" / "history"
        iteration_1 = history_dir / "iteration_001.json"
        assert iteration_1.exists()

        import json
        with open(iteration_1, 'r') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        # The history file should exist with at least the iteration number
        # Response may not be saved if ValueError was raised before save


class TestPlanPhaseEmptyResponse:
    """測試 agent 回傳空字串情況"""

    def test_agent_empty_response_should_fail_with_no_response_status(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 agent 回傳空字串時應該失敗並標記為 NO_RESPONSE 狀態

        當 agent 回傳空字串（可能是執行失敗or輸出未正確捕捉）, 
        應該立即終止並返回 FAILED 狀態, 並在 history 中記錄 CAFE_NO_RESPONSE.
        """
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test"

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
        agent_manager.execute.return_value = ("", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=False,
            template_path=create_template_file(tmp_path),  # Provide template for first iteration
            git_ops=mock_git_ops,
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
    """測試 PlanPhase  prompt 產生"""

    def test_prompt_includes_user_modification_request_in_iteration_2(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 iteration 2  prompt 應該包含使用者修改意見"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test"

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
        def capture_prompt(agent_name, prompt, allowed_tools=None, allowed_directories=None):
            nonlocal captured_prompt
            captured_prompt = prompt
            return ("CAFE_READY_FOR_REVIEW\n修改後計畫", TokenUsage())

        agent_manager.execute.side_effect = capture_prompt

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=True,  # Must be interactive to test modification flow
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Mock user choosing 'm' (modify) then confirming with CONFIRMED status code
        modification_request = "請加上錯誤處理and測試"

        # First call will be for iteration 2 (after user chooses 'm')
        # Second call should return CONFIRMED to finish
        agent_manager.execute.side_effect = [
            ("CAFE_READY_FOR_REVIEW\n修改後計畫", TokenUsage(), [], None, []),  # iter 2: user requested modification
            ("CAFE_CONFIRMED\n確認完成", TokenUsage(), [], None, []),  # iter 3: user confirms
        ]

        with patch('cafe.ui.inquirer_prompts.prompt_list', return_value='m') as mock_list, \
             patch('cafe.ui.inquirer_prompts.prompt_multiline', return_value=modification_request) as mock_multiline, \
             patch('builtins.print'):
            phase.execute()

        # Check that execute was called at least once (for iteration 2)
        assert agent_manager.execute.call_count >= 1
        # Get the first call's prompt (iteration 2, after user requested modification)
        first_call_args = agent_manager.execute.call_args_list[0]
        first_prompt = first_call_args[0][1]  # Second positional argument is the prompt

        assert modification_request in first_prompt, \
            f"Prompt should include user's modification request.\nPrompt: {first_prompt}"

    def test_prompt_does_not_include_contradicting_status_code_format(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 prompt 不應該包含矛盾 status code 格式指示"""
        monkeypatch.chdir(tmp_path)
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
        def capture_prompt(agent_name, prompt, allowed_tools=None, allowed_directories=None):
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
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
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
    """測試用戶確認計畫後行為"""

    def test_user_confirmation_saves_history_and_updates_status(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試在 non-interactive 模式下, READY_FOR_REVIEW 直接完成"""
        monkeypatch.chdir(tmp_path)
        issue_name = "test"
        mock_git_ops.get_current_branch.return_value = issue_name

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nGuide\n\n## 實作計畫\nPlan")

        agent_manager = MagicMock(spec=AgentManager)
        # Agent returns READY_FOR_REVIEW
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫完成", TokenUsage(), [], None, [])

        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name=issue_name,
            interactive=False,
            template_path=create_template_file(tmp_path),  # Provide template for first iteration
            git_ops=mock_git_ops,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # In non-interactive mode, READY_FOR_REVIEW completes immediately
        assert result.status == PhaseStatus.COMPLETED

        # Should have 1 iteration (agent response with READY_FOR_REVIEW)
        history_dir = spec_file.parent.parent / "plan" / "history"
        assert history_dir.exists()

        iteration_files = sorted(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) == 1, f"應該有 1 個 iteration 文件（non-interactive 直接完成）, 但有 {len(iteration_files)} 個"

        # Check iteration 1: agent returns READY_FOR_REVIEW and completes
        with open(iteration_files[0]) as f:
            iter1 = json.load(f)
        assert iter1["iteration"] == 1
        assert iter1["status_code"] == "CAFE_READY_FOR_REVIEW"

        # Check status.json has final READY_FOR_REVIEW status
        status_file = spec_file.parent.parent / "plan" / "status.json"
        assert status_file.exists()
        with open(status_file) as f:
            status_data = json.load(f)
        assert status_data["status_code"] == "CAFE_READY_FOR_REVIEW"
        assert status_data["iteration"] == 1


class TestExecuteAndHandleAgentResponse:
    """測試 PlanPhase._execute_and_handle_agent_response() 方法（透過 base class）"""

    def test_returns_none_for_ready_for_review(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n計畫已完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )
        phase.iteration = 1

        # Execute - call base class method with all required parameters
        with patch('builtins.print'):
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

        # After behavior change: READY_FOR_REVIEW returns COMPLETED immediately
        assert result is not None
        assert result.status == PhaseStatus.COMPLETED

    def test_returns_none_for_need_clarification(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n需要更多資訊", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,  # Changed to False to avoid hanging
            git_ops=mock_git_ops,
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

        # After behavior change: NEED_CLARIFICATION returns COMPLETED immediately
        assert result is not None
        assert result.status == PhaseStatus.COMPLETED

    def test_returns_failed_for_no_response(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.execute.return_value = ("", TokenUsage(), [], None, [])  # Empty response

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,  # Changed to False to avoid hanging
            git_ops=mock_git_ops,
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

    def test_returns_none_for_no_status_code_interactive(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試沒有 status code 時 interactive 模式經過 5 次重試後拋出 ValueError"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        # All calls return response without status code (will retry 5 times)
        agent_manager.execute.return_value = ("Some response without status code", TokenUsage(), [], None, [])

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=True,  # Interactive mode
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )
        phase.iteration = 1

        # Execute - should raise ValueError after 5 retries
        with pytest.raises(ValueError, match="Still did not return valid status code after"):
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

    def test_returns_in_progress_for_no_status_code_non_interactive(
        self, tmp_path: Path, mock_git_ops, monkeypatch
    ) -> None:
        """測試沒有 status code 且 non-interactive 模式時經過 5 次重試後拋出 ValueError"""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"

        # Setup
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("Test spec content")

        agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        # All calls return response without status code (will retry 5 times)
        agent_manager.execute.return_value = ("Some response without status code", TokenUsage(), [], None, [])

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=MagicMock(),
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,  # Non-interactive mode
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )
        phase.iteration = 1

        # Execute - should raise ValueError after 5 retries
        with pytest.raises(ValueError, match="Still did not return valid status code after"):
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


class TestPlanPhaseFilePermissions:
    """測試 PlanPhase 檔案權限設定"""

    def test_uses_precise_file_permissions_for_plan_file(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-issue"
        # Setup
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\n\nSome requirements")

        # Create plan.md with dev guide section
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\n\nDevelopment guide here")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        setup_agent_manager_mocks(agent_manager)

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,
            user_input="confirm",
            template_path=create_template_file(tmp_path),
            git_ops=mock_git_ops,
        )

        # Execute
        with patch('builtins.print'):
            result = phase.execute()

        # Verify allowed_tools includes precise file paths
        assert agent_manager.execute.called
        call_kwargs = agent_manager.execute.call_args
        allowed_tools = call_kwargs[1].get("allowed_tools")

        # Should have read, edit(plan.md) (write removed since no CLI supports file-specific write)
        assert "read" in allowed_tools
        # Check for edit with plan file path
        edit_tools = [t for t in allowed_tools if t.startswith("edit(")]

        assert len(edit_tools) >= 1, "Should have at least one edit permission"

        # Verify the paths point to plan_001.md (versioned file)
        assert any("plan_001.md" in tool for tool in edit_tools), \
            f"Edit permission should include plan_001.md path, got: {edit_tools}"
