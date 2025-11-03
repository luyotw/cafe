"""Tests for ReviewPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.review_phase import ReviewPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestReviewPhaseBasics:
    """Test basic ReviewPhase functionality."""

    def test_init_review_phase(self) -> None:
        """測試初始化 ReviewPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
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

    def test_init_with_target_commit(self) -> None:
        """測試設定特定 commit"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
            target_commit="abc123",
        )

        assert phase.target_commit == "abc123"


class TestSingleIterationExecution:
    """Test single iteration execution."""

    def test_single_review_iteration_confirmed(self, tmp_path: Path) -> None:
        """測試單次 review 迭代通過"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # Review agent approves immediately
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert "passed" in result.message.lower()
        assert result.data["status_code"] == "CONFIRMED"

    def test_single_review_iteration_needs_changes(self, tmp_path: Path) -> None:
        """測試單次 review 迭代需要修改"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "NEEDS_CHANGES\n問題 1: 需要修正"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "NEEDS_CHANGES"

    def test_only_executes_once(self, tmp_path: Path) -> None:
        """測試只執行一次（不迴圈）"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "NEEDS_CHANGES\n需要修正"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            result = phase.execute()

        # Should call agent exactly once
        assert agent_manager.execute.call_count == 1
        assert result.status == PhaseStatus.COMPLETED


class TestDiffChecking:
    """Test diff checking."""

    def test_no_diff_fails(self, tmp_path: Path) -> None:
        """測試沒有 diff 時失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = ""  # No diff

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "no changes" in result.message.lower()

    def test_full_branch_diff(self, tmp_path: Path) -> None:
        """測試完整 branch diff"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        # Should get diff from main to HEAD
        git_ops.get_diff.assert_called_once_with(base="main", head="HEAD")

    def test_commit_specific_diff(self, tmp_path: Path) -> None:
        """測試特定 commit diff"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            target_commit="abc123",
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        # Should get diff for specific commit
        git_ops.get_diff.assert_called_once_with(base="abc123^", head="abc123")

    def test_diff_includes_in_review_prompt(self, tmp_path: Path) -> None:
        """測試 diff 包含在 review prompt 中"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff --git a/file.py"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        # Check that diff was included in review prompt
        call_args = agent_manager.execute.call_args_list[0][0]
        prompt = call_args[1]
        assert "diff --git a/file.py" in prompt


class TestAgentSelection:
    """Test agent selection for review."""

    def test_uses_review_agent(self, tmp_path: Path) -> None:
        """測試使用 review agent"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            review_agent="Richard",
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        # Check agent was used correctly
        calls = agent_manager.execute.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "Richard"


class TestPromptGeneration:
    """Test prompt generation for review."""

    def test_review_prompt_structure(self, tmp_path: Path) -> None:
        """測試 review prompt 結構"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "程式碼變更" in prompt
        assert "狀態碼" in prompt
        assert "審查完成後請回傳狀態碼，指令執行即結束" in prompt

    def test_review_prompt_no_iteration_count(self, tmp_path: Path) -> None:
        """測試 review prompt 不包含迭代次數"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        # Should not mention iteration numbers
        assert "第 1 輪" not in prompt
        assert "第 2 輪" not in prompt


class TestReviewResultSaving:
    """Test review result saving."""

    def test_saves_review_result(self, tmp_path: Path) -> None:
        """測試儲存 review 結果"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("Requirements")

        # Create issue structure
        issue_dir = tmp_path / "myissue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        with patch("aaf.phases.review_phase.Path.mkdir"):
            with patch("aaf.phases.review_phase.Path.write_text") as mock_write:
                with patch("aaf.phases.review_phase.Path.glob", return_value=[]):
                    phase.execute()

        # Should save review result
        assert mock_write.called

    def test_saves_to_history(self, tmp_path: Path) -> None:
        """測試儲存到 history"""
        requirements_file = tmp_path / "spec.md"
        requirements_file.write_text("Requirements")

        # Create issue structure
        issue_dir = tmp_path / "myissue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_dir = tmp_path / "myissue" / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        history_dir = review_dir / "history"
        history_dir.mkdir(exist_ok=True)

        with patch("aaf.phases.review_phase.Path") as MockPath:
            # Mock Path to use our temp directory
            mock_review_path = MagicMock()
            mock_review_path.parent.parent.name = "myissue"
            mock_review_path.exists.return_value = True
            mock_review_path.read_text.return_value = "Requirements"

            MockPath.return_value = mock_review_path

            phase.execute()


class TestGitHubWorkflow:
    """Test GitHub workflow."""

    def test_github_workflow_uses_issue(self) -> None:
        """測試 GitHub workflow 使用 issue"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\nCode looks good!"

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        with patch.object(phase, "_save_review_result"):
            phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "gh issue view 123" in prompt


class TestErrorHandling:
    """Test error handling."""

    def test_git_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 git 錯誤時 phase 失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.side_effect = Exception("Git error")

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git error" in result.message
