"""Tests for DevelopPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.develop_phase import DevelopPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestDevelopPhaseBasics:
    """Test basic DevelopPhase functionality."""

    def test_init_develop_phase(self) -> None:
        """測試初始化 DevelopPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.git_ops == git_ops
        assert phase.spec_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestBranchManagement:
    """Test git branch management."""

    def test_create_new_branch_local_mode(self, tmp_path: Path) -> None:
        """測試 local mode 建立新分支"""
        requirements_file = tmp_path / "20250101-feature.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Should create branch named "feature"
        git_ops.create_branch.assert_called_once_with("feature")

    def test_switch_to_existing_branch(self, tmp_path: Path) -> None:
        """測試切換到已存在的分支"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = True

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        phase.execute()

        # Should checkout existing branch
        git_ops.checkout_branch.assert_called_once_with("issue-123")

    def test_github_mode_branch_name(self) -> None:
        """測試 GitHub mode 分支名稱"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
        )

        phase.execute()

        # Should create branch named "issue-456"
        git_ops.create_branch.assert_called_once_with("issue-456")


class TestDevelopmentExecution:
    """Test development execution."""

    def test_execute_development_local_mode(self, tmp_path: Path) -> None:
        """測試執行開發 local mode"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_execute_development_github_mode(self) -> None:
        """測試執行開發 GitHub mode"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # Should use gh issue view in prompt
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]
        assert "gh issue view 123" in prompt


class TestPromptGeneration:
    """Test development prompt generation."""

    def test_local_mode_prompt(self, tmp_path: Path) -> None:
        """測試 local mode prompt"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "requirements.md" in prompt
        assert "先不要 push" in prompt

    def test_github_mode_prompt(self) -> None:
        """測試 GitHub mode prompt"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "gh issue view 123" in prompt
        assert "gh issue comment 123" in prompt


class TestAgentSelection:
    """Test developer agent selection."""

    def test_uses_dev_agent(self, tmp_path: Path) -> None:
        """測試使用 Dev agent (David)"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "Development completed"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
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
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
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
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_agent_execution_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 agent 執行錯誤時 phase 失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message

    def test_git_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 git 操作錯誤時 phase 失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.side_effect = Exception("Git error")

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git error" in result.message
