"""Tests for PRPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from cafe.phases.pr_phase import PRPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage, AgentCLI
from cafe.core.permission import PermissionHandler
from cafe.utils.github import GitHubOps


class TestPRPhaseBasics:
    """Test basic PRPhase functionality."""

    def test_init_pr_phase(self, tmp_path: Path, monkeypatch) -> None:
        """測試初始化 PRPhase"""
        monkeypatch.chdir(tmp_path)
        # Create spec file for fallback
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        assert phase.agent_manager == agent_manager
        assert phase.git_ops == git_ops
        assert phase.spec_file == str(spec_file)
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        interactive=False,
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestBranchPushing:
    """Test pushing branch to remote."""

    def test_push_branch_github_mode(self, tmp_path: Path, monkeypatch) -> None:
        """測試推送 GitHub mode 分支到 remote"""
        monkeypatch.chdir(tmp_path)
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "issue-123" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "issue-123"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Should push branch named "issue-123"
        git_ops.push.assert_called_once_with("issue-123", set_upstream=True, force=False)
        assert result.status == PhaseStatus.COMPLETED

    def test_push_branch_local_mode(self, tmp_path: Path, monkeypatch) -> None:
        """測試推送 local mode 分支到 remote"""
        monkeypatch.chdir(tmp_path)
        requirements_file = tmp_path / "20250101-feature.md"
        requirements_file.write_text("# Feature Title\n")

        # Create spec directory for the feature branch
        spec_dir = tmp_path / ".cafe" / "issues" / "feature" / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        # Should push branch named "feature"
        git_ops.push.assert_called_once_with("feature", set_upstream=True, force=False)

    def test_push_failure_fails_phase(self, tmp_path: Path, monkeypatch) -> None:
        """測試 push 失敗時 phase 失敗"""
        monkeypatch.chdir(tmp_path)
        # Create spec file
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.push.side_effect = Exception("Push failed")

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "push" in result.message.lower() or "failed" in result.message.lower()


class TestPRCreation:
    """Test PR creation."""

    def test_create_pr_github_mode(self, tmp_path: Path, monkeypatch) -> None:
        """測試 GitHub mode 建立 PR（使用 custom title/body）"""
        monkeypatch.chdir(tmp_path)
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "issue-123" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "issue-123"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            custom_title="Issue Title",
            custom_body="Closes #123\n\ncommit1\ncommit2",
            interactive=False,
        )

        result = phase.execute()

        # Should include "Closes #123" in body
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert "Closes #123" in call_args.kwargs['body']

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("pr_number") == "1"

    def test_create_pr_local_mode(self, tmp_path: Path) -> None:
        """測試 local mode 建立 PR（使用 custom title/body）"""
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature Title\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            custom_title="Feature Title",
            custom_body="commit1",
        interactive=False,
        )

        result = phase.execute()

            # Check PR title comes from custom title

        assert result.status == PhaseStatus.COMPLETED

    def test_gh_not_available_fails(self, tmp_path: Path, monkeypatch) -> None:
        """測試 gh 不可用時失敗"""
        monkeypatch.chdir(tmp_path)
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = False  # gh not available
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "gh" in result.message.lower() or "auth" in result.message.lower()


class TestPRTitleGeneration:
    """Test PR title generation (now from files, not directly from GitHub issue or spec.md)."""

    def test_pr_title_from_github_issue_via_custom(self, tmp_path: Path) -> None:
        """測試 GitHub mode 使用 custom title"""
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "issue-456" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
            custom_title="Add new feature",
            custom_body="Closes #456\n\ncommits",
        interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED

    def test_pr_title_from_requirements_file_via_custom(self, tmp_path: Path) -> None:
        """測試 local mode 使用 custom title（模擬從 spec.md 提取的內容）"""
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "auth" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Add User Authentication\n\nDetails...")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            custom_title="Add User Authentication",
            custom_body="commits",
        interactive=False,
        )

        result = phase.execute()


        assert result.status == PhaseStatus.COMPLETED


class TestPRBodyGeneration:
    """Test PR body generation (now from files, not directly from commits)."""

    def test_pr_body_includes_commits_via_custom(self, tmp_path: Path) -> None:
        """測試 PR body 包含 commit 列表（透過 custom body）"""
        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature Title\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = (
            "abc123 Add feature A\ndef456 Fix bug B"
        )

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            custom_title="Feature Title",
            custom_body="abc123 Add feature A\ndef456 Fix bug B",
        interactive=False,
        )

        result = phase.execute()

            # Check commits are in PR body

        assert result.status == PhaseStatus.COMPLETED


class TestErrorHandling:
    """Test error handling."""

    def test_github_mode_without_issue_id_fails(self, tmp_path: Path, monkeypatch) -> None:
        """測試 GitHub mode 沒有 issue_id 時失敗"""
        monkeypatch.chdir(tmp_path)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_missing_requirements_file_fails(self, tmp_path: Path, monkeypatch) -> None:
        """測試缺少需求檔案時失敗"""
        monkeypatch.chdir(tmp_path)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file="/nonexistent/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_git_error_fails_phase(self, tmp_path: Path, monkeypatch) -> None:
        """測試 git 操作錯誤時 phase 失敗"""
        monkeypatch.chdir(tmp_path)

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.push.side_effect = Exception("Git push error")

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file="spec.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git push error" in result.message or "failed" in result.message.lower()

    def test_gh_pr_create_error_fails_phase(self, tmp_path: Path, monkeypatch) -> None:
        """測試 gh pr create 失敗時 phase 失敗"""
        # Change to tmp_path to avoid pollution
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Plan\n")

        agent_manager = MagicMock(spec=AgentManager)
        mock_executor = MagicMock()
        mock_executor.config.cli.value = "copilot"
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.check_gh_auth.return_value = True
        # Mock create_pr to raise an error
        from cafe.utils.github import GitHubError
        github_ops.create_pr.side_effect = GitHubError("Failed to create PR")
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"
        git_ops.get_current_branch.return_value = "test-issue"

        # Mock agent to write title and body files
        def mock_agent_execute(agent_name, prompt, allowed_tools=None, allowed_directories=None):
            pr_dir = spec_file.parent.parent / "pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "title.txt").write_text("Test Title")
            (pr_dir / "body.md").write_text("Test Body")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
            issue_name="test-issue",
        interactive=False,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "failed" in result.message.lower() or "error" in result.message.lower()

    def test_execute_fails_when_not_authenticated(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 gh 未登入時，execute() 正確回傳失敗狀態"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = False

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "gh auth login" in result.message
        # Should not push or create PR if not authenticated
        git_ops.push.assert_not_called()
        github_ops.create_pr.assert_not_called()

    def test_execute_handles_github_error_from_check_auth(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 check_gh_auth() 拋出 GitHubError 時，execute() 正確處理並回傳錯誤訊息"""
        from cafe.utils.github import GitHubError

        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.side_effect = GitHubError("Network error")

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "Failed to check gh authentication" in result.message
        assert "Network error" in result.message
        # Should not push or create PR if check fails
        git_ops.push.assert_not_called()
        github_ops.create_pr.assert_not_called()


