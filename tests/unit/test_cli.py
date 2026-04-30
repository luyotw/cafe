"""Tests for CLI."""

import pytest
import typer
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, Mock, patch

from cafe.ui.cli import app, _setup_agents
from cafe.core.git import GitOperations
from cafe.core.types import AgentCLI
from cafe.utils.config import ConfigManager


runner = CliRunner()


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


@pytest.fixture
def config_dir_with_file(tmp_path):
    """Create a config directory with a valid config.yaml file."""
    config_dir = tmp_path / ".cafe"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("""
agents:
  pm:
    name: Roger
    cli: copilot
  developer:
    name: David
    cli: copilot
  reviewer:
    name: Richard
    cli: copilot

auto:
  max_review_iterations: 5
""")
    return config_dir


def _create_minimal_config(tmp_path: Path):
    """Helper to create minimal config.yaml in tmp_path/.cafe/"""
    from tests.conftest import create_minimal_config
    create_minimal_config(tmp_path)


class TestSetupAgents:
    """Test agent setup functionality."""

    def test_setup_agents_with_default_config(self, config_dir_with_file) -> None:
        """測試使用預設設定建立 agents"""
        config_manager = ConfigManager(str(config_dir_with_file))

        agent_manager = _setup_agents(config_manager)

        # 驗證三個 agents 都已註冊
        assert "Roger" in agent_manager.agents
        assert "David" in agent_manager.agents
        assert "Richard" in agent_manager.agents

        # 驗證有預設值
        assert agent_manager.agents["Roger"].config.cli != None
        assert agent_manager.agents["David"].config.cli != None
        assert agent_manager.agents["Richard"].config.cli != None

    def test_setup_agents_with_custom_config(self, tmp_path: Path) -> None:
        """測試使用自訂設定建立 agents"""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # 設定自訂 agent 設定（使用 dict 結構而非預設 list）
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

    def test_setup_agents_preserves_model_from_config(self, tmp_path: Path) -> None:
        """Test that agent model from config is preserved when setting up agents."""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # Create config with model settings
        custom_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "claude", "model": "haiku"},
                "developer": {"name": "David", "cli": "claude", "model": "opus"},
                "reviewer": {"name": "Richard", "cli": "gemini", "model": "gemini-2.5-flash"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager)

        # Verify models are preserved
        assert agent_manager.agents["Roger"].config.model == "haiku"
        assert agent_manager.agents["David"].config.model == "opus"
        assert agent_manager.agents["Richard"].config.model == "gemini-2.5-flash"


class TestVersionCommand:
    """Test version command."""

    def test_version_shows_version_number(self) -> None:
        """Test version command runs and shows a valid version string."""
        import re

        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert re.search(r"CAFE version \d+\.\d+\.\d+", result.stdout)


