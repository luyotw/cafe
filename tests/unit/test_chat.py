"""Tests for the reusable chat launcher module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cafe.ui.chat import launch_chat_session


class TestLaunchChatSession:
    """Tests for launch_chat_session()."""

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
        return agent_manager

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_claude_cli_no_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for claude without session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None, model=None)
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "issue123")

        mock_run.assert_called_once_with(["claude"])

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_claude_cli_with_session_and_model(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for claude with session and model."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude", "model": "sonnet"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id="sess-abc", model="sonnet")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "issue123")

        mock_run.assert_called_once_with(["claude", "--resume", "sess-abc", "--model", "sonnet"])

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_copilot_cli_with_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for copilot with session."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "copilot"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Roger", "copilot", session_id="sess-xyz")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("pm", "issue123")

        mock_run.assert_called_once_with(["copilot", "--resume", "sess-xyz"])

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_gemini_cli_with_session_and_model(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for gemini with session and model."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Richard", "cli": "gemini", "model": "gemini-2.5-pro"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Richard", "gemini", session_id="sess-gem", model="gemini-2.5-pro")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("reviewer", "issue123")

        mock_run.assert_called_once_with(["gemini", "--resume", "sess-gem", "--model", "gemini-2.5-pro"])

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_cursor_agent_cli_with_session(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test building CLI command for cursor-agent with session (uses --session flag)."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "cursor-agent"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "cursor-agent", session_id="sess-cursor")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "issue123")

        mock_run.assert_called_once_with(["cursor-agent", "--session", "sess-cursor"])

    @patch("builtins.print")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_missing_cli_tool_prints_warning(self, mock_agent_manager_cls, mock_config_manager_cls, mock_print):
        """Test that a missing CLI tool prints a warning and does not raise."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None)
        mock_agent_manager_cls.return_value = agent_manager

        with patch("cafe.ui.chat.subprocess.run", side_effect=FileNotFoundError):
            launch_chat_session("developer", "issue123")  # Should not raise

        # Warning should be printed
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "claude" in printed

    @patch("builtins.print")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_no_agent_config_prints_warning(self, mock_agent_manager_cls, mock_config_manager_cls, mock_print):
        """Test that missing agent config prints a warning and does not raise."""
        mock_config = MagicMock()
        mock_config.get.return_value = None  # No agent config
        mock_config_manager_cls.return_value = mock_config

        launch_chat_session("developer", "issue123")  # Should not raise

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "developer" in printed

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_passes_issue_name_to_agent_manager(self, mock_agent_manager_cls, mock_config_manager_cls, mock_run):
        """Test that issue_name is passed to AgentManager for session resolution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "my-issue")

        mock_agent_manager_cls.assert_called_once_with(issue_name="my-issue")
