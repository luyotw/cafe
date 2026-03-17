"""Tests for AgentManager."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager, AgentNotFoundError
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI
from cafe.core.session import SessionManager


class TestAgentManagerBasics:
    """Test basic AgentManager functionality."""

    def test_init_agent_manager(self) -> None:
        """Test AgentManager initialization."""
        manager = AgentManager()
        assert manager is not None
        assert manager.agents == {}

    def test_init_with_session_manager(self) -> None:
        """Test initialization with SessionManager."""
        session_mgr = SessionManager()
        manager = AgentManager(session_manager=session_mgr)

        assert manager.session_manager == session_mgr

    def test_register_agent(self) -> None:
        """Test registering an agent."""
        manager = AgentManager()
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)

        manager.register_agent(config)

        assert "Roger" in manager.agents
        assert manager.agents["Roger"].config.name == config.name
        assert manager.agents["Roger"].config.cli == config.cli
        # session_id is None until first execution (lazy creation)
        assert manager.agents["Roger"].config.session_id is None


class TestAgentRetrieval:
    """Test agent retrieval."""

    def test_get_agent_returns_executor(self) -> None:
        """Test that get_agent returns an AgentExecutor."""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert isinstance(executor, AgentExecutor)
        assert executor.config.name == "David"

    def test_get_agent_not_found_raises_error(self) -> None:
        """Test that getting a nonexistent agent raises an error."""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError, match="Agent 'Unknown' not found"):
            manager.get_agent("Unknown")

    def test_get_agent_no_session_until_execute(self) -> None:
        """Test that session is not created when getting an agent (lazy creation)."""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Session should be None until first execution (lazy creation)
        assert executor.config.session_id is None
        # Should not have saved any session yet
        session_mgr.save_session.assert_not_called()


class TestAgentSwitching:
    """Test agent switching."""

    def test_switch_to_existing_agent(self) -> None:
        """Test switching to an existing agent."""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))

        manager.switch_agent("Roger")
        assert manager.current_agent_name == "Roger"

        manager.switch_agent("David")
        assert manager.current_agent_name == "David"

    def test_switch_to_nonexistent_agent_raises_error(self) -> None:
        """Test that switching to a nonexistent agent raises an error."""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError):
            manager.switch_agent("Unknown")

    def test_get_current_agent(self) -> None:
        """Test getting the current agent."""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.switch_agent("Roger")

        current = manager.get_current_agent()

        assert current is not None
        assert current.config.name == "Roger"

    def test_get_current_agent_when_none_returns_none(self) -> None:
        """Test that get_current_agent returns None when no agent is selected."""
        manager = AgentManager()

        current = manager.get_current_agent()

        assert current is None


class TestAgentExecution:
    """Test agent execution through manager."""

    def test_execute_with_agent_name(self) -> None:
        """Test executing an agent by name."""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage, AgentResponse
            mock_execute.return_value = AgentResponse(
                response="Agent response",
                token_usage=TokenUsage()
            )

            response, token_usage, permission_denials, cli_command_args, streaming_log, model = manager.execute("David", "Test prompt")

            assert response == "Agent response"
            assert streaming_log == []
            mock_execute.assert_called_once_with("Test prompt", None, None, None)

    def test_execute_returns_tuple_with_token_usage(self) -> None:
        """Test that execute returns a 6-tuple (response, token_usage, permission_denials, cli_command_args, streaming_log, model)."""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage, AgentResponse
            expected_token_usage = TokenUsage(input_tokens=100, output_tokens=50)
            mock_execute.return_value = AgentResponse(
                response="Agent response",
                token_usage=expected_token_usage
            )

            result = manager.execute("David", "Test prompt")

            # Should return 6-tuple (response, token_usage, permission_denials, cli_command_args, streaming_log, model)
            assert isinstance(result, tuple)
            assert len(result) == 6
            response, token_usage, permission_denials, cli_command_args, streaming_log, model = result
            assert response == "Agent response"
            assert token_usage.input_tokens == 100
            assert permission_denials == []
            assert cli_command_args is None
            assert streaming_log == []
            assert token_usage.output_tokens == 50

    def test_execute_current_agent(self) -> None:
        """Test executing the current agent."""
        manager = AgentManager()
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)
        manager.switch_agent("Roger")

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage
            mock_execute.return_value = ("Current agent response", TokenUsage())

            response = manager.execute_current("Test prompt")

            assert response == "Current agent response"

    def test_execute_current_when_no_current_raises_error(self) -> None:
        """Test that executing with no current agent raises an error."""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError, match="No current agent selected"):
            manager.execute_current("Test prompt")


class TestSessionManagement:
    """Test session management through AgentManager."""

    def test_resume_existing_session(self) -> None:
        """Test resuming an existing session."""
        from cafe.core.types import SessionData
        from datetime import datetime

        session_mgr = MagicMock(spec=SessionManager)
        # Return SessionData instead of string
        session_data = SessionData(
            agent_name="David",
            cli=AgentCLI.CLAUDE,
            session_id="existing-session-456",
            created_at=datetime.now(),
            last_used_at=datetime.now(),
        )
        session_mgr.load_session.return_value = session_data

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert executor.config.session_id == "existing-session-456"
        session_mgr.load_session.assert_called_once_with("David", AgentCLI.CLAUDE, None)

    def test_session_lazy_creation(self) -> None:
        """Test that session creation is deferred until first execution."""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Session is None until first execution (lazy creation by executor)
        assert executor.config.session_id is None
        # No session created at registration time
        session_mgr.save_session.assert_not_called()

    def test_create_claude_session_calls_cli(self) -> None:
        """Test that _create_claude_session calls Claude CLI and parses the session ID."""
        import subprocess
        import json

        session_mgr = MagicMock(spec=SessionManager)
        manager = AgentManager(session_manager=session_mgr)

        # Mock subprocess.run to return a JSON response with session_id
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "session_id": "0603a149-90f6-4bc0-b687-3610aae4e082",
            "result": "Hi! How can I help you today?"
        })

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            session_id = manager._create_claude_session()

            # Should call claude with correct arguments
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "claude"
            assert "-p" in args or "--print" in args
            assert "--output-format" in args
            assert "json" in args

            # Should return the session_id from JSON response
            assert session_id == "0603a149-90f6-4bc0-b687-3610aae4e082"

    def test_create_claude_session_handles_error(self) -> None:
        """Test that _create_claude_session handles errors."""
        import subprocess

        session_mgr = MagicMock(spec=SessionManager)
        manager = AgentManager(session_manager=session_mgr)

        # Mock subprocess.run to fail
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: API key not found"

        with patch('subprocess.run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="Failed to create Claude session"):
                manager._create_claude_session()

    def test_delete_agent_session(self) -> None:
        """Test deleting an agent session."""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None  # Mock to return None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        manager.delete_session("David")

        session_mgr.delete_session.assert_called_once_with("David", AgentCLI.CLAUDE, None)


class TestMultipleAgents:
    """Test managing multiple agents."""

    def test_register_multiple_agents(self) -> None:
        """Test registering multiple agents."""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="Cursor", cli=AgentCLI.CURSOR))

        assert len(manager.agents) == 3
        assert "Roger" in manager.agents
        assert "David" in manager.agents
        assert "Cursor" in manager.agents

    def test_list_agents(self) -> None:
        """Test listing all agents."""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))

        agent_names = manager.list_agents()

        assert len(agent_names) == 2
        assert "Roger" in agent_names
        assert "David" in agent_names

    def test_has_agent(self) -> None:
        """Test checking whether an agent exists."""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))

        assert manager.has_agent("Roger")
        assert not manager.has_agent("Unknown")


class TestAgentConfiguration:
    """Test agent configuration management."""

    def test_update_agent_config(self) -> None:
        """Test updating agent configuration."""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        # Update to different CLI
        new_config = AgentConfig(
            name="David", cli=AgentCLI.GEMINI
        )
        manager.register_agent(new_config)

        executor = manager.get_agent("David")
        assert executor.config.cli == AgentCLI.GEMINI

    def test_get_agent_config(self) -> None:
        """Test getting agent configuration."""
        manager = AgentManager()
        config = AgentConfig(
            name="Roger", cli=AgentCLI.CLAUDE
        )
        manager.register_agent(config)

        retrieved_config = manager.get_agent_config("Roger")

        assert retrieved_config.name == "Roger"

    def test_register_agent_preserves_model_field(self) -> None:
        """Test that model field is preserved when registering agent."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            model="haiku"
        )

        manager.register_agent(config)

        # Verify model is preserved in the registered executor
        executor = manager.get_agent("David")
        assert executor.config.model == "haiku"

    def test_register_agent_preserves_none_model(self) -> None:
        """Test that None model is preserved (not replaced with default)."""
        manager = AgentManager()
        config = AgentConfig(
            name="Roger",
            cli=AgentCLI.CLAUDE,
            model=None
        )

        manager.register_agent(config)

        # Verify None model is preserved
        executor = manager.get_agent("Roger")
        assert executor.config.model is None


