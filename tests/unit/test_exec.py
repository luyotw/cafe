"""Tests for the exec launcher module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import AgentCLI
from cafe.ui.exec import launch_exec_session


class TestLaunchExecSession:
    """Tests for launch_exec_session()."""

    def _make_agent_config(self, cli: str, session_id=None, model=None):
        """Build a mock AgentConfig."""
        config = MagicMock()
        config.cli.value = cli
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
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_claude_cli_no_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for claude without session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None, model=None)
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_run.assert_called_once_with(["claude", "-p", "do something"])

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_claude_cli_with_session_and_model(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for claude with session and model."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude", "model": "sonnet"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id="sess-abc", model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_run.assert_called_once_with(["claude", "--resume", "sess-abc", "-p", "do something", "--model", "sonnet"])

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_copilot_cli_with_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for copilot with session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "copilot"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Roger", "copilot", session_id="sess-xyz")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("pm", "issue123", "do something")

        mock_run.assert_called_once_with(["copilot", "--resume", "sess-xyz", "-p", "do something"])

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_gemini_cli_with_session_and_model(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for gemini with session and model."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Richard", "cli": "gemini", "model": "gemini-2.5-pro"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Richard", "gemini", session_id="sess-gem", model="gemini-2.5-pro")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("reviewer", "issue123", "do something")

        mock_run.assert_called_once_with(
            ["gemini", "--resume", "sess-gem", "-p", "do something", "--model", "gemini-2.5-pro"]
        )

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_cursor_agent_cli_with_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for cursor-agent with session (uses --session flag)."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "cursor-agent"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "cursor-agent", session_id="sess-cursor")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_run.assert_called_once_with(["cursor-agent", "--session", "sess-cursor", "-p", "do something"])

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_cli_with_model(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for codex with model."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "issue123", "do something")

        mock_run.assert_called_once_with(["codex", "--model", "gpt-5.4", "-p", "do something"])

    @patch("builtins.print")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_display_only_prints_command_without_executing(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_print
    ):
        """Test that display_only=True prints the command string instead of executing."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude", "model": "sonnet"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id="sess-abc", model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_exec_session("developer", "issue123", "do something", display_only=True)

        assert result == 0
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "claude" in printed
        assert "do something" in printed

    @patch("builtins.print")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_display_only_does_not_execute(self, mock_agent_manager_cls, mock_config_manager_cls, mock_print):
        """Test that display_only=True does not call subprocess.run."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        with patch("cafe.ui.exec.subprocess.run") as mock_run:
            launch_exec_session("developer", "issue123", "do something", display_only=True)
            mock_run.assert_not_called()

    @patch("builtins.print")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_missing_cli_tool_prints_warning(self, mock_agent_manager_cls, mock_config_manager_cls, mock_print):
        """Test that a missing CLI tool prints a warning and returns 1."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None)
        mock_agent_manager_cls.return_value = agent_manager

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
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_passes_issue_name_to_agent_manager(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test that issue_name is passed to AgentManager for session resolution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_exec_session("developer", "my-issue", "do something")

        mock_agent_manager_cls.assert_called_once_with(issue_name="my-issue")

    @patch("cafe.ui.exec._extract_latest_codex_session_id", return_value="thread-123")
    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_exec_saves_new_session(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        mock_extract_session,
    ):
        """Test that codex exec stores a new session after execution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_exec_session("developer", "issue123", "do something")

        assert result == 0
        mock_run.assert_called_once_with(["codex", "--model", "gpt-5.4", "-p", "do something"])
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "thread-123",
            "issue123",
        )

    @patch("cafe.ui.exec.subprocess.run")
    @patch("cafe.ui.exec.ConfigManager")
    @patch("cafe.ui.exec.AgentManager")
    def test_codex_exec_with_existing_session_uses_resume(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
    ):
        """Test codex exec with existing session uses resume and saves session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id="sess-codex", model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_exec_session("developer", "issue123", "do something")

        assert result == 0
        mock_run.assert_called_once_with(
            ["codex", "--model", "gpt-5.4", "resume", "sess-codex", "-p", "do something"]
        )
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "sess-codex",
            "issue123",
        )
