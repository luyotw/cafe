"""Tests for CLI commands using agent names from config instead of hardcoded defaults.

測試 CLI 指令從 config 讀取 agent 名稱而非使用 hardcoded 預設值.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.status_codes import PhaseStatusCode
from cafe.ui.cli import app


@pytest.fixture
def runner():
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations."""
    with patch("cafe.ui.cli.GitOperations") as mock:
        mock_instance = Mock()
        mock_instance.get_current_branch.return_value = "test-branch"
        mock_instance.get_repo_root.return_value = Path.cwd()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager."""
    with patch("cafe.ui.cli.AgentManager") as mock:
        mock_instance = Mock()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_permission_handler():
    """Mock PermissionHandler."""
    with patch("cafe.ui.cli.PermissionHandler") as mock:
        mock_instance = Mock()
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def setup_test_env(tmp_path, monkeypatch):
    """Setup test environment with issue config and spec file."""
    # Create issue directory and config
    issue_dir = tmp_path / ".cafe" / "issues" / "test-branch"
    issue_dir.mkdir(parents=True, exist_ok=True)

    config_file = issue_dir / "issue.yaml"
    config_file.write_text("issue_name: test-branch\nbase_branch: main\n")

    # Create spec directory with spec file in new structure (required for plan command)
    spec_dir = issue_dir / "spec"
    iter_dir = spec_dir / "iteration_001"
    iter_dir.mkdir(parents=True, exist_ok=True)
    spec_file = iter_dir / "output.md"
    spec_file.write_text("# Test Spec\n\nThis is a test spec.")

    # Change to tmp_path
    monkeypatch.chdir(tmp_path)

    return tmp_path


@pytest.fixture
def mock_config_manager_with_custom_dev():
    """Mock ConfigManager that returns 'John' as developer agent name."""
    with patch("cafe.ui.cli.ConfigManager") as mock:
        mock_instance = Mock()
        # Return 'John' for developer agent name, defaults for others
        def get_side_effect(key, default=None):
            if key == "agents.developer.name":
                return "John"
            elif key == "agents.pm.name":
                return "Roger"
            elif key == "agents.reviewer.name":
                return "Richard"
            elif key == "agents.pm.cli":
                return "copilot"
            elif key == "agents.developer.cli":
                return "copilot"
            elif key == "agents.reviewer.cli":
                return "copilot"
            return default

        mock_instance.get.side_effect = get_side_effect
        mock.return_value = mock_instance
        yield mock


class TestPlanCommandAgentDefault:
    """測試 plan 指令使用 config 中 developer agent 名稱."""

    def test_plan_uses_config_developer_name_when_no_flag_provided(
        self,
        runner,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_config_manager_with_custom_dev,
        setup_test_env,
    ):
        """當沒有提供 --dev flag 時, plan 指令應使用 config 中 developer agent 名稱."""
        # Setup: Mock _setup_agents to return our mock agent_manager
        with patch("cafe.ui.cli._setup_agents") as mock_setup:
            mock_setup.return_value = mock_agent_manager.return_value

            with patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias:
                mock_execute_alias.return_value = {
                    "iterations": 1,
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                    "output_file": ".cafe/issues/test-branch/plan/iteration_001/output.md",
                }

                # Mock get_agent to track which agent name is requested
                mock_executor = Mock()
                mock_executor.config.cli.value = "copilot"
                mock_executor.config.session_id = "test-session"
                mock_agent_manager.return_value.get_agent.return_value = mock_executor

                # Execute plan command WITHOUT --dev flag
                result = runner.invoke(app, ["plan", "--interactive"])

                call_kwargs = mock_execute_alias.call_args.kwargs
                assert call_kwargs["role_agent_map_override"] is None

    def test_plan_uses_flag_value_when_dev_flag_provided(
        self,
        runner,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_config_manager_with_custom_dev,
        setup_test_env,
    ):
        """當提供 --dev flag 時, plan 指令應使用 flag 指定名稱而非 config."""
        # Setup: Mock _setup_agents to return our mock agent_manager
        with patch("cafe.ui.cli._setup_agents") as mock_setup:
            mock_setup.return_value = mock_agent_manager.return_value

            with patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias:
                mock_execute_alias.return_value = {
                    "iterations": 1,
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                    "output_file": ".cafe/issues/test-branch/plan/iteration_001/output.md",
                }

                # Mock get_agent
                mock_executor = Mock()
                mock_executor.config.cli.value = "copilot"
                mock_executor.config.session_id = "test-session"
                mock_agent_manager.return_value.get_agent.return_value = mock_executor

                # Execute plan command WITH --dev flag
                result = runner.invoke(app, ["plan", "--dev", "CustomDev", "--interactive"])

                call_kwargs = mock_execute_alias.call_args.kwargs
                assert call_kwargs["role_agent_map_override"] == {"developer": "CustomDev"}


class TestDevelopCommandAgentDefault:
    """測試 develop 指令使用 config 中 developer agent 名稱."""

    def test_develop_uses_config_developer_name_when_no_flag_provided(
        self,
        runner,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        mock_config_manager_with_custom_dev,
        setup_test_env,
    ):
        """當沒有提供 --dev flag 時, develop 指令應使用 config 中 developer agent 名稱."""
        # Create plan file in new structure (required for develop command)
        plan_iter_dir = setup_test_env / ".cafe" / "issues" / "test-branch" / "plan" / "iteration_001"
        plan_iter_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_iter_dir / "output.md"
        plan_file.write_text("# Test Plan\n\nThis is a test plan.")

        # Setup: Mock _setup_agents to return our mock agent_manager
        with patch("cafe.ui.cli._setup_agents") as mock_setup:
            mock_setup.return_value = mock_agent_manager.return_value

            with patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias:
                mock_execute_alias.return_value = {
                    "iterations": 1,
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                    "output_file": ".cafe/issues/test-branch/develop/iteration_001/output.md",
                }

                # Mock get_agent
                mock_executor = Mock()
                mock_executor.config.cli.value = "copilot"
                mock_executor.config.session_id = "test-session"
                mock_agent_manager.return_value.get_agent.return_value = mock_executor

                # Execute develop command WITHOUT --dev flag
                result = runner.invoke(app, ["develop", "--interactive", "--user-input", "start"])

                call_kwargs = mock_execute_alias.call_args.kwargs
                assert call_kwargs["role_agent_map_override"] is None


class TestSpecCommandAgentDefault:
    """測試 spec 指令使用 config 中 PM agent 名稱."""

    def test_spec_uses_config_pm_name_when_no_flag_provided(
        self,
        runner,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        setup_test_env,
    ):
        """當沒有提供 --pm flag 時, spec 指令應使用 config 中 PM agent 名稱."""
        # Setup: Mock ConfigManager to return 'Alice' as PM agent name
        with patch("cafe.ui.cli.ConfigManager") as mock_config:
            mock_config_instance = Mock()

            def get_side_effect(key, default=None):
                if key == "agents.pm.name":
                    return "Alice"
                elif key == "agents.developer.name":
                    return "David"
                elif key == "agents.reviewer.name":
                    return "Richard"
                elif key == "agents.pm.cli":
                    return "copilot"
                elif key == "agents.developer.cli":
                    return "copilot"
                elif key == "agents.reviewer.cli":
                    return "copilot"
                return default

            mock_config_instance.get.side_effect = get_side_effect
            mock_config.return_value = mock_config_instance

            # Mock _setup_agents
            with patch("cafe.ui.cli._setup_agents") as mock_setup:
                mock_setup.return_value = mock_agent_manager.return_value

                with patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias:
                    mock_execute_alias.return_value = {
                        "iterations": 1,
                        "status_code": PhaseStatusCode.CONFIRMED.value,
                        "output_file": ".cafe/issues/test-branch/spec/iteration_001/output.md",
                    }

                    # Mock get_agent
                    mock_executor = Mock()
                    mock_executor.config.cli.value = "copilot"
                    mock_executor.config.session_id = "test-session"
                    mock_agent_manager.return_value.get_agent.return_value = mock_executor

                    # Execute spec command WITHOUT --pm flag
                    result = runner.invoke(app, ["spec", "--interactive", "--user-input", "test spec"])

                    call_kwargs = mock_execute_alias.call_args.kwargs
                    assert call_kwargs["role_agent_map_override"] is None