class TestConfigCommand:
    """Test config command."""

    def test_config_list_all(self, tmp_path: Path) -> None:
        """測試列出所有設定"""
        # Change to tmp_path directory first, then set config
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Create basic config first
            cafe_dir = tmp_path / ".cafe"
            cafe_dir.mkdir()
            (cafe_dir / "config.yaml").write_text("agents: {}")

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
        """測試取得存在設定值"""
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
        """測試取得不存在設定值"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _create_minimal_config(tmp_path)
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
            _create_minimal_config(tmp_path)
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
            _create_minimal_config(tmp_path)
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
    @patch("cafe.ui.cli._execute_single_step_alias")
    def test_plan_local_mode_success(
        self,
        mock_execute_alias: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令 local mode 成功執行"""
        # Setup: Create config.yaml
        _create_minimal_config(tmp_path)

        # Setup: Create versioned spec file in the expected location (new structure)
        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
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

        mock_execute_alias.return_value = {
            "status_code": "CAFE_CONFIRMED",
            "iterations": 2,
            "output_file": str(spec_file),
        }

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "--no-interactive", "--template", "default"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        assert "Implementation plan completed" in result.stdout
        assert "Iterations: 2" in result.stdout
        mock_execute_alias.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli._execute_single_step_alias")
    def test_plan_local_mode_uses_next_step_without_status_code(
        self,
        mock_execute_alias: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        _create_minimal_config(tmp_path)

        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance
        mock_select_template.return_value = "default"

        mock_execute_alias.return_value = {
            "next_step": "develop",
            "iterations": 2,
            "output_file": str(spec_file),
        }

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "--no-interactive", "--template", "default"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "Implementation plan completed" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    def test_plan_github_mode_with_issue_is_unsupported(
        self,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令不再支援 legacy GitHub issue mode."""
        # Setup: Create config.yaml
        _create_minimal_config(tmp_path)

        # Setup: Checks if versioned spec file exists first (new structure)
        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
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

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "-i", "123", "--no-interactive", "--template", "default"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "no longer supports legacy phase options" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli._execute_single_step_alias")
    def test_plan_fails_with_error(
        self,
        mock_execute_alias: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令執行失敗"""
        # Setup: Create config.yaml
        _create_minimal_config(tmp_path)

        # Setup: Create versioned spec file in the expected location (new structure)
        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
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

        mock_execute_alias.side_effect = RuntimeError("Missing dev guide")

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "--no-interactive", "--template", "default"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "Error in plan phase" in result.stdout


    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli._execute_single_step_alias")
    def test_plan_loads_template_from_issue_config(
        self,
        mock_execute_alias: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試 plan 指令從 issue.yaml 載入 template 設定，不應該提示選擇"""
        # Setup: Create config.yaml
        _create_minimal_config(tmp_path)

        # Setup: Create versioned spec file (new structure)
        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Setup: Create issue.yaml with plan.template: auto
        issue_config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
        issue_config_file.write_text("plan:\n  template: auto\n")

        # Create a default template
        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        mock_execute_alias.return_value = {
            "status_code": "CAFE_CONFIRMED",
            "iterations": 1,
            "output_file": str(spec_file),
        }

        # Execute
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan", "--no-interactive"])
        finally:
            os.chdir(old_cwd)

        # Verify: Should not call select_template because config has template setting
        mock_select_template.assert_not_called()

        mock_execute_alias.assert_called_once()

        # Verify: Command succeeded
        assert result.exit_code == 0
        assert "Implementation plan completed" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.select_template")
    @patch("cafe.ui.cli._execute_single_step_alias")
    @patch("sys.stdin.isatty")
    def test_plan_loads_template_from_issue_config_interactive(
        self,
        mock_isatty: Mock,
        mock_execute_alias: Mock,
        mock_select_template: Mock,
        mock_git_ops: Mock,
        tmp_path: Path,
    ) -> None:
        """測試互動模式下，plan 指令從 issue.yaml 載入 template 設定，不應該提示選擇"""
        # Setup: Create config.yaml
        _create_minimal_config(tmp_path)

        # Setup: Create versioned spec file (new structure)
        branch_name = "test-issue"
        spec_dir = tmp_path / ".cafe" / "issues" / branch_name / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)
        spec_file = iter_dir / "output.md"
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Setup: Create issue.yaml with plan.template: auto
        issue_config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
        issue_config_file.write_text("plan:\n  template: auto\n")

        # Create a default template
        template_dir = tmp_path / ".cafe" / "templates" / "plan"
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "default.md").write_text("# Plan Template")

        # Mock Git operations
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = branch_name
        mock_git_ops.return_value = mock_git_instance

        # Mock isatty to simulate interactive mode
        mock_isatty.return_value = True

        mock_execute_alias.return_value = {
            "status_code": "CAFE_CONFIRMED",
            "iterations": 1,
            "output_file": str(spec_file),
        }

        # Execute in interactive mode (default)
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["plan"])
        finally:
            os.chdir(old_cwd)

        # Verify: Should not call select_template because config has template setting
        mock_select_template.assert_not_called()
        mock_execute_alias.assert_called_once()

        # Verify: Command succeeded
        assert result.exit_code == 0


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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        assert "git branch -D test-issue" in result.stdout
        assert "cafe rm test-issue" in result.stdout

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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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
        assert "git branch -D test-issue" in result.stdout
        assert "cafe rm test-issue" in result.stdout

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
        config_file = tmp_path / ".cafe" / "issues" / branch_name / "issue.yaml"
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


class TestEditFileWithEditor:
    """測試 _edit_file_with_editor 函式"""

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "nano"})
    def test_edit_file_with_custom_editor(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試使用 $EDITOR 環境變數開啟檔案"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.return_value = Mock(returncode=0)

        # Execute
        _edit_file_with_editor(test_file)

        # Verify
        mock_subprocess.assert_called_once_with(["nano", str(test_file)], check=True)

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {}, clear=True)
    def test_edit_file_with_default_vim(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試預設使用 vim 作為編輯器"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.return_value = Mock(returncode=0)

        # Execute
        _edit_file_with_editor(test_file)

        # Verify
        mock_subprocess.assert_called_once_with(["vim", str(test_file)], check=True)

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "nonexistent-editor"})
    def test_edit_file_editor_not_found(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試編輯器不存在時錯誤處理"""
        from cafe.ui.cli import _edit_file_with_editor

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.side_effect = FileNotFoundError()

        # Execute & Verify
        with pytest.raises(typer.Exit) as exc_info:
            _edit_file_with_editor(test_file)
        assert exc_info.value.exit_code == 1

    @patch("cafe.ui.cli.subprocess.run")
    @patch.dict("os.environ", {"EDITOR": "vim"})
    def test_edit_file_editor_execution_failed(
        self, mock_subprocess: Mock, tmp_path: Path
    ) -> None:
        """測試編輯器執行失敗時錯誤處理"""
        from cafe.ui.cli import _edit_file_with_editor
        import subprocess

        # Setup
        test_file = tmp_path / "test.md"
        test_file.write_text("test content")
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "vim")

        # Execute & Verify
        with pytest.raises(typer.Exit) as exc_info:
            _edit_file_with_editor(test_file)
        assert exc_info.value.exit_code == 1


class TestEditCommand:
    """測試 cafe edit <phase> 指令"""

    def test_edit_rejects_unknown_phase(self) -> None:
        result = runner.invoke(app, ["edit", "unknown"])

        assert result.exit_code == 1
        assert "phase must be one of spec, plan, develop, review, pr" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_spec_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 spec artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and spec files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        iter1_dir = spec_dir / "iteration_001"
        iter2_dir = spec_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        spec_file_1 = iter1_dir / "output.md"
        spec_file_2 = iter2_dir / "output.md"
        spec_file_1.write_text("Spec version 1")
        spec_file_2.write_text("Spec version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_spec_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 spec 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no spec files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No spec file found" in result.stdout
        assert "cafe make --user-input" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_spec_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_spec_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create spec file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        iter_dir = spec_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        spec_file = iter_dir / "output.md"
        spec_file.write_text("Spec content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "spec"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()


    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_plan_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 plan artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and plan files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        iter1_dir = plan_dir / "iteration_001"
        iter2_dir = plan_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        plan_file_1 = iter1_dir / "output.md"
        plan_file_2 = iter2_dir / "output.md"
        plan_file_1.write_text("Plan version 1")
        plan_file_2.write_text("Plan version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_plan_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 plan 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no plan files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        plan_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No plan file found" in result.stdout
        assert "Run 'cafe make' first." in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_plan_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_plan_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create plan file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        plan_dir = issue_dir / "plan"
        iter_dir = plan_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        plan_file = iter_dir / "output.md"
        plan_file.write_text("Plan content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "plan"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()


    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_review_opens_latest_file(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試正確找到並開啟最新 review artifact."""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory and review files in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        iter1_dir = review_dir / "iteration_001"
        iter2_dir = review_dir / "iteration_002"
        iter1_dir.mkdir(parents=True)
        iter2_dir.mkdir(parents=True)
        review_file_1 = iter1_dir / "output.md"
        review_file_2 = iter2_dir / "output.md"
        review_file_1.write_text("Review version 1")
        review_file_2.write_text("Review version 2")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 0
        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path.name == "output.md"
        assert called_path.parent.name == "iteration_002"

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_review_no_file_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試沒有 review 檔案時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create issue directory but no review files
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        review_dir.mkdir(parents=True)

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "No review file found" in result.stdout
        assert "Run 'cafe make' first." in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    def test_edit_review_not_in_issue_branch_shows_error(
        self, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試不在 issue branch 上時顯示錯誤"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Don't create .cafe directory to simulate not initialized
        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify
        assert result.exit_code == 1
        assert "not been initialized" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_edit_review_shows_success_message(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        """測試編輯完成後顯示成功訊息"""
        import os

        # Setup
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        # Create review file in new structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        review_dir = issue_dir / "review"
        iter_dir = review_dir / "iteration_001"
        iter_dir.mkdir(parents=True)
        review_file = iter_dir / "output.md"
        review_file.write_text("Review content")

        # Execute
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["edit", "review"])
        finally:
            os.chdir(old_cwd)

        # Verify - message from _edit_file_with_editor
        assert result.exit_code == 0
        mock_edit.assert_called_once()

    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli._edit_file_with_editor")
    def test_legacy_spec_edit_still_works_with_notice(
        self, mock_edit: Mock, mock_git_ops_class: Mock, tmp_path: Path
    ) -> None:
        import os

        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "test-issue"
        mock_git_ops_class.return_value = mock_git_instance

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec" / "iteration_001"
        issue_dir.mkdir(parents=True)
        (issue_dir / "output.md").write_text("Spec content")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(app, ["spec", "edit"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert "cafe edit spec" in result.stdout
        mock_edit.assert_called_once()


class TestAutoUpdate:
    """Test auto-update functionality."""

    def test_should_check_for_updates_no_file(self, tmp_path: Path) -> None:
        """Test should_check_for_updates returns True when file doesn't exist."""
        import sys
        from pathlib import Path as PathlibPath

        # Patch get_global_cafe_dir to use tmp_path
        with patch("cafe.utils.config.get_global_cafe_dir") as mock_get_dir:
            mock_get_dir.return_value = tmp_path / ".cafe"
            from cafe.utils.config import should_check_for_updates

            # When file doesn't exist, should return True
            assert should_check_for_updates() is True

    def test_should_check_for_updates_within_24h(self, tmp_path: Path) -> None:
        """Test should_check_for_updates returns False when checked recently."""
        import json
        import time

        with patch("cafe.utils.config.get_global_cafe_dir") as mock_get_dir:
            cafe_dir = tmp_path / ".cafe"
            cafe_dir.mkdir(parents=True, exist_ok=True)
            mock_get_dir.return_value = cafe_dir

            # Create timestamp file with recent timestamp
            check_file = cafe_dir / "last_update_check.json"
            with open(check_file, "w") as f:
                json.dump({"timestamp": time.time()}, f)

            from cafe.utils.config import should_check_for_updates

            # Within 24 hours, should return False
            assert should_check_for_updates() is False

    def test_should_check_for_updates_after_24h(self, tmp_path: Path) -> None:
        """Test should_check_for_updates returns True after 24 hours."""
        import json
        import time

        with patch("cafe.utils.config.get_global_cafe_dir") as mock_get_dir:
            cafe_dir = tmp_path / ".cafe"
            cafe_dir.mkdir(parents=True, exist_ok=True)
            mock_get_dir.return_value = cafe_dir

            # Create timestamp file with old timestamp (>24 hours ago)
            check_file = cafe_dir / "last_update_check.json"
            old_time = time.time() - (25 * 3600)  # 25 hours ago
            with open(check_file, "w") as f:
                json.dump({"timestamp": old_time}, f)

            from cafe.utils.config import should_check_for_updates

            # After 24 hours, should return True
            assert should_check_for_updates() is True

    def test_update_last_check_timestamp(self, tmp_path: Path) -> None:
        """Test update_last_check_timestamp creates and updates the file."""
        import json

        with patch("cafe.utils.config.get_global_cafe_dir") as mock_get_dir:
            cafe_dir = tmp_path / ".cafe"
            mock_get_dir.return_value = cafe_dir

            from cafe.utils.config import update_last_check_timestamp

            update_last_check_timestamp()

            # Verify file was created
            check_file = cafe_dir / "last_update_check.json"
            assert check_file.exists()

            # Verify timestamp was written
            with open(check_file, "r") as f:
                data = json.load(f)
            assert "timestamp" in data
            assert isinstance(data["timestamp"], (int, float))

    def test_check_for_updates_respects_env_var(self, tmp_path: Path) -> None:
        """Test _check_for_updates returns early when env var is set."""
        import os

        with patch.dict(os.environ, {"CAFE_SKIP_UPDATE_CHECK": "1"}):
            with patch("cafe.ui.cli.ConfigManager") as mock_config:
                from cafe.ui.cli import _check_for_updates

                _check_for_updates()

                # Should not call config manager when env var is set
                mock_config.assert_not_called()

    def test_check_for_updates_respects_config_disabled(self, tmp_path: Path) -> None:
        """Test _check_for_updates respects config setting."""
        import os

        # Create config with auto_update disabled
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("settings:\n  auto_update: false\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("cafe.utils.config.should_check_for_updates") as mock_should:
                from cafe.ui.cli import _check_for_updates

                _check_for_updates()

                # should_check_for_updates should not be called when disabled
                mock_should.assert_not_called()
        finally:
            os.chdir(old_cwd)

    def test_check_for_updates_with_newer_version(self, tmp_path: Path) -> None:
        """Test _check_for_updates upgrades when newer version available."""
        import os
        import json

        # Create config with auto_update enabled
        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("settings:\n  auto_update: true\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("cafe.utils.config.should_check_for_updates", return_value=True):
                with patch("cafe.utils.config.update_last_check_timestamp"):
                    with patch("importlib.metadata.version", return_value="0.1.0"):
                        with patch("urllib.request.urlopen") as mock_urlopen:
                            # Mock PyPI response with newer version
                            pypi_response = {
                                "info": {"version": "0.1.1"}
                            }
                            mock_response = MagicMock()
                            mock_response.read.return_value = json.dumps(pypi_response).encode()
                            mock_urlopen.return_value.__enter__.return_value = mock_response

                            with patch("subprocess.run") as mock_run:
                                mock_run.return_value = MagicMock(returncode=0)
                                from cafe.ui.cli import _check_for_updates

                                _check_for_updates()

                                # Should attempt pip upgrade (filter out git calls from ConfigManager)
                                pip_calls = [
                                    c for c in mock_run.call_args_list
                                    if c[0] and "pip" in c[0][0]
                                ]
                                assert len(pip_calls) == 1
                                call_args = pip_calls[0][0][0]
                                assert "install" in call_args
                                assert "--upgrade" in call_args
                                assert "cafe-engine" in call_args
        finally:
            os.chdir(old_cwd)

    def test_check_for_updates_handles_pypi_failure(self, tmp_path: Path) -> None:
        """Test _check_for_updates handles PyPI query failure gracefully."""
        import os

        config_dir = tmp_path / ".cafe"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("settings:\n  auto_update: true\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            with patch("cafe.utils.config.should_check_for_updates", return_value=True):
                with patch("cafe.utils.config.update_last_check_timestamp"):
                    with patch("importlib.metadata.version", return_value="0.1.0"):
                        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
                            with patch("subprocess.run") as mock_run:
                                mock_run.return_value = MagicMock(returncode=0)
                                from cafe.ui.cli import _check_for_updates

                                # Should not raise exception
                                _check_for_updates()

                                # Should not attempt pip upgrade when PyPI fails
                                pip_calls = [
                                    c for c in mock_run.call_args_list
                                    if c[0] and "pip" in c[0][0]
                                ]
                                assert len(pip_calls) == 0
        finally:
            os.chdir(old_cwd)
