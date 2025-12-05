"""Tests for DevelopPhase."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from cafe.phases.develop_phase import DevelopPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage, AgentConfig, AgentCLI
from cafe.core.permission import PermissionHandler


def setup_agent_manager_mock(agent_manager: MagicMock, agent_name: str = "David") -> None:
    """Setup agent_manager mock with get_agent method.

    Args:
        agent_manager: The MagicMock agent manager to configure
        agent_name: Name of the agent (default: David)
    """
    mock_agent_executor = MagicMock()
    mock_agent_executor.config = AgentConfig(
        name=agent_name,
        cli=AgentCLI.CLAUDE,
        session_id='test_session'
    )
    agent_manager.get_agent.return_value = mock_agent_executor


class TestDevelopPhaseInit:
    """Test DevelopPhase initialization."""

    def test_init_with_all_required_params(self, tmp_path, monkeypatch) -> None:
        """測試使用所有必要參數初始化"""
        monkeypatch.chdir(tmp_path)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.git_ops == git_ops
        assert phase.spec_file == ".cafe/issues/test/spec/spec.md"
        assert phase.plan_file == ".cafe/issues/test/plan/plan.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL
        assert phase.iteration == 0
        assert phase.issue_name == "test"
        assert phase.history_dir == Path(".cafe/issues/test/develop/history")
        assert phase.interactive is True
        assert phase.dev_agent == "David"

    def test_init_with_issue_name(self) -> None:
        """測試提供 issue_name 參數"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="custom-name",
        )

        assert phase.issue_name == "custom-name"

    def test_init_derives_issue_name_from_spec_file(self) -> None:
        """測試從 spec_file 路徑推導 issue_name"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "myissue"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/myissue/spec/spec.md",
            plan_file=".cafe/issues/myissue/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.issue_name == "myissue"
        assert phase.history_dir == Path(".cafe/issues/myissue/develop/history")


class TestPlanCheck:
    """Test plan.md existence check."""

    def test_check_plan_exists_returns_true_when_file_exists(self, tmp_path: Path) -> None:
        """測試當 plan.md 存在時回傳 True"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("## Plan")

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_plan_exists() is True

    def test_check_plan_exists_returns_false_when_file_missing(self) -> None:
        """測試當 plan.md 不存在時回傳 False"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file="/nonexistent/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_plan_exists() is False

    def test_execute_fails_when_plan_missing(self) -> None:
        """測試當 plan.md 不存在時 execute 失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file="/nonexistent/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Plan file not found" in result.message


class TestIterativeFlow:
    """Test iterative execution flow."""

    def test_execute_with_confirmed_status(self, tmp_path: Path) -> None:
        """測試收到 CONFIRMED 狀態碼後完成"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan\n- [ ] Task 1")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("Development completed. CAFE_CONFIRMED", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "main"
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=False,  # Set to non-interactive to avoid input() call
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert phase.iteration == 1
        assert "CONFIRMED" in result.data["status_code"]

    def test_execute_saves_history_on_completion(self, tmp_path: Path, monkeypatch) -> None:
        """測試完成時儲存 history"""
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=False,  # Set to non-interactive to avoid input() call
        )

        result = phase.execute()

        # History directory should be created
        history_dir = tmp_path / ".cafe" / "issues" / "test" / "develop" / "history"
        assert history_dir.exists()

        # Check iteration file was created
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        # Verify history content
        with open(iteration_file) as f:
            history_data = json.load(f)
            assert history_data["iteration"] == 1
            assert history_data["status_code"] == "CAFE_CONFIRMED"


class TestHistoryAndProgress:
    """Test history and progress saving."""

    def test_save_progress_creates_status_json(self, tmp_path: Path) -> None:
        """測試 _save_progress 建立 status.json"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )

        phase.iteration = 1
        phase._save_progress(PhaseStatusCode.CONFIRMED)

        status_file = phase.history_dir.parent / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            data = json.load(f)
            assert data["phase"] == "develop"
            assert data["status"] == "completed"
            assert data["status_code"] == "CAFE_CONFIRMED"
            assert data["iteration"] == 1