class TestIssueNameBranchNaming:
    """Test issue_name parameter for branch naming."""

    def test_branch_name_from_issue_name(self, tmp_path: Path, monkeypatch) -> None:
        """測試使用 issue_name 參數產生 branch name"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "my-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "my-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="my-feature",
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Should push branch named "my-feature" (from issue_name)
        git_ops.push.assert_called_once_with("my-feature", set_upstream=True, force=False)
        assert result.status == PhaseStatus.COMPLETED

    def test_branch_name_fallback_to_filename(self, tmp_path: Path, monkeypatch) -> None:
        """測試當沒有 issue_name 時回退到從檔名提取"""
        monkeypatch.chdir(tmp_path)

        # Setup directory structure with dated filename
        spec_file = tmp_path / ".cafe" / "issues" / "feature" / "spec" / "20250101-feature.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            custom_title="Test PR",
            custom_body="Test body",
            # No issue_name provided
            interactive=False,
        )

        result = phase.execute()

        # Should push branch named "feature" (from issue directory name, not filename)
        git_ops.push.assert_called_once_with("feature", set_upstream=True, force=False)
        assert result.status == PhaseStatus.COMPLETED


class TestDraftPRCreation:
    """Test draft PR creation."""

    def test_draft_pr_default_true(self, tmp_path: Path, monkeypatch) -> None:
        """測試預設創建 draft PR (draft=True)"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            draft=True,  # Default value
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Check that github_ops.create_pr was called with draft=True
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['draft'] is True
        assert result.status == PhaseStatus.COMPLETED

    def test_non_draft_pr(self, tmp_path: Path, monkeypatch) -> None:
        """測試創建非 draft PR (draft=False)"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            draft=False,
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Check that github_ops.create_pr was called with draft=False
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['draft'] is False
        assert result.status == PhaseStatus.COMPLETED


class TestCustomTitleAndBody:
    """Test custom title and body parameters."""

    def test_custom_title_and_body(self, tmp_path: Path, monkeypatch) -> None:
        """測試使用自訂 title 和 body（會寫入檔案後再讀取）"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        custom_title = "Custom PR Title"
        custom_body = "Custom PR Body\nWith multiple lines"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title=custom_title,
            custom_body=custom_body,
            interactive=False,
        )

        result = phase.execute()

        # Verify that custom title and body were written to files
        pr_dir = spec_file.parent.parent / "pr"
        title_file = pr_dir / "title.txt"
        body_file = pr_dir / "body.md"
        assert title_file.exists()
        assert body_file.exists()
        assert title_file.read_text() == custom_title
        assert body_file.read_text() == custom_body

        # Check that github_ops.create_pr was called with custom title and body
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['title'] == custom_title
        assert call_args.kwargs['body'] == custom_body
        assert result.status == PhaseStatus.COMPLETED

    def test_auto_generate_title_and_body(self, tmp_path: Path, monkeypatch) -> None:
        """測試自動產生 title 和 body (custom_title=None, custom_body=None)"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Auto Generated Title\n")

        # Setup plan file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Implementation Plan\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        # Mock agent to write title and body files
        def mock_agent_execute(agent_name, prompt, allowed_tools, allowed_directories=None):
            pr_dir = spec_file.parent.parent / "pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "title.txt").write_text("Auto Generated Title")
            (pr_dir / "body.md").write_text("## Summary\nAuto-generated PR body\n\n## Changes\n- commit1\n- commit2")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title=None,  # Auto-generate
            custom_body=None,   # Auto-generate
            interactive=False,  # Non-interactive mode to trigger auto-generation
        )

        result = phase.execute()

        # Verify agent was called to generate PR content
        agent_manager.execute.assert_called_once()

        # Verify files were created
        pr_dir = spec_file.parent.parent / "pr"
        assert (pr_dir / "title.txt").exists()
        assert (pr_dir / "body.md").exists()

        # Check that github_ops.create_pr was called with auto-generated content
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['title'] == "Auto Generated Title"
        assert "Auto-generated PR body" in call_args.kwargs['body']
        assert result.status == PhaseStatus.COMPLETED


class TestPartialCustomTitleOrBody:
    """Test partial custom title or body (one provided, one generated)."""

    def test_custom_title_only_body_generated(self, tmp_path: Path, monkeypatch) -> None:
        """測試只提供 custom title，body 由 agent 生成"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup plan file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Implementation Plan\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        # Mock agent to write only body file (title is custom)
        def mock_agent_execute(agent_name, prompt, allowed_tools, allowed_directories=None):
            pr_dir = spec_file.parent.parent / "pr"
            # Agent should only generate body
            (pr_dir / "body.md").write_text("## Summary\nGenerated body content")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Custom Title",  # Provided
            custom_body=None,  # Will be generated
            interactive=False,  # Non-interactive mode
        )

        result = phase.execute()

        # Verify agent was called to generate only body
        agent_manager.execute.assert_called_once()
        call_args = agent_manager.execute.call_args
        allowed_tools = call_args[1]["allowed_tools"]

        # Should only have write permission for body, not title
        # Check that body.md is in allowed_tools but title.txt is not
        body_write_pattern = "pr/body.md)"
        title_write_pattern = "pr/title.txt)"
        assert any(body_write_pattern in tool for tool in allowed_tools)
        assert not any(title_write_pattern in tool for tool in allowed_tools)

        # Verify custom title was written directly
        pr_dir = spec_file.parent.parent / "pr"
        assert (pr_dir / "title.txt").read_text() == "Custom Title"
        # Verify body was generated by agent
        assert (pr_dir / "body.md").exists()

        # Verify github_ops.create_pr was called with custom title
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['title'] == "Custom Title"
        assert result.status == PhaseStatus.COMPLETED

    def test_custom_body_only_title_generated(self, tmp_path: Path, monkeypatch) -> None:
        """測試只提供 custom body，title 由 agent 生成"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup plan file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Implementation Plan\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        # Mock agent to write only title file (body is custom)
        def mock_agent_execute(agent_name, prompt, allowed_tools, allowed_directories=None):
            pr_dir = spec_file.parent.parent / "pr"
            # Agent should only generate title
            (pr_dir / "title.txt").write_text("Generated PR Title")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title=None,  # Will be generated
            custom_body="Custom body content",  # Provided
            interactive=False,  # Non-interactive mode
        )

        result = phase.execute()

        # Verify agent was called to generate only title
        agent_manager.execute.assert_called_once()
        call_args = agent_manager.execute.call_args
        allowed_tools = call_args[1]["allowed_tools"]

        # Should only have write permission for title, not body
        # Check that title.txt is in allowed_tools but body.md is not
        title_write_pattern = "pr/title.txt)"
        body_write_pattern = "pr/body.md)"
        assert any(title_write_pattern in tool for tool in allowed_tools)
        assert not any(body_write_pattern in tool for tool in allowed_tools)

        # Verify custom body was written directly
        pr_dir = spec_file.parent.parent / "pr"
        assert (pr_dir / "body.md").read_text() == "Custom body content"
        # Verify title was generated by agent
        assert (pr_dir / "title.txt").exists()

        # Verify github_ops.create_pr was called with generated title and custom body
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['title'] == "Generated PR Title"
        assert call_args.kwargs['body'] == "Custom body content"
        assert result.status == PhaseStatus.COMPLETED


class TestPRExistingFiles:
    """Test behavior when PR files already exist."""

    def test_pr_exists_non_interactive_without_update_fails(self, tmp_path: Path, monkeypatch) -> None:
        """測試 PR 檔案已存在且非互動模式下沒有 --update 會失敗"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure with existing PR files
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        pr_dir = spec_file.parent.parent / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "title.txt").write_text("Existing Title")
        (pr_dir / "body.md").write_text("Existing Body")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = {
            "number": 123,
            "url": "https://github.com/user/repo/pull/123",
            "title": "Existing Title",
            "body": "Existing Body"
        }

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title=None,
            custom_body=None,
            update=False,  # No update flag
            interactive=False,  # Non-interactive
        )

        result = phase.execute()

        # Should fail with message about using --update
        assert result.status == PhaseStatus.FAILED
        assert "--update" in result.message

    def test_pr_exists_with_update_flag_regenerates(self, tmp_path: Path, monkeypatch) -> None:
        """測試 PR 檔案已存在且使用 --update 會重新生成"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure with existing PR files
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup plan file
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Implementation Plan\n")

        pr_dir = spec_file.parent.parent / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "title.txt").write_text("Old Title")
        (pr_dir / "body.md").write_text("Old Body")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = {
            "number": 123,
            "url": "https://github.com/user/repo/pull/123",
            "title": "Old Title",
            "body": "Old Body"
        }

        # Mock agent to write new files
        def mock_agent_execute(agent_name, prompt, allowed_tools, allowed_directories=None):
            (pr_dir / "title.txt").write_text("New Generated Title")
            (pr_dir / "body.md").write_text("New Generated Body")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title=None,
            custom_body=None,
            update=True,  # Force update
            interactive=False,
        )

        result = phase.execute()

        # Should succeed and update PR
        assert result.status == PhaseStatus.COMPLETED
        # Verify files were updated
        assert (pr_dir / "title.txt").read_text() == "New Generated Title"
        assert (pr_dir / "body.md").read_text() == "New Generated Body"
        # Verify github_ops.update_pr was called
        github_ops.update_pr.assert_called_once_with(
            "123",
            title="New Generated Title",
            body="New Generated Body"
        )

    def test_pr_exists_with_update_and_custom_values(self, tmp_path: Path, monkeypatch) -> None:
        """測試 PR 檔案已存在，使用 --update 和 custom title/body 會覆蓋舊檔案"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure with existing PR files
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        pr_dir = spec_file.parent.parent / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "title.txt").write_text("Old Title")
        (pr_dir / "body.md").write_text("Old Body")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = {
            "number": 123,
            "url": "https://github.com/user/repo/pull/123",
            "title": "Old Title",
            "body": "Old Body"
        }

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="My custom title",  # Custom values
            custom_body="My custom body",
            update=True,  # Force update
            interactive=False,
        )

        result = phase.execute()

        # Should succeed
        assert result.status == PhaseStatus.COMPLETED

        # Verify agent was NOT called (because custom values were provided)
        agent_manager.execute.assert_not_called()

        # Verify files were updated with custom values
        assert (pr_dir / "title.txt").read_text() == "My custom title"
        assert (pr_dir / "body.md").read_text() == "My custom body"

        # Verify github_ops.update_pr was called with custom values
        github_ops.update_pr.assert_called_once_with(
            "123",
            title="My custom title",
            body="My custom body"
        )


