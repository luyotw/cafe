"""Tests for DevelopPhase."""

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from aaf.phases.develop_phase import DevelopPhase
from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode, TokenUsage
from aaf.core.permission import PermissionHandler


class TestDevelopPhaseInit:
    """Test DevelopPhase initialization."""

    def test_init_with_all_required_params(self) -> None:
        """測試使用所有必要參數初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=".aaf/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.git_ops == git_ops
        assert phase.spec_file == ".aaf/issues/test/spec/spec.md"
        assert phase.plan_file == ".aaf/issues/test/plan/plan.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL
        assert phase.iteration == 0
        assert phase.issue_name == "test"
        assert phase.history_dir == Path(".aaf/issues/test/develop/history")
        assert phase.conversation_history == []
        assert phase.interactive is True
        assert phase.dev_agent == "David"

    def test_init_with_issue_name(self) -> None:
        """測試提供 issue_name 參數"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=".aaf/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="custom-name",
        )

        assert phase.issue_name == "custom-name"

    def test_init_derives_issue_name_from_spec_file(self) -> None:
        """測試從 spec_file 路徑推導 issue_name"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/myissue/spec/spec.md",
            plan_file=".aaf/issues/myissue/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.issue_name == "myissue"
        assert phase.history_dir == Path(".aaf/issues/myissue/develop/history")


class TestPlanCheck:
    """Test plan.md existence check."""

    def test_check_plan_exists_returns_true_when_file_exists(self, tmp_path: Path) -> None:
        """測試當 plan.md 存在時回傳 True"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("## Plan")

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=str(plan_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_plan_exists() is True

    def test_check_plan_exists_returns_false_when_file_missing(self) -> None:
        """測試當 plan.md 不存在時回傳 False"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file="/nonexistent/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_plan_exists() is False

    def test_execute_fails_when_plan_missing(self) -> None:
        """測試當 plan.md 不存在時 execute 失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
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
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan\n- [ ] Task 1")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("Development completed. AAF_CONFIRMED", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
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
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert phase.iteration == 1
        assert "CONFIRMED" in result.data["status_code"]

    def test_execute_saves_history_on_completion(self, tmp_path: Path) -> None:
        """測試完成時儲存 history"""
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
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
        )

        result = phase.execute()

        # History directory should be created
        history_dir = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history"
        assert history_dir.exists()

        # Check iteration file was created
        iteration_file = history_dir / "iteration_001.json"
        assert iteration_file.exists()

        # Verify history content
        with open(iteration_file) as f:
            history_data = json.load(f)
            assert history_data["iteration"] == 1
            assert history_data["status_code"] == "AAF_CONFIRMED"