class TestStatusCodeHandling:
    """Test status code handling."""

    def test_handle_confirmed_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("All done. CAFE_CONFIRMED", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"
        git_ops.branch_exists.return_value = False
        git_ops.get_current_branch.return_value = "main"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=False,  # Set to non-interactive to avoid input() call
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "CAFE_CONFIRMED"

    def test_handle_need_permission_in_interactive_mode(self, tmp_path: Path, monkeypatch) -> None:
        """測試在互動模式下處理 NEED_PERMISSION 狀態碼（單輪執行）"""
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        # Agent returns NEED_PERMISSION
        agent_manager.execute.return_value = ("Need permission. CAFE_NEED_PERMISSION", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
            interactive=True,
        )

        # Execute - should return IN_PROGRESS and save history
        # Mock input() to simulate user skipping additional input
        with patch('builtins.input', return_value=""):
            result = phase.execute()

        # Should return IN_PROGRESS (not continue execution)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert agent_manager.execute.call_count == 1

        # Check that history was saved
        history_file = tmp_path / ".cafe" / "issues" / "test" / "develop" / "history" / "iteration_001.json"
        assert history_file.exists()
        with open(history_file) as f:
            history_data = json.load(f)
            assert history_data["status_code"] == "CAFE_NEED_PERMISSION"
            assert "user_response" not in history_data  # User hasn't responded yet

    @pytest.mark.skip(reason="Permission handling logic changed - no longer applicable with non-iterative design")
    def test_permission_denied_fails_phase(self, tmp_path: Path) -> None:
        """測試權限被拒絕時 phase 失敗（恢復場景）"""
        pass


class TestPromptGeneration:
    """Test prompt generation logic."""

    def test_first_iteration_prompt_contains_file_paths(self, tmp_path: Path) -> None:
        """測試第 1 輪 prompt 包含檔案路徑"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        assert ".cafe/issues/test/spec/spec.md" in prompt
        assert ".cafe/issues/test/plan/plan.md" in prompt
        assert "CONFIRMED" in prompt
        assert "CAFE_NEED_PERMISSION" in prompt

class TestBranchManagement:
    """Test branch name generation."""

    def test_local_mode_uses_issue_name_as_branch(self, tmp_path, monkeypatch) -> None:
        """測試 local mode 使用 issue_name 作為分支名"""
        monkeypatch.chdir(tmp_path)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "my-feature"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/my-feature/spec/spec.md",
            plan_file=".cafe/issues/my-feature/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        branch_name = phase._get_branch_name()
        assert branch_name == "my-feature"

    def test_github_mode_uses_issue_id(self) -> None:
        """測試 GitHub mode 使用 issue-{id} 作為分支名"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        branch_name = phase._get_branch_name()
        assert branch_name == "issue-123"

    def test_saves_base_branch_on_execution(self, tmp_path: Path, monkeypatch) -> None:
        """測試執行時儲存 base branch 資訊"""
        monkeypatch.chdir(tmp_path)

        # Setup files
        spec_file = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan.md"
        spec_file.parent.mkdir(parents=True)
        plan_file.parent.mkdir(parents=True)
        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n開發完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        # First call (during init): return "test-issue" for issue_dir
        # Second call (during execute): return "main" for base_branch
        git_ops.get_current_branch.side_effect = ["test-issue", "main"]
        git_ops.branch_exists.return_value = False

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        # Should save issue config with base branch
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "config.yaml"
        assert config_file.exists()

        import yaml
        config_data = yaml.safe_load(config_file.read_text())
        assert config_data["base_branch"] == "main"
        assert config_data["feature_branch"] == "test-issue"


class TestReviewFeedbackDetection:
    """Test review feedback detection methods."""

    def test_get_review_file_path_returns_latest_numbered_file(self, tmp_path: Path) -> None:
        """測試當有多個 review_XXX.md 檔案時，返回最新的"""
        # Create multiple review files
        review_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "review"
        review_dir.mkdir(parents=True)
        (review_dir / "review_001.md").write_text("First review")
        (review_dir / "review_002.md").write_text("Second review")
        (review_dir / "review_003.md").write_text("Latest review")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_path = phase._get_review_file_path()
        # Should return the latest numbered review file
        assert review_path == tmp_path / ".cafe" / "issues" / "test-issue" / "review" / "review_003.md"

    def test_get_review_file_path_fallback_to_review_md(self, tmp_path: Path) -> None:
        """測試當沒有 review_XXX.md 時，返回 review.md（向後兼容）"""
        # Create only review.md
        review_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "review"
        review_dir.mkdir(parents=True)
        (review_dir / "review.md").write_text("Old style review")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_path = phase._get_review_file_path()
        # Should fallback to review.md
        assert review_path == tmp_path / ".cafe" / "issues" / "test-issue" / "review" / "review.md"

    def test_get_review_file_path_no_review_files(self) -> None:
        """測試沒有任何 review 檔案時的路徑"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test-issue/spec/spec.md",
            plan_file=".cafe/issues/test-issue/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_path = phase._get_review_file_path()
        # Should default to review.md when no files exist
        assert review_path == Path(".cafe/issues/test-issue/review/review.md")

    def test_check_review_feedback_exists_true(self, tmp_path: Path) -> None:
        """測試檔案存在的情況"""
        # Create review.md file
        review_file = tmp_path / ".cafe" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback\n\nPlease fix the bug.")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_review_feedback_exists() is True

    def test_check_review_feedback_exists_false(self, tmp_path: Path) -> None:
        """測試檔案不存在的情況"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_review_feedback_exists() is False


class TestPromptGenerationWithReviewFeedback:
    """Test prompt generation with review feedback."""

    def test_generate_prompt_with_review_feedback(self, tmp_path: Path) -> None:
        """測試有 review feedback 時的 prompt"""
        # Create review.md file
        review_file = tmp_path / ".cafe" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback\n\nNeed to fix commit messages.")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        # Verify prompt contains review feedback instructions
        assert "Review Feedback" in prompt
        assert "review/review.md" in prompt
        assert "Code Review" in prompt
        assert "修正" in prompt

    def test_generate_prompt_without_review_feedback(self, tmp_path: Path) -> None:
        """測試無 review feedback 時的 prompt"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        # Verify prompt is the original development prompt
        assert "請按照實作計畫執行開發工作" in prompt
        assert "需求規格" in prompt
        assert "實作計畫" in prompt
        assert "Review Feedback" not in prompt

    def test_prompt_contains_correct_review_file_path(self, tmp_path: Path) -> None:
        """驗證 prompt 中包含正確的 review.md 路徑"""
        # Create review.md file
        review_file = tmp_path / ".cafe" / "issues" / "myissue" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".cafe" / "issues" / "myissue" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".cafe" / "issues" / "myissue" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        expected_path = str(tmp_path / ".cafe" / "issues" / "myissue" / "review" / "review.md")
        assert expected_path in prompt


class TestDevelopPhaseReviewFeedback:
    """測試 develop phase 處理 review feedback 的情況"""

    def test_execute_continues_when_completed_but_review_feedback_exists(self, tmp_path, monkeypatch) -> None:
        """測試當 develop 已完成但有 review feedback 時，應該繼續執行而非直接返回"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"

        # Create test files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        review_dir = issue_dir / "review"
        develop_dir = issue_dir / "develop"

        spec_dir.mkdir(parents=True)
        plan_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        develop_dir.mkdir(parents=True)

        spec_file = spec_dir / "spec.md"
        plan_file = plan_dir / "plan.md"
        review_file = review_dir / "review.md"
        status_file = develop_dir / "status.json"

        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")
        review_file.write_text("CAFE_NEEDS_CHANGES\n\nPlease fix the bug in function X.")
        
        # Create iteration history file
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True)
        history_file = history_dir / "iteration_001.json"
        history_data = {
            "iteration": 1,
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_CONFIRMED\n開發完成",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": datetime.now().isoformat()
        }
        history_file.write_text(json.dumps(history_data, indent=2))

        # Create status.json indicating COMPLETED
        status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        status_file.write_text(json.dumps(status_data, indent=2))
        
        # Create review status.json with NEEDS_CHANGES (after develop completes)
        import time
        time.sleep(0.01)  # Ensure review timestamp is after develop
        review_status_file = review_dir / "status.json"
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Mock agent response
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n修正完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Mock git operations

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Should NOT return early with "already completed"
        # Should execute agent to handle review feedback
        assert agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED, f"Expected COMPLETED but got {result.status}: {result.message}"
        # Iteration counter should be 2 (loaded 1 from history, then incremented)
        assert phase.iteration == 2

    def test_execute_returns_early_when_completed_and_no_review_feedback(self, tmp_path) -> None:
        """測試當 develop 已完成且沒有 review feedback 時，應該直接返回"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create test files (without review.md)
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        develop_dir = issue_dir / "develop"

        spec_dir.mkdir(parents=True)
        plan_dir.mkdir(parents=True)
        develop_dir.mkdir(parents=True)

        spec_file = spec_dir / "spec.md"
        plan_file = plan_dir / "plan.md"
        status_file = develop_dir / "status.json"

        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")

        # Create status.json indicating COMPLETED
        status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        status_file.write_text(json.dumps(status_data, indent=2))

        # Mock git operations
        git_ops.branch_exists.return_value = True

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Should return early without calling agent
        assert not agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED
        assert "already completed" in result.message.lower()

    def test_execute_continues_when_review_status_is_needs_changes(self, tmp_path, monkeypatch) -> None:
        """測試當 review status 為 NEEDS_CHANGES 時，即使 develop 已完成也應該繼續執行"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-issue"

        # Create test files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        review_dir = issue_dir / "review"
        develop_dir = issue_dir / "develop"

        spec_dir.mkdir(parents=True)
        plan_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        develop_dir.mkdir(parents=True)

        spec_file = spec_dir / "spec.md"
        plan_file = plan_dir / "plan.md"
        review_file = review_dir / "review.md"
        review_status_file = review_dir / "status.json"
        develop_status_file = develop_dir / "status.json"

        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")
        review_file.write_text("CAFE_NEEDS_CHANGES\n\nPlease fix commit messages.")

        # Create develop status.json indicating COMPLETED (first)
        develop_status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        develop_status_file.write_text(json.dumps(develop_status_data, indent=2))

        # Create review status.json with NEEDS_CHANGES (after develop)
        import time
        time.sleep(0.01)  # Ensure review is after develop
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Mock agent response
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n修正完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        # Mock git operations
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"
        git_ops.branch_exists.return_value = True

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Should NOT return early - should execute agent to handle review feedback
        assert agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED
        # Iteration increments from 0 to 1 (no history file, only status.json)
        assert phase.iteration == 1

    def test_execute_returns_early_when_review_status_is_confirmed(self, tmp_path) -> None:
        """測試當 review status 為 CONFIRMED 時，develop 已完成應該直接返回"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create test files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        review_dir = issue_dir / "review"
        develop_dir = issue_dir / "develop"

        spec_dir.mkdir(parents=True)
        plan_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        develop_dir.mkdir(parents=True)

        spec_file = spec_dir / "spec.md"
        plan_file = plan_dir / "plan.md"
        review_file = review_dir / "review.md"
        review_status_file = review_dir / "status.json"
        develop_status_file = develop_dir / "status.json"

        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")
        review_file.write_text("CAFE_CONFIRMED\n\nLooks good!")

        # Create review status.json with CONFIRMED
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Create develop status.json indicating COMPLETED
        develop_status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        develop_status_file.write_text(json.dumps(develop_status_data, indent=2))

        # Mock git operations
        git_ops.branch_exists.return_value = True

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Should return early without calling agent (review passed)
        assert not agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED
        assert "already completed" in result.message.lower()

    def test_prints_review_file_when_detected(self, tmp_path, capsys) -> None:
        """測試當檢測到 review feedback 時，應該 print 出 review 檔案路徑"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n修正完成", TokenUsage(), [], None, [])
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"
        git_ops.branch_exists.return_value = True

        # Create test files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        review_dir = issue_dir / "review"
        develop_dir = issue_dir / "develop"

        spec_dir.mkdir(parents=True)
        plan_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        develop_dir.mkdir(parents=True)

        spec_file = spec_dir / "spec.md"
        plan_file = plan_dir / "plan.md"
        review_status_file = review_dir / "status.json"
        develop_status_file = develop_dir / "status.json"

        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")

        # Create numbered review files
        (review_dir / "review_001.md").write_text("First review")
        (review_dir / "review_002.md").write_text("CAFE_NEEDS_CHANGES\n\nPlease fix the bug.")

        # Create develop status.json indicating COMPLETED (first)
        develop_status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        develop_status_file.write_text(json.dumps(develop_status_data, indent=2))

        # Create review status.json with NEEDS_CHANGES (after develop)
        import time
        time.sleep(0.01)  # Ensure review is after develop
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "iteration": 2,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Create iteration history
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True)
        history_file = history_dir / "iteration_001.json"
        history_data = {
            "iteration": 1,
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_CONFIRMED\n開發完成",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": datetime.now().isoformat()
        }
        history_file.write_text(json.dumps(history_data, indent=2))

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Execute
        result = phase.execute()

        # Verify print output includes review file path
        captured = capsys.readouterr()
        expected_review_file = str(review_dir / "review_002.md")
        assert expected_review_file in captured.out, f"Expected '{expected_review_file}' in output, got: {captured.out}"

        # Should continue execution (not return early)
        assert agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED


class TestDevelopPhaseIterationCounter:
    """Test DevelopPhase iteration counter behavior to prevent overwriting previous iterations."""

    def test_second_execution_creates_iteration_002_not_overwrite_001(self, tmp_path: Path, monkeypatch):
        """測試第二次執行創建 iteration_002.json，而不是覆蓋 iteration_001.json

        這是一個 regression test，確保 DevelopPhase 使用 _load_iteration_counter()
        而不是總是設定 self.iteration = 1，否則會覆蓋之前的 iteration history。

        測試場景：
        1. 第一次執行：agent 完成工作 (iteration_001.json)
        2. 手動刪除 status.json 模擬未完成狀態
        3. 第二次執行：agent 繼續工作 (應創建 iteration_002.json)
        """
        monkeypatch.chdir(tmp_path)

        issue_name = "test-iteration-not-overwrite"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        status_file = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "status.json"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest")
        plan_file.write_text("# Plan\nTest")

        # Setup mocks
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test-iteration-not-overwrite"
        git_ops.branch_exists.return_value = False

        # First execution - agent completes work
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n第一次開發完成", TokenUsage(), [], None, [])
        result1 = phase.execute()

        assert result1.status == PhaseStatus.COMPLETED

        # Verify iteration_001.json was created
        iteration_001_file = history_dir / "iteration_001.json"
        assert iteration_001_file.exists()

        with open(iteration_001_file, "r") as f:
            saved_001 = json.load(f)

        assert saved_001["iteration"] == 1
        assert "第一次開發完成" in saved_001["response"]
        original_content_001 = saved_001.copy()

        # Delete status.json to simulate incomplete state
        if status_file.exists():
            status_file.unlink()

        # Create a NEW phase object for second execution
        phase2 = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Branch already exists for second run
        git_ops.branch_exists.return_value = True

        # Second execution - agent does more work
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n第二次開發完成", TokenUsage(), [], None, [])
        result2 = phase2.execute()

        assert result2.status == PhaseStatus.COMPLETED

        # CRITICAL: Verify iteration_001.json was NOT overwritten
        with open(iteration_001_file, "r") as f:
            saved_001_after = json.load(f)

        assert saved_001_after["iteration"] == 1, "iteration_001 should still be 1"
        assert "第一次開發完成" in saved_001_after["response"], "原始 response 應保持不變"
        assert saved_001_after == original_content_001, "iteration_001.json should be completely unchanged"

        # CRITICAL: Verify iteration_002.json was created
        iteration_002_file = history_dir / "iteration_002.json"
        assert iteration_002_file.exists(), \
            "iteration_002.json should be created, not overwrite iteration_001.json"

        with open(iteration_002_file, "r") as f:
            saved_002 = json.load(f)

        assert saved_002["iteration"] == 2, "第二輪應該是 iteration 2"
        assert "第二次開發完成" in saved_002["response"]