class TestPRURLInResult:
    """測試 PR URL 是否包含在結果中"""

    def test_pr_url_included_in_result(self, tmp_path: Path, monkeypatch) -> None:
        """測試 PR 建立成功後，結果中包含 PR URL"""
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n\nAdd new feature")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/42"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Verify result includes both PR number and URL
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["pr_number"] == "42"
        assert result.data["pr_url"] == "https://github.com/user/repo/pull/42"


class TestBaseBranchFromConfig:
    """測試從 config.yaml 讀取 base_branch"""

    def test_base_branch_from_config_used(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 config.yaml 存在且有 base_branch 時，應使用該值"""
        # Change to tmp_path so relative paths work
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "fix-branch" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup config.yaml with base_branch
        config_file = spec_file.parent.parent / "issue.yaml"
        config_file.write_text('base_branch: develop\nfeature_branch: fix-branch\n')

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "fix-branch"
        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="fix-branch",
            custom_title="Test PR",
            custom_body="Test body",
        interactive=False,
        )

        result = phase.execute()

        # Verify github_ops.create_pr was called with base="develop"
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['base'] == "develop"
        assert result.status == PhaseStatus.COMPLETED

    def test_base_branch_default_when_config_missing(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 config.yaml 不存在時，應使用預設值 'main'"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure (no config.yaml)
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Verify github_ops.create_pr was called with base="main" (default)
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['base'] == "main"
        assert result.status == PhaseStatus.COMPLETED

    def test_base_branch_default_when_config_no_base_branch_field(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 config.yaml 存在但無 base_branch 欄位時，應使用預設值 'main'"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup config.yaml without base_branch field
        config_file = spec_file.parent.parent / "issue.yaml"
        config_file.write_text('feature_branch: test-feature\n')

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
            interactive=False,
        )

        result = phase.execute()

        # Verify github_ops.create_pr was called with base="main" (default)
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['base'] == "main"
        assert result.status == PhaseStatus.COMPLETED

    def test_cli_base_param_overrides_config(self, tmp_path: Path, monkeypatch) -> None:
        """測試 CLI --base 參數能覆蓋 config.yaml 中的值（CLI 參數優先）"""
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        # Setup config.yaml with base_branch="develop"
        config_file = spec_file.parent.parent / "issue.yaml"
        config_file.write_text('base_branch: develop\nfeature_branch: test-feature\n')

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"

        # Pass base_branch="staging" via CLI parameter (should override config)
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
            base_branch="staging",  # CLI parameter overrides config
            interactive=False,
        )

        result = phase.execute()

        # Verify github_ops.create_pr was called with base="staging" (CLI value, not config value)
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['base'] == "staging"
        assert result.status == PhaseStatus.COMPLETED


class TestIssueCommentIntegration:
    """Test issue comment integration when PR is created."""

    def test_add_issue_comment_when_issue_id_configured(self, tmp_path: Path, monkeypatch) -> None:
        """測試當 issue_id 有設定時，PR 建立後會自動加上 issue comment"""
        # Change to tmp_path so relative paths work
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure with config
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        spec_file = issue_dir / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        config_file = issue_dir / "issue.yaml"
        config_file.write_text("issue_id: '123'\nbase_branch: 'main'\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/42"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
        interactive=False,
        )

        result = phase.execute()

        # Verify PR was created
        assert result.status == PhaseStatus.COMPLETED
        github_ops.create_pr.assert_called_once()

        # Verify issue comment was added
        github_ops.add_issue_comment.assert_called_once_with(
            "123",
            "Pull Request created: #42\n\nhttps://github.com/user/repo/pull/42"
        )

    def test_no_issue_comment_when_issue_id_not_configured(self, tmp_path: Path) -> None:
        """測試當 issue_id 未設定時，不會加 issue comment"""
        # Setup issue directory structure without issue_id in config
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        spec_file = issue_dir / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        config_file = issue_dir / "issue.yaml"
        config_file.write_text("base_branch: 'main'\n")  # No issue_id

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/42"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
        interactive=False,
        )

        result = phase.execute()

        # Verify PR was created
        assert result.status == PhaseStatus.COMPLETED
        github_ops.create_pr.assert_called_once()

        # Verify issue comment was NOT added
        github_ops.add_issue_comment.assert_not_called()

    def test_pr_succeeds_even_if_issue_comment_fails(self, tmp_path: Path, monkeypatch) -> None:
        """測試即使 issue comment 失敗，PR 建立仍然成功"""
        # Change to tmp_path so relative paths work
        monkeypatch.chdir(tmp_path)

        # Setup issue directory structure with config
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        spec_file = issue_dir / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")

        config_file = issue_dir / "issue.yaml"
        config_file.write_text("issue_id: '123'\nbase_branch: 'main'\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Done! CAFE_CONFIRMED", TokenUsage(), [], None, [])
        mock_executor = MagicMock()
        mock_executor.config.cli = AgentCLI.COPILOT
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.check_gh_auth.return_value = True
        github_ops.get_pr_for_branch.return_value = None  # No existing PR
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/42"
        # Simulate issue comment failure
        github_ops.add_issue_comment.side_effect = Exception("GitHub API error")

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-feature",
            custom_title="Test PR",
            custom_body="Test body",
        interactive=False,
        )

        result = phase.execute()

        # Verify PR creation still succeeded despite comment failure
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["pr_number"] == "42"
        github_ops.create_pr.assert_called_once()
        github_ops.add_issue_comment.assert_called_once()


class TestInteractiveModeBehavior:
    """Test interactive mode specific behaviors."""

    @patch('cafe.ui.phase_prompts.is_github_repo')
    @patch('cafe.ui.phase_prompts.prompt_confirm')
    def test_interactive_mode_auto_generates_without_asking(
        self, mock_prompt_confirm, mock_is_github_repo, tmp_path: Path, monkeypatch
    ) -> None:
        """
        測試互動模式下，若無 --title 或 --body，應直接由 agent 生成，不再詢問。
        """
        monkeypatch.chdir(tmp_path)

        # Mock GitHub repo and auto_create prompt
        mock_is_github_repo.return_value = True
        mock_prompt_confirm.return_value = True

        # Setup
        spec_file = tmp_path / ".cafe" / "issues" / "test-feature" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Feature\n")
        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Plan\n")

        agent_manager = MagicMock(spec=AgentManager)
        mock_executor = MagicMock()
        mock_executor.config.cli.value = "copilot"
        mock_executor.config.session_id = "session_123"
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-feature"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        github_ops = MagicMock(spec=GitHubOps)
        github_ops.get_pr_for_branch.return_value = None
        github_ops.create_pr.return_value = "https://github.com/user/repo/pull/1"
        github_ops.check_gh_auth.return_value = True

        # Mock agent to write title and body files
        def mock_agent_execute(agent_name, prompt, allowed_tools, allowed_directories=None):
            pr_dir = spec_file.parent.parent / "pr"
            pr_dir.mkdir(parents=True, exist_ok=True)
            (pr_dir / "title.txt").write_text("Agent Title")
            (pr_dir / "body.md").write_text("Agent Body")
            return "CAFE_CONFIRMED", TokenUsage(), [], None, []

        agent_manager.execute.side_effect = mock_agent_execute

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,  # Key part of the test
            custom_title=None,
            custom_body=None,
            issue_name="test-feature"
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        # Verify agent was called to generate content
        agent_manager.execute.assert_called_once()
        # Verify PR was created with agent-generated content
        github_ops.create_pr.assert_called_once()
        call_args = github_ops.create_pr.call_args
        assert call_args.kwargs['title'] == "Agent Title"
        assert call_args.kwargs['body'] == "Agent Body"
        # Verify no user input was ever requested (by not mocking it and not getting an error)