class TestHistoryAndProgress:
    """Test history and progress saving."""

    def test_save_history_creates_json_file(self, tmp_path: Path) -> None:
        """測試 _save_history 建立 JSON 檔案"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )

        phase.iteration = 1
        phase._save_history(
            user_input="test input",
            response="test response",
            status_code=PhaseStatusCode.CONFIRMED,
        )

        history_file = phase.history_dir / "iteration_001.json"
        assert history_file.exists()

        with open(history_file) as f:
            data = json.load(f)
            assert data["iteration"] == 1
            assert data["user_input"] == "test input"
            assert data["response"] == "test response"
            assert data["status_code"] == "AAF_CONFIRMED"

    def test_save_progress_creates_status_json(self, tmp_path: Path) -> None:
        """測試 _save_progress 建立 status.json"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
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
            assert data["status_code"] == "AAF_CONFIRMED"
            assert data["iteration"] == 1

    def test_load_history_restores_iterations(self, tmp_path: Path) -> None:
        """測試 _load_history 恢復迭代記錄"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        history_dir = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history"
        history_dir.mkdir(parents=True)

        # Create mock history files
        for i in range(1, 4):
            history_file = history_dir / f"iteration_{i:03d}.json"
            with open(history_file, 'w') as f:
                json.dump({
                    "iteration": i,
                    "timestamp": datetime.now().isoformat(),
                    "user_input": f"input {i}",
                    "response": f"response {i}",
                    "status_code": "AAF_NEED_PERMISSION",
                }, f)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test",
        )

        assert phase.iteration == 3
        assert len(phase.conversation_history) == 3
        assert phase.conversation_history[0]["iteration"] == 1
        assert phase.conversation_history[2]["iteration"] == 3


class TestStatusCodeHandling:
    """Test status code handling."""

    def test_handle_confirmed_completes_phase(self, tmp_path: Path) -> None:
        """測試 CONFIRMED 狀態碼完成 phase"""
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("All done. AAF_CONFIRMED", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
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
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "AAF_CONFIRMED"

    def test_handle_need_permission_in_interactive_mode(self, tmp_path: Path) -> None:
        """測試在互動模式下處理 NEED_PERMISSION 狀態碼（單輪執行）"""
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        agent_manager = MagicMock(spec=AgentManager)
        # Agent returns NEED_PERMISSION
        agent_manager.execute.return_value = ("Need permission. AAF_NEED_PERMISSION", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
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
            interactive=True,
        )

        # Execute - should return IN_PROGRESS and save history
        result = phase.execute()

        # Should return IN_PROGRESS (not continue execution)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert agent_manager.execute.call_count == 1

        # Check that history was saved
        history_file = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history" / "iteration_001.json"
        assert history_file.exists()
        with open(history_file) as f:
            history_data = json.load(f)
            assert history_data["status_code"] == "AAF_NEED_PERMISSION"
            assert "user_response" not in history_data  # User hasn't responded yet

    def test_permission_denied_fails_phase(self, tmp_path: Path) -> None:
        """測試權限被拒絕時 phase 失敗（恢復場景）"""
        spec_file = tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec")

        plan_file = tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("## Plan")

        # Create a pending NEED_PERMISSION history from previous run
        history_dir = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history"
        history_dir.mkdir(parents=True)

        pending_permission = {
            "iteration": 1,
            "timestamp": "2025-11-03T10:00:00",
            "user_input": "",
            "response": "Need permission to write file",
            "status_code": "AAF_NEED_PERMISSION",
        }
        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(pending_permission, f)

        agent_manager = MagicMock(spec=AgentManager)
        # Won't be called but set it anyway
        agent_manager.execute.return_value = ("", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = True

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

        # Mock user input to deny permission
        with patch('builtins.input', return_value='r'):
            result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Permission denied" in result.message


class TestPromptGeneration:
    """Test prompt generation logic."""

    def test_first_iteration_prompt_contains_file_paths(self, tmp_path: Path) -> None:
        """測試第 1 輪 prompt 包含檔案路徑"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=".aaf/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        assert ".aaf/issues/test/spec/spec.md" in prompt
        assert ".aaf/issues/test/plan/plan.md" in prompt
        assert "CONFIRMED" in prompt
        assert "AAF_NEED_PERMISSION" in prompt

    def test_subsequent_iteration_prompt_refers_to_history(self, tmp_path: Path) -> None:
        """測試第 2+ 輪 prompt 參考歷史記錄"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=".aaf/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # Add history
        phase.conversation_history = [
            {"iteration": 1, "status_code": "AAF_NEED_PERMISSION"},
            {"iteration": 2, "status_code": "AAF_NEED_PERMISSION"},
        ]

        phase.iteration = 3
        prompt = phase._generate_prompt()

        assert "第 3 輪" in prompt
        assert "歷史記錄" in prompt


class TestBranchManagement:
    """Test branch name generation."""

    def test_local_mode_uses_issue_name_as_branch(self) -> None:
        """測試 local mode 使用 issue_name 作為分支名"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/my-feature/spec/spec.md",
            plan_file=".aaf/issues/my-feature/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        branch_name = phase._get_branch_name()
        assert branch_name == "my-feature"

    def test_github_mode_uses_issue_id(self) -> None:
        """測試 GitHub mode 使用 issue-{id} 作為分支名"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test/spec/spec.md",
            plan_file=".aaf/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        branch_name = phase._get_branch_name()
        assert branch_name == "issue-123"

    def test_saves_base_branch_on_execution(self, tmp_path: Path) -> None:
        """測試執行時儲存 base branch 資訊"""
        # Setup files
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        plan_file = tmp_path / ".aaf" / "issues" / "test-issue" / "plan" / "plan.md"
        spec_file.parent.mkdir(parents=True)
        plan_file.parent.mkdir(parents=True)
        spec_file.write_text("Test spec")
        plan_file.write_text("Test plan")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n開發完成", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.get_current_branch.return_value = "main"
        git_ops.branch_exists.return_value = False
        git_ops.get_current_branch.return_value = "main"

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

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
            config_file = tmp_path / ".aaf" / "issues" / "test-issue" / "config.json"
            assert config_file.exists()

            config_data = json.loads(config_file.read_text())
            assert config_data["base_branch"] == "main"
            assert config_data["feature_branch"] == "test-issue"
        finally:
            os.chdir(original_cwd)


class TestReviewFeedbackDetection:
    """Test review feedback detection methods."""

    def test_get_review_file_path(self) -> None:
        """測試路徑生成正確性"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".aaf/issues/test-issue/spec/spec.md",
            plan_file=".aaf/issues/test-issue/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        review_path = phase._get_review_file_path()
        assert review_path == Path(".aaf/issues/test-issue/review/review.md")

    def test_check_review_feedback_exists_true(self, tmp_path: Path) -> None:
        """測試檔案存在的情況"""
        # Create review.md file
        review_file = tmp_path / ".aaf" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback\n\nPlease fix the bug.")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_review_feedback_exists() is True

    def test_check_review_feedback_exists_false(self, tmp_path: Path) -> None:
        """測試檔案不存在的情況"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase._check_review_feedback_exists() is False


class TestPromptGenerationWithReviewFeedback:
    """Test prompt generation with review feedback."""

    def test_generate_prompt_with_review_feedback(self, tmp_path: Path) -> None:
        """測試有 review feedback 時的 prompt"""
        # Create review.md file
        review_file = tmp_path / ".aaf" / "issues" / "test" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback\n\nNeed to fix commit messages.")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
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

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "test" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "test" / "plan" / "plan.md"),
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
        review_file = tmp_path / ".aaf" / "issues" / "myissue" / "review" / "review.md"
        review_file.parent.mkdir(parents=True)
        review_file.write_text("# Review Feedback")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(tmp_path / ".aaf" / "issues" / "myissue" / "spec" / "spec.md"),
            plan_file=str(tmp_path / ".aaf" / "issues" / "myissue" / "plan" / "plan.md"),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.iteration = 1
        prompt = phase._generate_prompt()

        expected_path = str(tmp_path / ".aaf" / "issues" / "myissue" / "review" / "review.md")
        assert expected_path in prompt


class TestDevelopPhaseReviewFeedback:
    """測試 develop phase 處理 review feedback 的情況"""

    def test_execute_continues_when_completed_but_review_feedback_exists(self, tmp_path) -> None:
        """測試當 develop 已完成但有 review feedback 時，應該繼續執行而非直接返回"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        # Create test files
        issue_dir = tmp_path / ".aaf" / "issues" / "test-issue"
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
        review_file.write_text("AAF_NEEDS_CHANGES\n\nPlease fix the bug in function X.")
        
        # Create review status.json with NEEDS_CHANGES
        review_status_file = review_dir / "status.json"
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "AAF_NEEDS_CHANGES",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Create iteration history file
        history_dir = develop_dir / "history"
        history_dir.mkdir(parents=True)
        history_file = history_dir / "iteration_001.json"
        history_data = {
            "iteration": 1,
            "user_input": "",
            "prompt": "Test prompt",
            "response": "AAF_CONFIRMED\n開發完成",
            "status_code": "AAF_CONFIRMED",
            "timestamp": datetime.now().isoformat()
        }
        history_file.write_text(json.dumps(history_data, indent=2))

        # Create status.json indicating COMPLETED
        status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "AAF_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        status_file.write_text(json.dumps(status_data, indent=2))

        # Mock agent response
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n修正完成", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

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

        # Should NOT return early with "already completed"
        # Should execute agent to handle review feedback
        assert agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED, f"Expected COMPLETED but got {result.status}: {result.message}"
        # Should have incremented iteration
        assert phase.iteration == 2

    def test_execute_returns_early_when_completed_and_no_review_feedback(self, tmp_path) -> None:
        """測試當 develop 已完成且沒有 review feedback 時，應該直接返回"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        # Create test files (without review.md)
        issue_dir = tmp_path / ".aaf" / "issues" / "test-issue"
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
            "status_code": "AAF_CONFIRMED",
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

    def test_execute_continues_when_review_status_is_needs_changes(self, tmp_path) -> None:
        """測試當 review status 為 NEEDS_CHANGES 時，即使 develop 已完成也應該繼續執行"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        # Create test files
        issue_dir = tmp_path / ".aaf" / "issues" / "test-issue"
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
        review_file.write_text("AAF_NEEDS_CHANGES\n\nPlease fix commit messages.")

        # Create review status.json with NEEDS_CHANGES
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "AAF_NEEDS_CHANGES",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Create develop status.json indicating COMPLETED
        develop_status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "AAF_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        develop_status_file.write_text(json.dumps(develop_status_data, indent=2))

        # Mock agent response
        agent_manager.execute.return_value = ("AAF_CONFIRMED\n修正完成", TokenUsage())
        agent_manager.get_total_token_usage.return_value = TokenUsage()

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

        # Should NOT return early - should execute agent to handle review feedback
        assert agent_manager.execute.called
        assert result.status == PhaseStatus.COMPLETED
        # Iteration increments from 0 to 1 (no history file, only status.json)
        assert phase.iteration == 1

    def test_execute_returns_early_when_review_status_is_confirmed(self, tmp_path) -> None:
        """測試當 review status 為 CONFIRMED/APPROVED 時，develop 已完成應該直接返回"""
        # Setup
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)

        # Create test files
        issue_dir = tmp_path / ".aaf" / "issues" / "test-issue"
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
        review_file.write_text("AAF_CONFIRMED\n\nLooks good!")

        # Create review status.json with CONFIRMED
        review_status_data = {
            "phase": "review",
            "status": "completed",
            "status_code": "AAF_CONFIRMED",
            "iteration": 1,
            "timestamp": datetime.now().isoformat()
        }
        review_status_file.write_text(json.dumps(review_status_data, indent=2))

        # Create develop status.json indicating COMPLETED
        develop_status_data = {
            "phase": "develop",
            "status": "completed",
            "status_code": "AAF_CONFIRMED",
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