class TestGetAgentFilePath:
    """Tests for get_agent_file_path path lookup priority."""

    def test_local_cafe_agent_has_highest_priority(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that local .cafe/agents/ takes priority over global and system,
        even when CWD is a subdirectory of the repo root."""
        from pathlib import Path as RealPath

        # Set up repo root with local .cafe agent
        repo_root = tmp_path / "repo"
        local_agent = repo_root / ".cafe" / "agents" / "pm" / "Roger.md"
        local_agent.parent.mkdir(parents=True)
        local_agent.write_text("# Roger (local)")

        # Set up global agent
        global_home = tmp_path / "global_home"
        global_agent = global_home / ".cafe" / "agents" / "pm" / "Roger.md"
        global_agent.parent.mkdir(parents=True)
        global_agent.write_text("# Roger (global)")

        # Run from a subdirectory to verify upward search finds .cafe/agents/
        subdir = repo_root / "src" / "nested"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        with patch.object(RealPath, "home", return_value=global_home):
            result = AgentManager.get_agent_file_path("Roger", "pm")

        # Local .cafe/ path should be an absolute path under repo root
        assert result == str(repo_root / ".cafe" / "agents" / "pm" / "Roger.md")

    def test_falls_back_to_global_when_no_local(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global ~/.cafe/agents/ is used when no local agent exists."""
        from pathlib import Path as RealPath

        # Repo root has no local .cafe agents
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        # Set up global agent
        global_home = tmp_path / "global_home"
        global_agent = global_home / ".cafe" / "agents" / "pm" / "Roger.md"
        global_agent.parent.mkdir(parents=True)
        global_agent.write_text("# Roger (global)")

        # Run from a subdirectory
        subdir = repo_root / "subdir"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        with patch.object(RealPath, "home", return_value=global_home):
            result = AgentManager.get_agent_file_path("Roger", "pm")

        assert result == str(global_agent)

    def test_falls_back_to_system_when_no_local_or_global(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that system default path is used when neither local nor global exists."""
        from pathlib import Path as RealPath

        # Repo root with no local agents
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        # Global directory does not exist
        global_home = tmp_path / "nonexistent_home"

        # Run from a subdirectory
        subdir = repo_root / "subdir"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        with patch.object(RealPath, "home", return_value=global_home):
            result = AgentManager.get_agent_file_path("Roger", "pm")

        # Falls back to system default path
        assert result == "src/cafe/data/agents/pm/Roger.md"
