"""Tests for PRPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from aaf.phases.pr_phase import PRPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestPRPhaseBasics:
    """Test basic PRPhase functionality."""

    def test_init_pr_phase(self) -> None:
        """測試初始化 PRPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.git_ops == git_ops
        assert phase.requirements_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestBranchPushing:
    """Test pushing branch to remote."""

    def test_push_branch_github_mode(self) -> None:
        """測試推送 GitHub mode 分支到 remote"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "https://github.com/user/repo/pull/1"
            mock_run.return_value.returncode = 0
            result = phase.execute()

        # Should push branch named "issue-123"
        git_ops.push.assert_called_once_with("issue-123", set_upstream=True)
        assert result.status == PhaseStatus.COMPLETED

    def test_push_branch_local_mode(self, tmp_path: Path) -> None:
        """測試推送 local mode 分支到 remote"""
        requirements_file = tmp_path / "20250101-feature.md"
        requirements_file.write_text("# Feature Title\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "https://github.com/user/repo/pull/1"
            mock_run.return_value.returncode = 0
            result = phase.execute()

        # Should push branch named "feature"
        git_ops.push.assert_called_once_with("feature", set_upstream=True)

    def test_push_failure_fails_phase(self) -> None:
        """測試 push 失敗時 phase 失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.push.side_effect = Exception("Push failed")

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "push" in result.message.lower() or "failed" in result.message.lower()


class TestPRCreation:
    """Test PR creation."""

    def test_create_pr_github_mode(self) -> None:
        """測試 GitHub mode 建立 PR"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1\ncommit2"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        with patch("subprocess.run") as mock_run:
            # Mock gh issue view (called from _get_pr_title)
            # Mock gh pr create (called from _create_pr)
            def side_effect(*args, **kwargs):
                result = MagicMock()
                result.returncode = 0
                cmd = " ".join(args[0]) if args else ""
                if "gh issue view" in cmd:
                    result.stdout = "Issue Title"
                elif "gh pr create" in cmd:
                    result.stdout = "https://github.com/user/repo/pull/1"
                else:
                    result.stdout = ""
                return result

            mock_run.side_effect = side_effect

            result = phase.execute()

            # Should include "Closes #123" in body
            call_str = str(mock_run.call_args_list)
            assert "Closes #123" in call_str

        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("pr_number") == "1"

    def test_create_pr_local_mode(self, tmp_path: Path) -> None:
        """測試 local mode 建立 PR"""
        requirements_file = tmp_path / "20250101-feature.md"
        requirements_file.write_text("# Feature Title\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commit1"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "https://github.com/user/repo/pull/2"
            mock_run.return_value.returncode = 0

            result = phase.execute()

            # Check PR title comes from requirements file
            call_str = str(mock_run.call_args_list)
            assert "Feature Title" in call_str

        assert result.status == PhaseStatus.COMPLETED

    def test_gh_not_available_fails(self) -> None:
        """測試 gh 不可用時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("subprocess.run") as mock_run:
            # Simulate gh command not found
            mock_run.side_effect = FileNotFoundError("gh not found")

            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "gh" in result.message.lower() or "not found" in result.message.lower()


class TestPRTitleGeneration:
    """Test PR title generation."""

    def test_pr_title_from_github_issue(self) -> None:
        """測試從 GitHub issue 取得 PR title"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
        )

        with patch("subprocess.run") as mock_run:
            # Mock gh issue view and gh pr create
            def side_effect(*args, **kwargs):
                cmd = " ".join(args[0]) if isinstance(args[0], list) else args[0]
                mock_result = MagicMock()
                mock_result.returncode = 0

                if "gh issue view" in cmd:
                    mock_result.stdout = "Add new feature"
                elif "gh pr create" in cmd:
                    mock_result.stdout = "https://github.com/user/repo/pull/3"
                return mock_result

            mock_run.side_effect = side_effect

            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED

    def test_pr_title_from_requirements_file(self, tmp_path: Path) -> None:
        """測試從需求檔案第一行取得 PR title"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Add User Authentication\n\nDetails...")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "https://github.com/user/repo/pull/4"
            mock_run.return_value.returncode = 0

            result = phase.execute()

            call_str = str(mock_run.call_args_list)
            assert "Add User Authentication" in call_str

        assert result.status == PhaseStatus.COMPLETED


class TestPRBodyGeneration:
    """Test PR body generation."""

    def test_pr_body_includes_commits(self, tmp_path: Path) -> None:
        """測試 PR body 包含 commit 列表"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Feature Title\n")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = (
            "abc123 Add feature A\ndef456 Fix bug B"
        )

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "https://github.com/user/repo/pull/5"
            mock_run.return_value.returncode = 0

            result = phase.execute()

            # Check commits are in PR body
            call_str = str(mock_run.call_args_list)
            assert "abc123" in call_str or "Add feature A" in call_str

        assert result.status == PhaseStatus.COMPLETED


class TestErrorHandling:
    """Test error handling."""

    def test_github_mode_without_issue_id_fails(self) -> None:
        """測試 GitHub mode 沒有 issue_id 時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_missing_requirements_file_fails(self) -> None:
        """測試缺少需求檔案時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="/nonexistent/requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_git_error_fails_phase(self) -> None:
        """測試 git 操作錯誤時 phase 失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.push.side_effect = Exception("Git push error")

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git push error" in result.message or "failed" in result.message.lower()

    def test_gh_pr_create_error_fails_phase(self) -> None:
        """測試 gh pr create 失敗時 phase 失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_commits_between.return_value = "commits"

        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        with patch("subprocess.run") as mock_run:
            # First call for gh issue view (title) succeeds
            # Second call for gh pr create fails
            def side_effect(*args, **kwargs):
                cmd = " ".join(args[0]) if args else ""
                result = MagicMock()
                if "gh issue view" in cmd:
                    result.returncode = 0
                    result.stdout = "Issue Title"
                elif "gh pr create" in cmd:
                    result.returncode = 1
                    result.stderr = "PR creation failed"
                return result

            mock_run.side_effect = side_effect

            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "failed" in result.message.lower()
