"""Tests for the reusable chat launcher module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import AgentCLI
from cafe.ui.chat import _build_chat_seed_prompt, launch_chat_session


@pytest.fixture(autouse=True)
def mock_chat_environment():
    """Avoid writing to real native CLI skill directories in unit tests."""
    with patch("cafe.ui.chat._prepare_chat_environment") as mock_prepare:
        yield mock_prepare


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
        agent_manager.session_manager = MagicMock()
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

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_prepares_chat_environment_before_launch(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        mock_chat_environment,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "issue123")

        mock_chat_environment.assert_called_once()
        kwargs = mock_chat_environment.call_args.kwargs
        assert kwargs["agent_name"] == "David"
        assert kwargs["agent_cli"] == AgentCLI.CLAUDE
        assert kwargs["role"] == "developer"
        assert kwargs["issue_name"] == "issue123"

    @patch("cafe.ui.chat._extract_latest_codex_session_id", return_value="thread-123")
    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_codex_chat_saves_new_session(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        mock_extract_session,
    ):
        """Test that Codex chat stores a new session after interactive launch."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_chat_session("developer", "issue123")

        assert result == 0
        mock_run.assert_called_once_with(["codex", "--model", "gpt-5.4"])
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "thread-123",
            "issue123",
        )

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_codex_chat_with_existing_session_uses_resume_and_updates_last_used(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
    ):
        """Test Codex interactive resume and session persistence."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id="sess-codex", model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_chat_session("developer", "issue123")

        assert result == 0
        mock_run.assert_called_once_with(["codex", "--model", "gpt-5.4", "resume", "sess-codex"])
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "sess-codex",
            "issue123",
        )


def test_build_chat_seed_prompt_includes_common_handoff_and_unified_next_step() -> None:
    prompt = _build_chat_seed_prompt(
        role="developer",
        issue_name="issue123",
        invocations={
            "common-chat-handoff": "$common-chat-handoff",
            "chat-develop-change": "$chat-develop-change",
            "chat-spec-revision": "$chat-spec-revision",
            "chat-plan-revision": "$chat-plan-revision",
        },
    )

    assert "$common-chat-handoff" in prompt
    assert "$chat-develop-change" in prompt
    assert "$chat-spec-revision" in prompt
    assert "$chat-plan-revision" in prompt
    assert "exit chat and run `cafe make`" in prompt
