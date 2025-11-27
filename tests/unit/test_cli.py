"""Tests for CLI."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, Mock, patch

from cafe.ui.cli import app, _setup_agents, _build_workflow
from cafe.core.git import GitOperations
from cafe.core.types import AgentCLI, PhaseResult, PhaseStatus, WorkflowMode
from cafe.utils.config import ConfigManager


runner = CliRunner()


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


class TestSetupAgents:
    """Test agent setup functionality."""

    def test_setup_agents_with_default_config(self, tmp_path: Path) -> None:
        """測試使用預設設定建立 agents"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        agent_manager = _setup_agents(config_manager)

        # 驗證三個 agents 都已註冊
        assert "Roger" in agent_manager.agents
        assert "David" in agent_manager.agents
        assert "Richard" in agent_manager.agents

        # 驗證預設使用 copilot
        assert agent_manager.agents["Roger"].config.cli == AgentCLI.COPILOT
        assert agent_manager.agents["David"].config.cli == AgentCLI.COPILOT
        assert agent_manager.agents["Richard"].config.cli == AgentCLI.COPILOT

    def test_setup_agents_with_custom_config(self, tmp_path: Path) -> None:
        """測試使用自訂設定建立 agents"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # 設定自訂 agent 設定（使用 dict 結構而非預設的 list）
        custom_config = {
            "agents": {
                "pm": {"name": "CustomPM", "cli": "gemini"},
                "developer": {"name": "CustomDev", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "cursor-agent"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager)

        # 驗證自訂設定
        assert "CustomPM" in agent_manager.agents
        assert "CustomDev" in agent_manager.agents
        assert agent_manager.agents["CustomPM"].config.cli == AgentCLI.GEMINI
        assert agent_manager.agents["Richard"].config.cli == AgentCLI.CURSOR


class TestBuildWorkflow:
    """Test workflow building functionality."""

    @patch("cafe.ui.cli.SpecPhase")
    @patch("cafe.ui.cli.PlanPhase")
    @patch("cafe.ui.cli.DevelopPhase")
    @patch("cafe.ui.cli.ReviewPhase")
    @patch("cafe.ui.cli.PRPhase")
    def test_build_workflow_creates_all_phases(
        self,
        mock_pr: Mock,
        mock_review: Mock,
        mock_impl: Mock,
        mock_plan: Mock,
        mock_req: Mock,
        tmp_path: Path,
        mock_git_ops: MagicMock,
    ) -> None:
        """測試建立 workflow 會初始化所有 5 個 phases"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))
        agent_manager = _setup_agents(config_manager)
        permission_handler = MagicMock()

        workflow = _build_workflow(
            mode=WorkflowMode.LOCAL,
            issue_id=None,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            config_manager=config_manager,
            git_ops=mock_git_ops,
        )

        # 驗證所有 phase 都被建立
        mock_req.assert_called_once()
        mock_plan.assert_called_once()
        mock_impl.assert_called_once()
        mock_review.assert_called_once()
        mock_pr.assert_called_once()

        # 驗證 workflow 有 5 個 phases
        assert len(workflow.phases) == 5

    @patch("cafe.ui.cli.PRPhase")
    @patch("cafe.ui.cli.ReviewPhase")
    @patch("cafe.ui.cli.DevelopPhase")
    @patch("cafe.ui.cli.PlanPhase")
    @patch("cafe.ui.cli.SpecPhase")
    def test_build_workflow_passes_correct_mode(
        self,
        mock_req: Mock,
        mock_plan: Mock,
        mock_impl: Mock,
        mock_review: Mock,
        mock_pr: Mock,
        tmp_path: Path,
        mock_git_ops: MagicMock,
    ) -> None:
        """測試 workflow 正確傳遞 workflow mode"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))
        agent_manager = _setup_agents(config_manager)
        permission_handler = MagicMock()

        _build_workflow(
            mode=WorkflowMode.GITHUB,
            issue_id="123",
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            config_manager=config_manager,
            git_ops=mock_git_ops,
        )

        # 驗證 SpecPhase 收到正確的 mode
        call_kwargs = mock_req.call_args.kwargs
        assert call_kwargs["workflow_mode"] == WorkflowMode.GITHUB
        assert call_kwargs["issue_id"] == "123"


class TestRunCommand:
    """Test run command."""

    def test_run_local_mode_success(self, tmp_path: Path) -> None:
        """測試 local mode 成功執行"""
        # 建立測試檔案
        req_file = tmp_path / "spec.md"
        req_file.write_text("Test requirements")
        config_file = tmp_path / "config.yaml"

        with patch("cafe.ui.cli._build_workflow") as mock_build:
            # Mock workflow execution
            mock_workflow = MagicMock()
            mock_workflow.execute.return_value = [
                PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 1 done"),
                PhaseResult(status=PhaseStatus.COMPLETED, message="Phase 2 done"),
            ]
            mock_build.return_value = mock_workflow

            result = runner.invoke(
                app,
                [
                    "run",
                    "--mode", "local",
                    "--spec", str(req_file),
                    "--config", str(config_file),
                ]
            )

            assert result.exit_code == 0
            assert "Starting CAFE workflow" in result.stdout
            assert "Mode: local" in result.stdout

    def test_run_github_mode_without_issue_fails(self, tmp_path: Path) -> None:
        """測試 github mode 沒有 issue_id 會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "github",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "--issue is required for github mode" in result.stdout

    def test_run_local_mode_missing_requirements_file_fails(self, tmp_path: Path) -> None:
        """測試 local mode 缺少 spec 檔案會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "local",
                "--spec", "nonexistent.md",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "Spec file not found" in result.stdout

    def test_run_invalid_mode_fails(self, tmp_path: Path) -> None:
        """測試無效的 mode 會失敗"""
        config_file = tmp_path / "config.yaml"

        result = runner.invoke(
            app,
            [
                "run",
                "--mode", "invalid",
                "--config", str(config_file),
            ]
        )

        assert result.exit_code == 1
        assert "Invalid mode 'invalid'" in result.stdout

    def test_run_exits_with_error_on_failed_phase(self, tmp_path: Path) -> None:
        """測試當有 phase 失敗時會回傳錯誤碼"""
        req_file = tmp_path / "spec.md"
        req_file.write_text("Test requirements")
        config_file = tmp_path / "config.yaml"

        with patch("cafe.ui.cli._build_workflow") as mock_build:
            mock_workflow = MagicMock()
            mock_workflow.execute.return_value = [
                PhaseResult(status=PhaseStatus.COMPLETED, message="Done"),
                PhaseResult(status=PhaseStatus.FAILED, message="Error"),
            ]
            mock_build.return_value = mock_workflow

            result = runner.invoke(
                app,
                [
                    "run",
                    "--mode", "local",
                    "--spec", str(req_file),
                    "--config", str(config_file),
                ]
            )

            assert result.exit_code == 1
            assert "FAILED" in result.stdout


class TestVersionCommand:
    """Test version command."""

    def test_version_shows_version_number(self) -> None:
        """測試 version 指令顯示版本號"""
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "0.1.0" in result.stdout


class TestConfigCommand:
    """Test config command."""

    def test_config_list_all(self, tmp_path: Path) -> None:
        """測試列出所有設定"""
        # Change to tmp_path directory first, then set config
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Now ConfigManager will use .cafe in current directory
            config_manager = ConfigManager()
            config_manager.set("test.key", "value")  # set() already calls save_config()

            result = runner.invoke(app, ["config"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "test:" in result.stdout or "test" in result.stdout
        assert "value" in result.stdout

    def test_config_get_existing_key(self, tmp_path: Path) -> None:
        """測試取得存在的設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Save custom config with dict structure for agents
            custom_config = {
                "agents": {
                    "pm": {"name": "Roger"}
                }
            }
            config_manager = ConfigManager()
            config_manager.save_config(custom_config)

            result = runner.invoke(app, ["config", "get", "agents.pm.name"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Roger" in result.stdout

    def test_config_get_nonexistent_key(self, tmp_path: Path) -> None:
        """測試取得不存在的設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["config", "get", "nonexistent.key"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Key not found" in result.stdout

    def test_config_set_value(self, tmp_path: Path) -> None:
        """測試設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["config", "set", "test.key", "test_value"])

            # 驗證設定已儲存
            config_manager = ConfigManager()
            assert config_manager.get("test.key") == "test_value"
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Set test.key = test_value" in result.stdout

    def test_config_without_args_shows_help(self, tmp_path: Path) -> None:
        """測試沒有參數時顯示所有設定"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["config"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # Without args, config command shows all configuration
        assert "agents" in result.stdout or "Configuration" in result.stdout


class TestPlanCommand:
    """Test plan command."""

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli.PlanPhase")
    def test_plan_local_mode_success(
        self,
        mock_plan_phase: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令 local mode 成功執行"""
        # Setup: Create spec file in the expected location
        branch_name = "test-issue"
        spec_file = tmp_path / ".cafe" / "issues" / branch_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Create a default template
        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Mock template selection
        mock_select_template.return_value = "default"

        # Mock phase execution
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Plan completed",
            data={"iterations": 2}
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "Implementation plan completed" in result.stdout
        assert "Iterations: 2" in result.stdout
        mock_plan_phase.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli.PlanPhase")
    def test_plan_github_mode_with_issue(
        self,
        mock_plan_phase: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令 github mode 使用 issue ID"""
        # Setup: GitHub mode still checks if spec file exists first
        branch_name = "test-issue"
        spec_file = tmp_path / ".cafe" / "issues" / branch_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Create a default template
        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Mock template selection
        mock_select_template.return_value = "default"

        # Mock phase execution
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Plan completed",
            data={"iterations": 1}
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "-m", "github", "-i", "123"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "GitHub Issue: #123" in result.stdout
        mock_plan_phase.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli.PlanPhase")
    def test_plan_fails_with_error(
        self,
        mock_plan_phase: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令執行失敗"""
        # Setup: Create spec file in the expected location
        branch_name = "test-issue"
        spec_file = tmp_path / ".cafe" / "issues" / branch_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec")

        # Create a default template
        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Mock template selection
        mock_select_template.return_value = "default"

        # Mock phase execution failure
        mock_phase_instance = MagicMock()
        mock_phase_instance.execute.return_value = PhaseResult(
            status=PhaseStatus.FAILED,
            message="Missing dev guide"
        )
        mock_plan_phase.return_value = mock_phase_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Plan phase failed" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_plan_invalid_mode_fails(self, mock_git_ops: Mock, tmp_path: Path) -> None:
        """測試 plan 指令使用無效 mode"""
        branch_name = "test-issue"
        config_file = tmp_path / "config.yaml"

        # Setup: Create spec file in the expected location
        spec_file = tmp_path / ".cafe" / "issues" / branch_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Spec")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                app,
                ["plan", "-m", "invalid", "--config", str(config_file)]
            )
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 1
        assert "Invalid mode" in result.stdout


class TestCloseCommand:
    """Test close command."""

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_normal_mode_success(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 指令在一般模式（非 worktree）下成功執行"""
        # Setup: Create issue config
        branch_name = "test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("""
base_branch: main
feature_branch: test-issue
""")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.return_value = None
        mock_git_instance.pull.return_value = None
        mock_git_instance.delete_branch.return_value = None
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub operations (no open PR)
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "Successfully closed issue" in result.stdout

        # Verify all operations were called
        mock_git_instance.checkout_branch.assert_called_once_with("main")
        mock_git_instance.pull.assert_called_once()
        mock_git_instance.delete_branch.assert_called_once_with("test-issue")

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_worktree_mode_success(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 指令在 worktree 模式下成功執行"""
        # Setup: Create issue config with worktree_path
        branch_name = "test-issue"
        worktree_path = ".cafe/worktrees/test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(f"""
base_branch: main
feature_branch: test-issue
worktree_path: {worktree_path}
""")

        # Create .git directory to simulate main repo
        git_dir = tmp_path / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.return_value = None
        mock_git_instance.pull.return_value = None
        mock_git_instance.remove_worktree.return_value = None
        mock_git_instance.delete_branch.return_value = None
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub operations (no open PR)
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "Successfully closed issue" in result.stdout

        # Verify all operations were called in worktree mode
        mock_git_instance.checkout_branch.assert_called_once_with("main")
        mock_git_instance.pull.assert_called_once()
        mock_git_instance.remove_worktree.assert_called_once_with(worktree_path)
        mock_git_instance.delete_branch.assert_called_once_with("test-issue")

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_fails_on_checkout_error_normal_mode(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 在 checkout 失敗時中斷（一般模式）"""
        # Setup
        branch_name = "test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("base_branch: main\nfeature_branch: test-issue\n")

        # Mock Git operations - checkout fails
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.side_effect = Exception("Uncommitted changes")
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Failed to switch to base branch" in result.stdout
        assert "Remaining steps" in result.stdout
        assert "git checkout main" in result.stdout
        assert "git pull" in result.stdout
        assert "git branch -d test-issue" in result.stdout

        # Verify subsequent operations were not called
        mock_git_instance.pull.assert_not_called()
        mock_git_instance.delete_branch.assert_not_called()

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_fails_on_pull_error_normal_mode(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 在 pull 失敗時中斷（一般模式）"""
        # Setup
        branch_name = "test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("base_branch: main\nfeature_branch: test-issue\n")

        # Mock Git operations - pull fails
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.return_value = None
        mock_git_instance.pull.side_effect = Exception("Network error")
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Failed to update base branch" in result.stdout
        assert "Remaining steps" in result.stdout
        assert "git pull" in result.stdout
        assert "git branch -d test-issue" in result.stdout

        # Verify delete was not called
        mock_git_instance.delete_branch.assert_not_called()

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_fails_on_delete_branch_error(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 在刪除分支失敗時顯示錯誤"""
        # Setup
        branch_name = "test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("base_branch: main\nfeature_branch: test-issue\n")

        # Mock Git operations - delete fails
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.return_value = None
        mock_git_instance.pull.return_value = None
        mock_git_instance.delete_branch.side_effect = Exception("Branch not fully merged")
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Failed to delete branch" in result.stdout
        assert "Remaining steps" in result.stdout
        assert "git branch -D test-issue" in result.stdout

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_fails_on_remove_worktree_error(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 在刪除 worktree 失敗時中斷（worktree 模式）"""
        # Setup
        branch_name = "test-issue"
        worktree_path = ".cafe/worktrees/test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(f"""
base_branch: main
feature_branch: test-issue
worktree_path: {worktree_path}
""")

        # Create .git directory
        git_dir = tmp_path / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)

        # Mock Git operations - remove_worktree fails
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_instance.checkout_branch.return_value = None
        mock_git_instance.pull.return_value = None
        mock_git_instance.remove_worktree.side_effect = Exception("Worktree has changes")
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = None
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Failed to remove worktree" in result.stdout
        assert "Remaining steps" in result.stdout
        assert f"git worktree remove {worktree_path}" in result.stdout
        assert "git branch -d test-issue" in result.stdout

        # Verify delete_branch was not called
        mock_git_instance.delete_branch.assert_not_called()

    @patch("cafe.ui.cli.GitHubOps")
    @patch("cafe.ui.cli.GitOperations")
    def test_close_blocks_on_open_pr(
        self,
        mock_git_ops: Mock,
        mock_github_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 close 在有 open PR 時被阻擋"""
        # Setup
        branch_name = "test-issue"
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "config.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("base_branch: main\n")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Mock GitHub - has open PR
        mock_github_instance = MagicMock()
        mock_github_instance.get_pr_for_branch.return_value = {
            "number": 123,
            "title": "Test PR",
            "state": "OPEN",
            "isDraft": False,
            "url": "https://github.com/user/repo/pull/123"
        }
        mock_github_ops.return_value = mock_github_instance

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["close"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Cannot close: Open PR found" in result.stdout
        assert "PR #123" in result.stdout

        # Verify no git operations were performed
        mock_git_instance.checkout_branch.assert_not_called()
        mock_git_instance.delete_branch.assert_not_called()
