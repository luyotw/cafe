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
        agent_manager.execute.return_value = "Development completed. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

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
        agent_manager.execute.return_value = "CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

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
            assert history_data["status_code"] == "CONFIRMED"


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
            assert data["status_code"] == "CONFIRMED"

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
            assert data["status_code"] == "CONFIRMED"
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
                    "status_code": "NEED_PERMISSION",
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
        agent_manager.execute.return_value = "All done. CONFIRMED"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False

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
        assert result.data["status_code"] == "CONFIRMED"

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
        agent_manager.execute.return_value = "Need permission. NEED_PERMISSION"
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        git_ops = MagicMock(spec=GitOperations)
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
        result = phase.execute()

        # Should return IN_PROGRESS (not continue execution)
        assert result.status == PhaseStatus.IN_PROGRESS
        assert agent_manager.execute.call_count == 1

        # Check that history was saved
        history_file = tmp_path / ".aaf" / "issues" / "test" / "develop" / "history" / "iteration_001.json"
        assert history_file.exists()
        with open(history_file) as f:
            history_data = json.load(f)
            assert history_data["status_code"] == "NEED_PERMISSION"
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
            "status_code": "NEED_PERMISSION",
        }
        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(pending_permission, f)

        agent_manager = MagicMock(spec=AgentManager)
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
        assert "NEED_PERMISSION" in prompt

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
            {"iteration": 1, "status_code": "NEED_PERMISSION"},
            {"iteration": 2, "status_code": "NEED_PERMISSION"},
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
