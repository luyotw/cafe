"""Tests for PlanPhase with status codes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.types import PhaseStatus, TokenUsage
from cafe.core.git import GitOperations
from cafe.phases.plan_phase import PlanPhase
from cafe.utils.checklist_validator import ChecklistValidationResult



@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test"
    return git_ops

def create_template_file(tmp_path: Path) -> str:
    """Create a dummy template file for tests."""
    template_file = tmp_path / "template.md"
    template_file.write_text("# Plan Template\n\nTemplate content")
    return str(template_file)


class TestPlanPhaseWithStatusCodes:
    """Test PlanPhase integration with status code system."""

    def test_confirmed_status_code_completes_phase(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        # Create checklist.md with all items completed
        checklist_dir = plan_file.parent / "iteration_001"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_file = checklist_dir / "checklist.md"
        checklist_file.write_text("## Execution Steps Checklist\n\n[x] Step 1\n[x] Step 2\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [], None)
        agent_manager.preview_cli_command_args.return_value = ["--model", "copilot"]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock checklist validation
        def mock_validate_checklist(checklist_path):
            return ChecklistValidationResult(is_complete=True, unchecked_count=0, checklist_path=checklist_path)

        with patch('cafe.utils.checklist_validator.validate_checklist', side_effect=mock_validate_checklist), \
             patch('builtins.print'):
            phase = PlanPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                spec_file=str(requirements_file),
                    interactive=False,
                user_input="confirm",  # Simulate user confirming the plan
                template_path=create_template_file(tmp_path),
            )

            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"  # non-interactive mode completes at READY_FOR_REVIEW
        assert result.data.get("iterations") == 1  # Only 1 iteration in non-interactive mode

    def test_need_clarification_continues_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        """測試 NEED_CLARIFICATION 繼續迭代"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # After removing while loop, only executes once and returns IN_PROGRESS
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請補充更多資訊.", TokenUsage(), [], None, [], None)
        agent_manager.preview_cli_command_args.return_value = ["--model", "copilot"]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
                git_ops=mock_git_ops,
            spec_file=str(requirements_file),
            interactive=True,
            template_path=create_template_file(tmp_path),
        )

        # Mock user input and confirmation
        from unittest.mock import patch
        mock_multiline_input.return_value = "補充資訊"
        with patch('builtins.input', return_value='c'), \
             patch('builtins.print'):  # 'c' for confirm
            result = phase.execute()

        # After removing while loop, NEED_CLARIFICATION returns COMPLETED (no automatic continuation)
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"
        assert result.data.get("iterations") == 1
        assert agent_manager.execute.call_count == 1

    def test_status_code_in_middle_of_response(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試狀態碼在回應中間也能識別"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        # Create checklist.md with all items completed
        checklist_dir = plan_file.parent / "iteration_001"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_file = checklist_dir / "checklist.md"
        checklist_file.write_text("## Execution Steps Checklist\n\n[x] Step 1\n[x] Step 2\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("分析結果：\nCAFE_READY_FOR_REVIEW\n實作分析已完成.", TokenUsage(), [], None, [], None)
        agent_manager.preview_cli_command_args.return_value = ["--model", "copilot"]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "copilot"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock checklist validation
        def mock_validate_checklist(checklist_path):
            return ChecklistValidationResult(is_complete=True, unchecked_count=0, checklist_path=checklist_path)

        with patch('cafe.utils.checklist_validator.validate_checklist', side_effect=mock_validate_checklist), \
             patch('builtins.print'):
            phase = PlanPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                spec_file=str(requirements_file),
                    interactive=False,
                user_input="confirm",  # Simulate user confirming the plan
                template_path=create_template_file(tmp_path),
            )

            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"  # non-interactive completes at READY_FOR_REVIEW

    def test_no_status_code_continues_iteration(self, tmp_path: Path, mock_git_ops, monkeypatch, mock_multiline_input) -> None:
        """測試沒有狀態碼時繼續迭代"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        agent_manager = MagicMock(spec=AgentManager)
        # After removing while loop, when no status code, _analyze_missing_status_code is called
        # which calls agent again to get status code. However, the status code from the second
        # call is used internally but the original response (without status code) is returned.
        # So when we extract status code from response in plan_phase.py, we get None.
        agent_manager.execute.side_effect = [
            ("這是一般回應, 沒有狀態碼.", TokenUsage(), [], None, [], None),
            ("CAFE_NEED_CLARIFICATION\n請補充技術選型.", TokenUsage(), [], None, [], None),
        ]
        agent_manager.preview_cli_command_args.return_value = ["--model", "claude"]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
                git_ops=mock_git_ops,
            spec_file=str(requirements_file),
            interactive=True,  # Must be interactive to continue iterations
            template_path=create_template_file(tmp_path),
        )

        mock_multiline_input.return_value = "回應"
        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'):
            result = phase.execute()

        # After behavior change: _analyze_missing_status_code returns NEED_CLARIFICATION, which now completes immediately
        # The extracted status code from _analyze_missing_status_code is NEED_CLARIFICATION
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"  # Status code from second call
        assert agent_manager.execute.call_count == 2  # First call + _analyze_missing_status_code

    def test_case_insensitive_status_code(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """測試狀態碼不區分大小寫"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        requirements_file.parent.mkdir(parents=True, exist_ok=True)
        requirements_file.write_text("# 需求\n\n## 開發指南\nSome guide")

        # Create plan.md with dev guide
        plan_file = requirements_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("## 開發指南\nSome guide\n\n## 實作計畫\nTODO")

        # Create checklist.md with all items completed
        checklist_dir = plan_file.parent / "iteration_001"
        checklist_dir.mkdir(parents=True, exist_ok=True)
        checklist_file = checklist_dir / "checklist.md"
        checklist_file.write_text("## Execution Steps Checklist\n\n[x] Step 1\n[x] Step 2\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("cafe_ready_for_review\n實作分析已完成.", TokenUsage(), [], None, [], None)
        agent_manager.preview_cli_command_args.return_value = ["--model", "claude"]

        # Mock get_agent to return agent with config
        mock_agent = MagicMock()
        mock_agent.config.cli.value = "claude"
        mock_agent.config.session_id = "test_session"
        agent_manager.get_agent.return_value = mock_agent
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        # Mock checklist validation
        def mock_validate_checklist(checklist_path):
            return ChecklistValidationResult(is_complete=True, unchecked_count=0, checklist_path=checklist_path)

        with patch('cafe.utils.checklist_validator.validate_checklist', side_effect=mock_validate_checklist), \
             patch('builtins.print'):
            phase = PlanPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                    git_ops=mock_git_ops,
                spec_file=str(requirements_file),
                    interactive=False,
                user_input="confirm",  # Simulate user confirming the plan
                template_path=create_template_file(tmp_path),
            )

            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"  # non-interactive completes at READY_FOR_REVIEW