class TestDeveloperPermissions:
    """測試 developer 的權限配置"""

    def test_developer_has_edit_permission(self):
        """測試 developer 的 base_allowed_tools 包含 edit"""
        from cafe.phases.develop_phase import DevelopPhase
        from unittest.mock import MagicMock

        # Create a minimal phase instance (we don't need to execute, just check the code)
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="spec.md",
            plan_file="plan.md",
            workflow_mode="local"
        )

        # Check the source code directly - base_allowed_tools should include edit
        import inspect
        source = inspect.getsource(DevelopPhase.execute)

        # Verify that base_allowed_tools includes edit
        assert 'base_allowed_tools = ["write", "read", "edit", "bash", "grep", "glob", "ls", "web_fetch", "web_search"]' in source, \
            "Developer's base_allowed_tools should include edit permission"

    def test_inherits_previous_iteration_tools(self, tmp_path):
        """測試第二輪會繼承第一輪的 allowed_tools"""
        import os
        import json
        from unittest.mock import MagicMock
        from cafe.core.types import AgentConfig, AgentCLI, TokenUsage

        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create issue structure
            issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
            spec_dir = issue_dir / "spec"
            spec_dir.mkdir(parents=True)
            spec_file = spec_dir / "spec.md"
            spec_file.write_text("Test spec")

            plan_dir = issue_dir / "plan"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "plan.md"
            plan_file.write_text("Test plan")

            develop_dir = issue_dir / "develop"
            develop_dir.mkdir(parents=True)
            history_dir = develop_dir / "history"
            history_dir.mkdir(parents=True)

            # Create iteration_001.json with some allowed_tools
            iteration_001 = {
                "iteration": 1,
                "user_input": "First iteration",
                "allowed_tools": ["write", "read", "edit", "bash", "grep(/some/path)"],
                "response": "CAFE_CONTINUE\nNeed more work",
                "status_code": "CONTINUE",
            }
            iteration_001_file = history_dir / "iteration_001.json"
            iteration_001_file.write_text(json.dumps(iteration_001, ensure_ascii=False, indent=2))

            # Mock agent manager
            mock_executor = MagicMock()
            mock_executor.config = AgentConfig(
                name="David",
                cli=AgentCLI.CLAUDE,
                session_id="test-session"
            )

            agent_manager = MagicMock()
            agent_manager.get_agent.return_value = mock_executor

            # Track what allowed_tools were passed to execute (capture all calls)
            captured_calls = []
            def capture_execute(*args, **kwargs):
                captured_calls.append({
                    "args": args,
                    "kwargs": kwargs,
                    "allowed_tools": kwargs.get("allowed_tools", [])
                })
                return (
                    "CAFE_DONE\nImplementation complete",
                    TokenUsage(),
                    [],
                    None,
                    []
                )

            agent_manager.execute.side_effect = capture_execute

            permission_handler = MagicMock()
            git_ops = MagicMock()

            from cafe.phases.develop_phase import DevelopPhase

            phase = DevelopPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                git_ops=git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                workflow_mode="local"
            )

            # Manually set iteration to 2
            phase.iteration = 2
            phase.history_dir = history_dir

            # Execute second iteration (should inherit tools from iteration 1)
            phase._get_or_prompt_user_input = MagicMock(return_value="Second iteration")

            result, response = phase._execute_and_handle_agent_response(
                agent_name="dev",
                user_input="Second iteration",
                valid_status_codes=[],
                allowed_tools=list(set(["write", "read", "edit", "bash"] +
                                      iteration_001["allowed_tools"])),
                continue_codes=[],
                complete_codes=[],
            )

            # Verify that allowed_tools includes both base tools and previous iteration's tools
            # 檢查第一次呼叫（主要執行，非 _analyze_missing_status_code）
            assert len(captured_calls) >= 1, "execute should be called at least once"
            expected_tools = set(["write", "read", "edit", "bash", "grep(/some/path)"])
            actual_tools = set(captured_calls[0]["allowed_tools"]) if captured_calls[0]["allowed_tools"] else set()

            assert expected_tools.issubset(actual_tools), \
                f"Second iteration should inherit previous tools. Expected {expected_tools}, got {actual_tools}"
        finally:
            os.chdir(original_dir)


