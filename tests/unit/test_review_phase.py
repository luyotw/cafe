"""Tests for ReviewPhase."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.review_phase import ReviewPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.types import PhaseResult, PhaseStatus, TokenUsage, WorkflowMode
from cafe.core.permission import PermissionHandler


def setup_agent_manager_mock(agent_manager: MagicMock, agent_name: str = "Richard") -> None:
    """Setup agent_manager mock with get_agent method."""
    from cafe.core.types import AgentCLI, AgentConfig, TokenUsage
    mock_agent_executor = MagicMock()
    mock_agent_executor.config = AgentConfig(
        name=agent_name,
        cli=AgentCLI.CLAUDE,
        session_id='test_session'
    )
    agent_manager.get_agent.return_value = mock_agent_executor
    agent_manager.get_total_token_usage.return_value = TokenUsage()


class TestReviewIterationNumbering:
    """測試 review iteration 編號功能"""

    def test_first_review_gets_iteration_1(self, tmp_path: Path) -> None:
        """測試第一次 review 時 iteration 為 1"""
        issue_name = "test-first-review"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\nReview passed",
            TokenUsage(),
            [],
            None
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        # Should be iteration 1 (first review)
        assert phase.iteration == 1

    def test_second_review_gets_iteration_2(self, tmp_path: Path) -> None:
        """測試當已有 review_001.md 時，下次 review iteration 為 2"""
        issue_name = "test-second-review"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        # Create existing review file
        review_dir = spec_file.parent.parent / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / "review_001.md").write_text("First review")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\nSecond review passed",
            TokenUsage(),
            [],
            None
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        # Should be iteration 2 (second review)
        assert phase.iteration == 2

    def test_prompt_includes_review_file_path(self, tmp_path: Path) -> None:
        """測試 prompt 包含正確的 review 檔案路徑"""
        issue_name = "test-review-path"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\nReview done",
            TokenUsage(),
            [],
            None
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        # Check that execute was called
        agent_manager.execute.assert_called_once()

        # Get the prompt argument
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]  # Second positional argument is the prompt

        # Verify prompt contains the review file path instruction
        assert "review_001.md" in prompt
        assert ".cafe/issues/test-review-path/review/review_001.md" in prompt


class TestReviewPhaseBasics:
    """Test basic ReviewPhase functionality."""

    def test_init_review_phase(self, tmp_path: Path) -> None:
        """測試初始化 ReviewPhase"""
        # Create temporary spec and plan files with proper issue structure
        issue_name = "test-review"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.git_ops == git_ops
        assert phase.spec_file == str(spec_file)
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_target_commit(self, tmp_path: Path) -> None:
        """測試設定特定 commit"""
        # Create temporary spec and plan files with proper issue structure
        issue_name = "test-review-commit"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            target_commit="abc123",
        )

        assert phase.target_commit == "abc123"


class TestSingleIterationExecution:
    """Test single iteration execution."""

    def test_single_review_iteration_confirmed(self, tmp_path: Path) -> None:
        """測試單次 review 迭代通過"""
        # Create proper issue structure
        issue_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        # Review agent approves immediately
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            base_branch="main",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "CAFE_CONFIRMED"

    def test_single_review_iteration_needs_changes(self, tmp_path: Path) -> None:
        """測試單次 review 迭代需要修改"""
        # Create proper issue structure
        issue_name = "test-issue-changes"
        spec_dir = tmp_path / ".cafe" / "issues" / issue_name / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_NEEDS_CHANGES\n問題 1: 需要修正", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            base_branch="main",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "CAFE_NEEDS_CHANGES"

    def test_only_executes_once(self, tmp_path: Path) -> None:
        """測試只執行一次（不迴圈）"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_NEEDS_CHANGES\n需要修正", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

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
            plan_file="plan.md",
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
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Should get diff from main to HEAD
        git_ops.get_diff.assert_called_once_with(base="main", head="HEAD")

    def test_commit_specific_diff(self, tmp_path: Path) -> None:
        """測試特定 commit diff"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            target_commit="abc123",
        )

        phase.execute()

        # Should get diff for specific commit
        git_ops.get_diff.assert_called_once_with(base="abc123^", head="abc123")

    def test_diff_includes_in_review_prompt(self, tmp_path: Path) -> None:
        """測試 diff 包含在 review prompt 中"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff --git a/file.py"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

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
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            review_agent="Richard",
        )

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
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "程式碼變更" in prompt
        assert "狀態碼" in prompt
        assert "審查完成後請回傳狀態碼，不要做任何總結或額外說明" in prompt

    def test_review_prompt_no_iteration_count(self, tmp_path: Path) -> None:
        """測試 review prompt 不包含迭代次數"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(requirements_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

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
        import os
        original_dir = os.getcwd()
        try:
            # Create issue structure (.cafe/issues/myissue/...)
            issue_dir = tmp_path / ".cafe" / "issues" / "myissue"
            spec_dir = issue_dir / "spec"
            spec_dir.mkdir(parents=True)
            spec_file = spec_dir / "spec.md"
            spec_file.write_text("Requirements")

            plan_dir = issue_dir / "plan"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "plan.md"
            plan_file.write_text("Plan")

            os.chdir(tmp_path)

            agent_manager = MagicMock(spec=AgentManager)
            setup_agent_manager_mock(agent_manager)
            agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

            permission_handler = MagicMock(spec=PermissionHandler)

            git_ops = MagicMock(spec=GitOperations)
            git_ops.get_diff.return_value = "diff content"

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                workflow_mode=WorkflowMode.LOCAL,
            )

            phase.execute()

            # Should save review.md (under .cafe/issues/{issue_name}/review)
            review_dir = tmp_path / ".cafe" / "issues" / "myissue" / "review"
            review_file = review_dir / "review.md"
            assert review_file.exists(), f"review.md should be saved at {review_file}"
            assert "Code looks good!" in review_file.read_text()
        finally:
            os.chdir(original_dir)

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
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_dir = tmp_path / "myissue" / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        history_dir = review_dir / "history"
        history_dir.mkdir(exist_ok=True)

        with patch("cafe.phases.review_phase.Path") as MockPath:
            # Mock Path to use our temp directory
            mock_review_path = MagicMock()
            mock_review_path.parent.parent.name = "myissue"
            mock_review_path.exists.return_value = True
            mock_review_path.read_text.return_value = "Requirements"

            MockPath.return_value = mock_review_path

            phase.execute()

    def test_saves_agent_info_to_history(self, tmp_path: Path) -> None:
        """測試 history 包含 agent 資訊（cli, session_id, allowed_tools, denied_tools）"""
        import os
        from cafe.core.types import AgentCLI, AgentConfig

        # Change to tmp_path so .cafe is created there
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create issue structure
            issue_dir = Path(".cafe/issues/myissue")
            spec_dir = issue_dir / "spec"
            spec_dir.mkdir(parents=True)
            spec_file = spec_dir / "spec.md"
            spec_file.write_text("Requirements")

            plan_dir = issue_dir / "plan"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "plan.md"
            plan_file.write_text("Plan")

            # Mock agent executor with config
            mock_executor = MagicMock()
            mock_executor.config = AgentConfig(
                name="Richard",
                cli=AgentCLI.COPILOT,
                session_id="test-session-123"
            )

            agent_manager = MagicMock(spec=AgentManager)
            setup_agent_manager_mock(agent_manager)
            agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)
            agent_manager.get_agent.return_value = mock_executor

            permission_handler = MagicMock(spec=PermissionHandler)

            git_ops = MagicMock(spec=GitOperations)
            git_ops.get_diff.return_value = "diff content"

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                workflow_mode=WorkflowMode.LOCAL,
            )

            # Execute phase
            phase.execute()

            # Check that history file was created with agent info
            review_dir = issue_dir / "review"
            history_dir = review_dir / "history"
            history_file = history_dir / "iteration_001.json"

            assert history_file.exists(), "History file should exist"

            # Read and verify history content
            history_data = json.loads(history_file.read_text())

            # Verify new fields exist
            assert "cli" in history_data, "History should contain cli field"
            assert "session_id" in history_data, "History should contain session_id field"
            assert "allowed_tools" in history_data, "History should contain allowed_tools field"
            assert "denied_tools" in history_data, "History should contain denied_tools field"

            # Verify values
            assert history_data["cli"] == "copilot"
            assert history_data["session_id"] == "test-session-123"
            # ReviewPhase allows bash for git commands and write for review file
            assert "bash" in history_data["allowed_tools"]
            assert any("write(/.cafe/issues/myissue/review/review_" in tool for tool in history_data["allowed_tools"])
            assert history_data["denied_tools"] is None  # Default when not specified
        finally:
            os.chdir(original_dir)

    def test_saves_prompt_to_history(self, tmp_path: Path) -> None:
        """測試 history 包含發送給 agent 的 prompt"""
        import os
        from cafe.core.types import AgentCLI, AgentConfig

        # Change to tmp_path so .cafe is created there
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create issue structure
            issue_dir = Path(".cafe/issues/myissue")
            spec_dir = issue_dir / "spec"
            spec_dir.mkdir(parents=True)
            spec_file = spec_dir / "spec.md"
            spec_file.write_text("Requirements")

            plan_dir = issue_dir / "plan"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "plan.md"
            plan_file.write_text("Plan")

            # Mock agent executor with config
            mock_executor = MagicMock()
            mock_executor.config = AgentConfig(
                name="Richard",
                cli=AgentCLI.COPILOT,
                session_id="test-session-123"
            )

            agent_manager = MagicMock(spec=AgentManager)
            setup_agent_manager_mock(agent_manager)
            agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)
            agent_manager.get_agent.return_value = mock_executor

            permission_handler = MagicMock(spec=PermissionHandler)

            git_ops = MagicMock(spec=GitOperations)
            git_ops.get_diff.return_value = "diff content"

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                workflow_mode=WorkflowMode.LOCAL,
            )

            # Execute phase
            phase.execute()

            # Check that history file was created with prompt
            review_dir = issue_dir / "review"
            history_dir = review_dir / "history"
            history_file = history_dir / "iteration_001.json"

            assert history_file.exists(), "History file should exist"

            # Read and verify history content
            history_data = json.loads(history_file.read_text())

            # Verify prompt field exists
            assert "prompt" in history_data, "History should contain prompt field"

            # Verify prompt contains expected content (from _generate_review_prompt)
            prompt = history_data["prompt"]
            assert "你是資深軟體工程師 Richard" in prompt
            assert "程式碼審查" in prompt
            assert "diff content" in prompt
        finally:
            os.chdir(original_dir)


class TestIssueConfigReading:
    """Test reading issue config for base branch."""

    def test_reads_base_branch_from_issue_config(self, tmp_path: Path) -> None:
        """測試從 issue config 讀取 base branch"""
        # Create issue structure with config
        issue_name = "myissue"
        issues_dir = tmp_path / ".cafe" / "issues" / issue_name
        spec_dir = issues_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        # Create config file with base branch
        config_file = issues_dir / "config.json"
        config_data = {
            "base_branch": "develop",
            "feature_branch": "myissue"
        }
        config_file.write_text(json.dumps(config_data))

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nLGTM!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Don't specify base_branch - should read from config
            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
            plan_file="plan.md",
                workflow_mode=WorkflowMode.LOCAL,
            )

            result = phase.execute()

            # Should have called get_diff with base branch from config
            git_ops.get_diff.assert_called_once_with(base="develop", head="HEAD")
        finally:
            os.chdir(original_cwd)


class TestReviewPhaseStatus:
    """Test review phase status tracking."""

    def test_saves_status_json_on_execution(self, tmp_path: Path) -> None:
        """測試執行後儲存 status.json"""
        # Create issue structure following CAFE convention: .cafe/issues/<name>/spec/spec.md
        issue_name = "myissue"
        issues_dir = tmp_path / ".cafe" / "issues" / issue_name
        spec_dir = issues_dir / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Requirements")

        # Mock agent executor with config
        from cafe.core.types import AgentCLI, AgentConfig
        mock_executor = MagicMock()
        mock_executor.config = AgentConfig(
            name="Richard",
            cli=AgentCLI.COPILOT,
            session_id="test-session"
        )

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)
        agent_manager.get_agent.return_value = mock_executor

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        # Change working directory to tmp_path for test
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            phase = ReviewPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
            plan_file="plan.md",
                workflow_mode=WorkflowMode.LOCAL,
            )

            result = phase.execute()

            # Should save status.json
            review_dir = issues_dir / "review"
            status_file = review_dir / "status.json"
            assert status_file.exists(), f"status.json not found at {status_file}"

            import json
            status_data = json.loads(status_file.read_text())
            assert status_data["phase"] == "review"
            assert status_data["status"] == "completed"
            assert status_data["status_code"] == "CAFE_CONFIRMED"
            assert "iteration" in status_data
            assert "timestamp" in status_data
        finally:
            os.chdir(original_cwd)


class TestGitHubWorkflow:
    """Test GitHub workflow."""

    def test_github_workflow_uses_issue(self, tmp_path: Path) -> None:
        """測試 GitHub workflow 使用 issue"""
        # Create temporary spec and plan files with proper issue structure
        issue_name = "test-github-review"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Requirements")

        plan_file = spec_file.parent.parent / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\nCode looks good!", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_diff.return_value = "diff content"

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

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
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Git error" in result.message


class TestDevelopTimestampCheck:
    """測試檢查 develop 時間戳記的功能。"""

    def test_check_if_develop_is_newer_when_develop_is_newer(
        self, tmp_path: Path
    ) -> None:
        """測試當 develop 時間較新時返回 True"""
        from datetime import datetime, timedelta

        # 建立測試目錄結構
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        develop_dir = issue_dir / "develop"
        review_dir = issue_dir / "review"
        spec_dir = issue_dir / "spec"

        develop_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        # 建立 spec 檔案
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Test spec")

        # develop 的時間較新（現在）
        develop_time = datetime.now()
        develop_status = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": develop_time.isoformat(),
            "iteration": 1,
        }
        (develop_dir / "status.json").write_text(
            json.dumps(develop_status, ensure_ascii=False)
        )

        # review 的時間較舊（1 小時前）
        review_time = develop_time - timedelta(hours=1)
        review_status = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": review_time.isoformat(),
            "iteration": 1,
        }
        (review_dir / "status.json").write_text(
            json.dumps(review_status, ensure_ascii=False)
        )

        # 建立 ReviewPhase
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # 測試
        result = phase._check_if_develop_is_newer()
        assert result is True

    def test_check_if_develop_is_newer_when_review_is_newer(
        self, tmp_path: Path
    ) -> None:
        """測試當 review 時間較新時返回 False"""
        from datetime import datetime, timedelta

        # 建立測試目錄結構
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        develop_dir = issue_dir / "develop"
        review_dir = issue_dir / "review"
        spec_dir = issue_dir / "spec"

        develop_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        # 建立 spec 檔案
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Test spec")

        # develop 的時間較舊（1 小時前）
        develop_time = datetime.now() - timedelta(hours=1)
        develop_status = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": develop_time.isoformat(),
            "iteration": 1,
        }
        (develop_dir / "status.json").write_text(
            json.dumps(develop_status, ensure_ascii=False)
        )

        # review 的時間較新（現在）
        review_time = datetime.now()
        review_status = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": review_time.isoformat(),
            "iteration": 1,
        }
        (review_dir / "status.json").write_text(
            json.dumps(review_status, ensure_ascii=False)
        )

        # 建立 ReviewPhase
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # 測試
        result = phase._check_if_develop_is_newer()
        assert result is False

    def test_check_if_develop_is_newer_when_no_review_status(
        self, tmp_path: Path
    ) -> None:
        """測試當沒有 review status.json 時返回 True（第一次 review）"""
        from datetime import datetime

        # 建立測試目錄結構
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        develop_dir = issue_dir / "develop"
        spec_dir = issue_dir / "spec"

        develop_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        # 建立 spec 檔案
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Test spec")

        # 只有 develop status，沒有 review status
        develop_time = datetime.now()
        develop_status = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": develop_time.isoformat(),
            "iteration": 1,
        }
        (develop_dir / "status.json").write_text(
            json.dumps(develop_status, ensure_ascii=False)
        )

        # 建立 ReviewPhase
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # 測試
        result = phase._check_if_develop_is_newer()
        assert result is True

    def test_check_if_develop_is_newer_when_no_develop_status(
        self, tmp_path: Path
    ) -> None:
        """測試當沒有 develop status.json 時返回 False"""
        # 建立測試目錄結構
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"

        spec_dir.mkdir(parents=True)

        # 建立 spec 檔案
        spec_file = spec_dir / "spec.md"
        spec_file.write_text("Test spec")

        # 沒有 develop status

        # 建立 ReviewPhase
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # 測試
        result = phase._check_if_develop_is_newer()
        assert result is False
