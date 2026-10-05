"""Tests for AgentManager."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager, AgentNotFoundError
from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI
from cafe.core.session import SessionManager


class TestImmediateSessionPersistence:
    def test_backup_session_is_saved_once_under_its_own_cli(self):
        from cafe.core.types import AgentResponse, CliEntry, TokenUsage

        store = MagicMock(spec=SessionManager)
        store.load_session.return_value = None
        manager = AgentManager(session_manager=store, issue_name="early-session")
        manager.register_agent(AgentConfig(
            name="Morgan", cli=AgentCLI.CLAUDE,
            clis=[CliEntry(cli=AgentCLI.CLAUDE), CliEntry(cli=AgentCLI.CODEX)],
        ))
        executors = []

        def execute(executor, *args, **kwargs):
            executors.append(executor)
            if executor.config.cli == AgentCLI.CLAUDE:
                raise AgentExecutionError("missing", error_type="cli_not_found")
            executor.on_session_observed("backup-session")
            executor.on_session_observed("backup-session")
            store.save_session.assert_called_once_with(
                "Morgan", AgentCLI.CODEX, "backup-session", "early-session", "synthesize"
            )
            return AgentResponse(
                response="done", token_usage=TokenUsage(), cli=AgentCLI.CODEX,
                session_id="backup-session",
            )

        with patch.object(AgentExecutor, "execute", new=execute):
            result = manager.execute("Morgan", "fixture", phase_name="synthesize")
        assert result[0] == "done"
        assert store.save_session.call_count == 1
        assert all(executor.on_session_observed is None for executor in executors)

    @pytest.mark.parametrize("outcome", ["success", "rate_limit", "incomplete_stream"])
    def test_provider_sees_saved_session_before_finishing(
        self, tmp_path, monkeypatch, outcome
    ):
        import sys

        from cafe.agents.executor import AgentExecutionControl

        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="early-session", stream_agent_output=False)
        manager.register_agent(AgentConfig(name="Morgan", cli=AgentCLI.CODEX))
        session_file = manager.session_manager.get_session_file(
            "Morgan", AgentCLI.CODEX, "early-session", "synthesize"
        ).resolve()
        script = (
            "import json,sys,time; from pathlib import Path; "
            "print(json.dumps({'type':'thread.started','thread_id':'fixture-session'}),"
            "flush=True); "
            f"path=Path({str(session_file)!r}); deadline=time.monotonic()+2\n"
            "while time.monotonic()<deadline:\n"
            "    try:\n"
            "        if json.loads(path.read_text())['session_id']=='fixture-session': break\n"
            "    except (FileNotFoundError,json.JSONDecodeError): pass\n"
            "    time.sleep(.01)\n"
            "else: raise RuntimeError('session was not saved before provider completion')\n"
        )
        if outcome == "success":
            script += "print(json.dumps({'type':'turn.completed','usage':{}}),flush=True)\n"
        elif outcome == "rate_limit":
            script += "sys.stderr.write('API rate limit reached\\n'); sys.exit(1)\n"
        executor = manager.get_agent("Morgan")
        with (
            patch.object(AgentExecutor, "_build_controlled_command", return_value=(
                [sys.executable, "-u", "-c", script], tmp_path
            )),
            patch.object(
                manager.session_manager, "save_session",
                wraps=manager.session_manager.save_session,
            ) as save,
            patch("cafe.agents.manager.time.sleep"),
        ):
            arguments = dict(
                phase_name="synthesize",
                streaming_output_file=str(tmp_path / "stream.jsonl"),
                execution_control=AgentExecutionControl(max_duration_seconds=3),
            )
            if outcome == "success":
                manager.execute("Morgan", "fixture", **arguments)
                assert not manager.get_failed_attempts()
            else:
                with pytest.raises(AgentExecutionError) as caught:
                    manager.execute("Morgan", "fixture", **arguments)
                assert caught.value.error_type == outcome
                assert [item["session_id"] for item in manager.get_failed_attempts()] == [
                    "fixture-session"
                ] * 3
            save.assert_called_once_with(
                "Morgan", AgentCLI.CODEX, "fixture-session", "early-session", "synthesize"
            )
        assert executor.on_session_observed is None

    @pytest.mark.parametrize("fail", [False, True])
    def test_observer_is_restored_after_attempt(self, fail):
        from cafe.core.types import AgentResponse, TokenUsage

        store = MagicMock(spec=SessionManager)
        store.load_session.return_value = None
        manager = AgentManager(session_manager=store)
        manager.register_agent(AgentConfig(name="Morgan", cli=AgentCLI.CODEX))
        executor = manager.get_agent("Morgan")
        previous = MagicMock()
        executor.on_session_observed = previous

        def execute(*args, **kwargs):
            executor.on_session_observed("fixture-session")
            store.save_session.assert_called_once_with(
                "Morgan", AgentCLI.CODEX, "fixture-session", None, "synthesize"
            )
            if fail:
                raise AgentExecutionError("stopped", error_type="pipe_read_error")
            return AgentResponse(
                response="done", token_usage=TokenUsage(), cli=AgentCLI.CODEX,
                session_id="fixture-session",
            )

        with (
            patch.object(manager, "get_execution_config", return_value=executor.config),
            patch.object(executor, "execute", side_effect=execute),
        ):
            if fail:
                with pytest.raises(AgentExecutionError):
                    manager.execute("Morgan", "fixture", phase_name="synthesize")
            else:
                manager.execute("Morgan", "fixture", phase_name="synthesize")
        assert executor.on_session_observed is previous
        previous.assert_called_once_with("fixture-session")
        assert store.save_session.call_count == 1

    def test_exact_wrong_session_does_not_overwrite_saved_session(self, tmp_path, monkeypatch):
        import sys

        from cafe.core.session_continuation import SessionContinuation

        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="exact-session", stream_agent_output=False)
        manager.register_agent(AgentConfig(name="Morgan", cli=AgentCLI.CODEX))
        manager.session_manager.save_session(
            "Morgan", AgentCLI.CODEX, "expected", "exact-session", "synthesize"
        )
        script = (
            "import json; "
            "print(json.dumps({'type':'thread.started','thread_id':'wrong'}),flush=True); "
            "print(json.dumps({'type':'turn.completed','usage':{}}),flush=True)"
        )
        with (
            patch.object(AgentExecutor, "_build_controlled_command", return_value=(
                [sys.executable, "-u", "-c", script], tmp_path
            )),
            patch.object(manager.session_manager, "save_session") as save,
        ):
            with pytest.raises(AgentExecutionError) as caught:
                manager.execute(
                    "Morgan", "fixture", phase_name="synthesize",
                    continuation=SessionContinuation.resume_exact(AgentCLI.CODEX, "expected"),
                )
        assert caught.value.error_type == "session_mismatch"
        save.assert_not_called()
        assert manager.session_manager.load_session(
            "Morgan", AgentCLI.CODEX, "exact-session", "synthesize"
        ).session_id == "expected"
        assert "session_id" not in manager.get_failed_attempts()[0]

    def test_session_write_failure_stops_without_retry(self, tmp_path):
        import sys

        store = MagicMock(spec=SessionManager)
        store.load_session.return_value = None
        store.save_session.side_effect = OSError("token=private-fixture")
        manager = AgentManager(session_manager=store, stream_agent_output=False)
        manager.register_agent(AgentConfig(name="Morgan", cli=AgentCLI.CODEX))
        script = (
            "import json,time; "
            "print(json.dumps({'type':'thread.started','thread_id':'observed'}),flush=True); "
            "time.sleep(10)"
        )
        with (
            patch.object(AgentExecutor, "_build_controlled_command", return_value=(
                [sys.executable, "-u", "-c", script], tmp_path
            )),
            patch("cafe.agents.manager.time.sleep") as retry_sleep,
        ):
            with pytest.raises(AgentExecutionError) as caught:
                manager.execute("Morgan", "fixture", phase_name="synthesize")
        assert caught.value.error_type == "session_persistence_error"
        assert not any(call.args[0] >= 30 for call in retry_sleep.call_args_list)
        assert store.save_session.call_count == 1
        assert "private-fixture" not in manager.get_failed_attempts()[0]["error_excerpt"]


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
            mock_execute.assert_called_once()
            assert mock_execute.call_args.args[0].endswith("Test prompt")
            assert mock_execute.call_args.args[1:] == (None, None, None)

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

    def test_execute_retries_provider_overload_after_a_bounded_delay(self) -> None:
        """Temporary provider capacity retries the same CLI before any fallback."""
        from cafe.core.types import AgentResponse, TokenUsage

        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Nick", cli=AgentCLI.CODEX))
        overloaded = AgentExecutionError(
            "Selected model is at capacity.",
            error_type="provider_overloaded",
            display_message="Codex provider is temporarily at capacity.",
        )
        success = AgentResponse(response="completed", token_usage=TokenUsage())

        with (
            patch.object(AgentExecutor, "execute", side_effect=[overloaded, success]) as execute,
            patch("cafe.agents.manager.time.sleep") as sleep,
        ):
            result = manager.execute("Nick", "Test prompt")

        assert result[0] == "completed"
        assert execute.call_count == 2
        sleep.assert_called_once_with(30)
        assert manager.get_failed_attempts() == [
            {
                "cli": "codex",
                "chain_role": "primary",
                "attempt": 1,
                "error_type": "provider_overloaded",
                "error_excerpt": "Codex provider is temporarily at capacity.",
            }
        ]

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

    @pytest.mark.parametrize("chain_role", ["primary", "fallback"])
    @pytest.mark.parametrize("failures", [1, 2, 3])
    def test_incomplete_stream_retries_same_session_with_bounded_attempts(
        self, tmp_path, monkeypatch, chain_role, failures
    ) -> None:
        """Incomplete output retries in place, then succeeds or exposes the final error."""
        from cafe.core.session_continuation import SessionContinuation
        from cafe.core.types import AgentResponse, CliEntry, TokenUsage

        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="retry-stream")
        codex = CliEntry(cli=AgentCLI.CODEX, model="codex-model")
        other = CliEntry(cli=AgentCLI.CLAUDE, model="claude-model")
        chain = [codex, other] if chain_role == "primary" else [other, codex]
        manager.register_agent(AgentConfig(name="David", cli=chain[0].cli, clis=chain))
        manager.session_manager.save_session(
            "David", AgentCLI.CODEX, "existing-codex", "retry-stream", "develop"
        )
        continuation = (
            SessionContinuation.resume_exact(AgentCLI.CODEX, "existing-codex")
            if chain_role == "primary"
            else SessionContinuation.auto()
        )
        error = AgentExecutionError(
            "Codex ended before reporting completion", error_type="incomplete_stream"
        )
        attempts = []
        other_attempts = []
        allowed_tools = ["Read"]
        allowed_directories = [str(tmp_path)]
        stream_path = str(tmp_path / "stream.jsonl")

        def execute(executor, prompt, tools, directories, stream, **kwargs):
            assert prompt.endswith("continue saved work")
            assert (tools, directories, stream) == (allowed_tools, allowed_directories, stream_path)
            if executor.config.cli != AgentCLI.CODEX:
                assert chain_role == "fallback"
                other_attempts.append(executor.config.cli)
                raise AgentExecutionError("CLI missing", error_type="cli_not_found")
            assert executor.config.session_id == "existing-codex"
            assert executor.config.model == "codex-model"
            assert kwargs.get("exact_session", False) == (chain_role == "primary")
            attempts.append(executor)
            if len(attempts) <= failures:
                raise error
            return AgentResponse(response="completed", token_usage=TokenUsage())

        with (
            patch.object(AgentExecutor, "execute", new=execute),
            patch("cafe.agents.manager.time.sleep") as sleep,
        ):
            if failures == 3:
                with pytest.raises(AgentExecutionError) as raised:
                    manager.execute(
                        "David", "continue saved work", allowed_tools, allowed_directories,
                        stream_path, phase_name="develop", continuation=continuation,
                    )
                assert raised.value is error
            else:
                result = manager.execute(
                    "David", "continue saved work", allowed_tools, allowed_directories,
                    stream_path, phase_name="develop", continuation=continuation,
                )
                assert result[0] == "completed"

        assert len(attempts) == min(failures + 1, 3)
        assert all(executor is attempts[0] for executor in attempts)
        assert other_attempts == ([AgentCLI.CLAUDE] if chain_role == "fallback" else [])
        assert [call.args[0] for call in sleep.call_args_list] == [30, 120][:failures]
        recorded = manager.get_failed_attempts()
        if chain_role == "fallback":
            assert recorded.pop(0)["error_type"] == "cli_not_found"
        assert [(item["cli"], item["chain_role"], item["attempt"], item["error_type"])
                for item in recorded] == [
            ("codex", chain_role, attempt, "incomplete_stream")
            for attempt in range(1, failures + 1)
        ]

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

    def test_execution_config_reuses_only_same_phase_session(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        session_mgr = SessionManager(str(tmp_path / "sessions"))
        session_mgr.save_session(
            "David", AgentCLI.CODEX, "plan-session", "issue365", "plan"
        )
        session_mgr.save_session(
            "David", AgentCLI.CODEX, "develop-session", "issue365", "develop"
        )
        manager = AgentManager(session_manager=session_mgr, issue_name="issue365")
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CODEX))

        plan_config = manager.get_execution_config("David", phase_name="plan")
        develop_config = manager.get_execution_config("David", phase_name="develop")
        review_config = manager.get_execution_config("David", phase_name="review")

        assert plan_config.session_id == "plan-session"
        assert develop_config.session_id == "develop-session"
        assert review_config.session_id is None

    def test_execute_persists_session_under_current_phase(self) -> None:
        from cafe.core.types import AgentResponse, TokenUsage

        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None
        manager = AgentManager(session_manager=session_mgr, issue_name="issue365")
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CODEX))

        with patch.object(
            AgentExecutor,
            "execute",
            return_value=AgentResponse(
                response="done",
                token_usage=TokenUsage(),
                cli=AgentCLI.CODEX,
                session_id="develop-session",
            ),
        ):
            manager.execute("David", "prompt", phase_name="develop")

        session_mgr.save_session.assert_called_once_with(
            "David",
            AgentCLI.CODEX,
            "develop-session",
            "issue365",
            "develop",
        )

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
        local_agent.write_text(
            "---\nname: Roger\ndescription: local\n---\n\n# Roger (local)\n"
        )

        # Set up global agent
        global_home = tmp_path / "global_home"
        global_agent = global_home / ".cafe" / "agents" / "pm" / "Roger.md"
        global_agent.parent.mkdir(parents=True)
        global_agent.write_text(
            "---\nname: Roger\ndescription: global\n---\n\n# Roger (global)\n"
        )

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
        global_agent.write_text(
            "---\nname: Roger\ndescription: global\n---\n\n# Roger (global)\n"
        )

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

        resolved_path = RealPath(result)
        assert resolved_path.is_absolute()
        assert resolved_path.is_file()

    def test_reads_builtin_agent_outside_source_checkout(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Builtin guidance remains readable when CWD is outside the source tree."""
        from pathlib import Path as RealPath

        working_dir = tmp_path / "working"
        working_dir.mkdir()
        monkeypatch.chdir(working_dir)

        with patch.object(RealPath, "home", return_value=tmp_path / "empty-home"):
            path, content = AgentManager.read_agent_file("David", "developer")

        resolved_path = RealPath(path)
        assert resolved_path.is_absolute()
        assert resolved_path.is_file()
        assert content.strip()

    def test_read_agent_file_never_substitutes_empty_invalid_guidance(
        self, tmp_path
    ) -> None:
        from cafe.catalogs.resolver import CatalogValidationError

        project = tmp_path / "project"
        invalid = project / ".cafe" / "agents" / "developer" / "David.md"
        invalid.parent.mkdir(parents=True)
        invalid.write_bytes(b"\xff\xfeinvalid-agent")

        with pytest.raises(CatalogValidationError):
            AgentManager.read_agent_file(
                "David", "developer", str(project / ".cafe")
            )