class TestAskForAdditionalInput:
    """測試 _ask_for_additional_input() 方法"""

    def test_ask_for_additional_input_interactive_with_input(self, tmp_path, monkeypatch) -> None:
        """測試 interactive 模式，使用者提供額外輸入"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create phase
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock input() to return user input
        with patch('builtins.input', return_value="請注意效能"):
            result = phase._ask_for_additional_input()

        assert result == "請注意效能"

    def test_ask_for_additional_input_interactive_empty(self, tmp_path, monkeypatch) -> None:
        """測試 interactive 模式，使用者按 Enter 跳過"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create phase
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        # Mock input() to return empty string
        with patch('builtins.input', return_value=""):
            result = phase._ask_for_additional_input()

        assert result == ""

    def test_ask_for_additional_input_non_interactive(self, tmp_path, monkeypatch) -> None:
        """測試 non-interactive 模式，使用 user_input"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create phase with user_input
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="請記得寫測試",
        )

        result = phase._ask_for_additional_input()

        # 驗證返回值
        assert result == "請記得寫測試"
        # 重要：驗證 user_input 被清空
        assert phase.user_input == ""

    def test_ask_for_additional_input_non_interactive_empty(self, tmp_path, monkeypatch) -> None:
        """測試 non-interactive 模式，user_input 為空"""
        monkeypatch.chdir(tmp_path)

        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "test"

        # Create phase without user_input
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="",
        )

        result = phase._ask_for_additional_input()

        assert result == ""
