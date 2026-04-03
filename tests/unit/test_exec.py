"""Tests for the exec launcher module."""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from cafe.core.types import AgentCLI
from cafe.ui.cli import app
from cafe.ui.exec import launch_exec_session


runner = CliRunner()


class TestLaunchExecSession:
    """Tests for launch_exec_session()."""

    def _make_agent_config(self, cli: str, session_id=None, model=None):
        """Build a mock AgentConfig."""
        config = MagicMock()
        config.cli = AgentCLI(cli)
        config.session_id = session_id
        config.model = model
        return config

    def _make_agent_manager(self, agent_name: str, cli: str, session_id=None, model=None):
        """Build a mock AgentManager with one agent."""
        config = self._make_agent_config(cli, session_id, model)
        executor = MagicMock()
        executor.config = config

        agent_manager = MagicMock()
        agent_manager.agents = {agent_name: executor}
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        return agent_manager

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_claude_cli_no_session(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test that build_command is called and subprocess.run uses its output."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None, model=None)
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["claude", "-p", "do something", "--output-format", "stream-json", "--verbose"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_claude_cli_with_session_and_model(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test that session and model are reflected in the built command."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude", "model": "sonnet"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id="sess-abc", model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["claude", "--resume", "sess-abc", "-p", "do something", "--model", "sonnet", "--output-format", "stream-json", "--verbose"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_copilot_cli_with_session(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test copilot CLI uses build_command."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "copilot"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Roger", "copilot", session_id="sess-xyz")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["copilot", "-p", "do something", "--allow-all-tools", "--resume", "sess-xyz"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("pm", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_gemini_cli_with_session_and_model(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test gemini CLI uses build_command."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Richard", "cli": "gemini", "model": "gemini-2.5-pro"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Richard", "gemini", session_id="sess-gem", model="gemini-2.5-pro")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["gemini", "-p", "do something", "--resume", "sess-gem", "--model", "gemini-2.5-pro", "--output-format", "stream-json"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("reviewer", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_cursor_agent_cli_with_session(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test cursor-agent CLI uses build_command (which uses --resume, not --session)."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "cursor-agent"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "cursor-agent", session_id="sess-cursor")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["cursor-agent", "-p", "do something", "--resume", "sess-cursor", "--force", "--output-format", "stream-json"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_cli_with_model(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test codex CLI uses build_command (codex exec flow, not -p flag)."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["codex", "-C", "/some/path", "-a", "never", "exec", "--model", "gpt-5.4", "--json", "do something"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_strategy.build_command.assert_called_once_with("do something")
        mock_run.assert_called_once_with(expected_cmd)

    @patch("builtins.print")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_display_only_prints_command_without_executing(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_print
    ):
        """Test that display_only=True prints the command string instead of executing."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude", "model": "sonnet"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id="sess-abc", model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["claude", "--resume", "sess-abc", "-p", "do something", "--model", "sonnet"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        result = launch_exec_session("developer", "issue123", "do something", display_only=True)

        assert result == 0
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "claude" in printed
        assert "do something" in printed

    @patch("builtins.print")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_display_only_does_not_execute(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_print
    ):
        """Test that display_only=True does not call subprocess.run."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = ["claude", "-p", "do something"]
        mock_get_strategy.return_value = mock_strategy

        with patch("cafe.ui.exec.subprocess.run") as mock_run:
            launch_exec_session("developer", "issue123", "do something", display_only=True)
            mock_run.assert_not_called()

    @patch("builtins.print")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_missing_cli_tool_prints_warning(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_print
    ):
        """Test that a missing CLI tool prints a warning and returns 1."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None)
        mock_agent_manager_cls.return_value = agent_manager

        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = ["claude", "-p", "do something"]
        mock_get_strategy.return_value = mock_strategy

        with patch("cafe.ui.exec.subprocess.run", side_effect=FileNotFoundError):
            result = launch_exec_session("developer", "issue123", "do something")

        assert result == 1
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "claude" in printed

    @patch("builtins.print")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_no_agent_config_prints_warning(self, mock_agent_manager_cls, mock_config_manager_cls, mock_print):
        """Test that missing agent config prints a warning and returns 0."""
        mock_config = MagicMock()
        mock_config.get.return_value = None
        mock_config_manager_cls.return_value = mock_config

        result = launch_exec_session("developer", "issue123", "do something")

        assert result == 0
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "developer" in printed

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_passes_issue_name_to_agent_manager(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_get_strategy, mock_run
    ):
        """Test that issue_name is passed to AgentManager for session resolution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = ["claude", "-p", "do something"]
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "my-issue", "do something")

        mock_agent_manager_cls.assert_called_once_with(issue_name="my-issue")

    @patch("cafe.ui.exec._extract_latest_codex_session_id", return_value="thread-123")
    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_exec_saves_new_session(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_get_strategy,
        mock_run,
        mock_extract_session,
    ):
        """Test that codex exec stores a new session after execution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["codex", "-C", "/some/path", "-a", "never", "exec", "--model", "gpt-5.4", "--json", "do something"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        result = launch_exec_session("developer", "issue123", "do something")

        assert result == 0
        mock_run.assert_called_once_with(expected_cmd)
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "thread-123",
            "issue123",
        )

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_exec_with_existing_session_uses_resume(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_get_strategy,
        mock_run,
    ):
        """Test codex exec with existing session uses resume flow and saves session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id="sess-codex", model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager

        expected_cmd = ["codex", "-C", "/some/path", "-a", "never", "exec", "resume", "--model", "gpt-5.4", "--json", "sess-codex", "do something"]
        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = expected_cmd
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        result = launch_exec_session("developer", "issue123", "do something")

        assert result == 0
        mock_run.assert_called_once_with(expected_cmd)
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "sess-codex",
            "issue123",
        )


    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec._get_cli_strategy")
    @patch("cafe.ui.exec.AgentManager")
    @patch("cafe.ui.exec.ConfigManager")
    def test_backup_clis_and_models_config_are_loaded(
        self, mock_config_manager_cls, mock_agent_manager_cls, mock_get_strategy, mock_run
    ):
        """Test that backup CLIs and models config are loaded and passed to AgentConfig."""
        mock_config = MagicMock()
        mock_config.get.return_value = {
            "name": "David",
            "cli": "claude",
            "model": "sonnet",
            "backup": ["gemini", "copilot"],
            "models": {
                "claude": {"develop": "sonnet"},
                "gemini": {"develop": "gemini-2.5-pro"},
            },
        }
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None, model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        mock_strategy = MagicMock()
        mock_strategy.build_command.return_value = ["claude", "-p", "do something"]
        mock_get_strategy.return_value = mock_strategy

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        # Verify register_agent was called with backup_clis and models_config
        call_args = agent_manager.register_agent.call_args[0][0]
        assert AgentCLI.GEMINI in call_args.backup_clis
        assert AgentCLI.COPILOT in call_args.backup_clis
        assert call_args.models_config.get("claude", {}).get("develop") == "sonnet"
        assert call_args.models_config.get("gemini", {}).get("develop") == "gemini-2.5-pro"


class TestExecCommand:
    """Tests for the cafe exec CLI command."""

    def test_invalid_role_shows_error(self):
        """Test that an invalid role shows an error message."""
        result = runner.invoke(app, ["exec", "admin", "do something"])
        assert result.exit_code != 0
        assert "admin" in result.stdout

    def test_help_flag_shows_usage(self):
        """Test that --help flag displays usage information."""
        result = runner.invoke(app, ["exec", "--help"])
        assert result.exit_code == 0
        assert "role" in result.stdout.lower() or "prompt" in result.stdout.lower()

    @patch("cafe.ui.cli.is_branch_initialized", return_value=True)
    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.launch_exec_session", return_value=0)
    def test_valid_role_calls_launch_exec_session(self, mock_launch, mock_git_ops, mock_is_initialized):
        """Test that a valid role and prompt calls launch_exec_session."""
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "issue123"
        mock_git_ops.return_value = mock_git_instance

        result = runner.invoke(app, ["exec", "developer", "do something"])

        assert result.exit_code == 0
        mock_launch.assert_called_once_with("developer", "issue123", "do something", False)

    @patch("cafe.ui.cli.is_branch_initialized", return_value=True)
    @patch("cafe.ui.cli.GitOperations")
    @patch("cafe.ui.cli.launch_exec_session", return_value=0)
    def test_display_only_flag_passed_through(self, mock_launch, mock_git_ops, mock_is_initialized):
        """Test that --display-only flag is passed to launch_exec_session."""
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "issue123"
        mock_git_ops.return_value = mock_git_instance

        result = runner.invoke(app, ["exec", "developer", "do something", "--display-only"])

        assert result.exit_code == 0
        mock_launch.assert_called_once_with("developer", "issue123", "do something", True)
