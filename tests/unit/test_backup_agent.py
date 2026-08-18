"""Tests for backup agent feature."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import StringIO

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CriticalPhaseError, TokenUsage


class TestAgentConfigBackupFields:
    """Tests for backup-related fields in AgentConfig."""

    def test_backup_clis_default_is_empty_list(self) -> None:
        """Test that backup_clis defaults to an empty list."""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        assert config.backup_clis == []

    def test_backup_clis_accepts_list_of_agent_cli(self) -> None:
        """Test that backup_clis accepts a list of AgentCLI values."""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        assert config.backup_clis == [AgentCLI.GEMINI, AgentCLI.COPILOT]

    def test_models_config_default_is_empty_dict(self) -> None:
        """Test that models_config defaults to an empty dict."""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        assert config.models_config == {}

    def test_models_config_accepts_dict_of_dicts(self) -> None:
        """Test that models_config accepts a nested dict structure."""
        models = {
            "claude": {"plan": "opus", "develop": "sonnet"},
            "gemini": {"plan": "gemini-3-flash-preview", "develop": "gemini-3-flash-preview"},
        }
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE, models_config=models)
        assert config.models_config["claude"]["plan"] == "opus"
        assert config.models_config["gemini"]["develop"] == "gemini-3-flash-preview"

    def test_existing_fields_still_work(self) -> None:
        """Test that adding new fields does not break existing fields."""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="session-123",
            model="opus",
            backup_clis=[AgentCLI.GEMINI],
            models_config={"claude": {"plan": "opus"}},
        )
        assert config.name == "David"
        assert config.cli == AgentCLI.CLAUDE
        assert config.session_id == "session-123"
        assert config.model == "opus"


class TestAgentManagerBackupRetry:
    """Tests for AgentManager backup agent retry logic."""

    def _make_success_response(self, text: str = "success") -> AgentResponse:
        return AgentResponse(response=text, token_usage=TokenUsage())

    def _make_rate_limit_error(self) -> AgentExecutionError:
        return AgentExecutionError("rate limit reached", error_type="rate_limit")

    def _make_cli_not_found_error(self) -> AgentExecutionError:
        return AgentExecutionError("cli not found", error_type="cli_not_found")

    def test_primary_succeeds_no_backup_needed(self) -> None:
        """Test that backup is not triggered when the primary agent succeeds."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.return_value = self._make_success_response()
            response, *_ = manager.execute("David", "test prompt")

        assert response == "success"
        assert mock_exec.call_count == 1

    def test_rate_limit_triggers_first_backup(self) -> None:
        """Test that a rate limit on the primary agent automatically switches to the first backup."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"develop": "gemini-3-flash"}},
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise self._make_rate_limit_error()
            return self._make_success_response("backup success")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt", phase_name="develop")

        assert response == "backup success"

    def test_backup_receives_fresh_takeover_context_without_primary_session(self) -> None:
        manager = AgentManager()
        manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE, backup_clis=[AgentCLI.GEMINI])
        )
        seen_prompts = []

        def side_effect(prompt, *args, **kwargs):
            seen_prompts.append(prompt)
            if len(seen_prompts) == 1:
                raise self._make_rate_limit_error()
            return self._make_success_response("backup success")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute(
                "David",
                "primary prompt",
                backup_context_callback=lambda _error: '{"operation":{"state":"running"}}',
            )

        assert response == "backup success"
        assert len(seen_prompts) == 2
        assert "Cold backup takeover context" in seen_prompts[1]
        assert '"operation":{"state":"running"}' in seen_prompts[1]
        assert "session-" not in seen_prompts[1]

    def test_each_backup_receives_the_immediately_preceding_failure(self) -> None:
        """A later cold backup must see the failed backup, not stale primary context."""
        manager = AgentManager()
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CLAUDE,
                backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
            )
        )
        primary_error = self._make_rate_limit_error()
        backup_error = AgentExecutionError("backup rate limit", error_type="rate_limit")
        callback_errors = []
        execution_calls = 0

        def side_effect(*_args, **_kwargs):
            nonlocal execution_calls
            execution_calls += 1
            if execution_calls == 1:
                raise primary_error
            if execution_calls == 2:
                raise backup_error
            return self._make_success_response("second backup success")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute(
                "David",
                "primary prompt",
                backup_context_callback=callback_errors.append,
            )

        assert response == "second backup success"
        assert callback_errors == [primary_error, backup_error]

    def test_backup_is_not_started_when_takeover_snapshot_cannot_be_built(self) -> None:
        manager = AgentManager()
        manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE, backup_clis=[AgentCLI.GEMINI])
        )

        with patch(
            "cafe.agents.executor.AgentExecutor.execute", side_effect=self._make_rate_limit_error()
        ) as execute:
            with pytest.raises(AgentExecutionError, match="takeover context unavailable"):
                manager.execute(
                    "David",
                    "primary prompt",
                    backup_context_callback=lambda _error: (_ for _ in ()).throw(
                        OSError("snapshot unavailable")
                    ),
                )

        assert execute.call_count == 1

    def test_first_backup_fails_second_succeeds(self) -> None:
        """Test that when the first backup also fails, the second backup is tried."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise self._make_rate_limit_error()
            return self._make_success_response("second backup success")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt")

        assert response == "second backup success"
        assert call_count == 3

    def test_all_agents_fail_raises_error(self) -> None:
        """Test that AgentExecutionError is raised when all agents fail."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = self._make_rate_limit_error()

            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "test prompt")

        assert exc_info.value.error_type == "rate_limit"

    def test_no_backup_configured_rate_limit_raises_immediately(self) -> None:
        """Test that rate limit raises immediately when no backup agents are configured."""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)  # no backup_clis
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = self._make_rate_limit_error()

            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "test prompt")

        assert exc_info.value.error_type == "rate_limit"
        assert mock_exec.call_count == 1

    def test_duplicate_cli_in_backup_is_skipped(self) -> None:
        """Test that duplicate CLIs in the backup list are only attempted once."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.GEMINI],  # duplicate GEMINI
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise self._make_rate_limit_error()
            return self._make_success_response()

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            manager.execute("David", "test prompt")

        # Primary + 1 GEMINI (duplicate not retried)
        assert call_count == 2

    def test_non_rate_limit_error_not_retried(self) -> None:
        """Test that non-rate-limit errors do not trigger backup retry."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = AgentExecutionError(
                "permission denied", error_type="permission_denied"
            )

            with pytest.raises(AgentExecutionError):
                manager.execute("David", "test prompt")

        # Only attempted once, no backup triggered
        assert mock_exec.call_count == 1

    def test_backup_uses_phase_specific_model(self) -> None:
        """Test that backup agent uses the phase-specific model configuration."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"plan": "gemini-3-flash-preview"}},
        )
        manager.register_agent(config)

        created_executors = []

        original_init = __import__(
            "cafe.agents.executor", fromlist=["AgentExecutor"]
        ).AgentExecutor.__init__

        def capture_executor(self, config):
            created_executors.append(config)
            original_init(self, config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            return AgentResponse(response="ok", token_usage=TokenUsage())

        with (
            patch("cafe.agents.executor.AgentExecutor.__init__", capture_executor),
            patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect),
        ):
            manager.execute("David", "test prompt", phase_name="plan")

        # The second executor should be the backup CLI (gemini) with the correct model
        backup_configs = [c for c in created_executors if c.cli == AgentCLI.GEMINI]
        assert len(backup_configs) >= 1
        assert backup_configs[0].model == "gemini-3-flash-preview"

    def test_backup_model_missing_defaults_to_none(self) -> None:
        """Test that backup agent model defaults to None when not configured."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={},  # no model configuration
        )
        manager.register_agent(config)

        created_configs = []
        original_init = __import__(
            "cafe.agents.executor", fromlist=["AgentExecutor"]
        ).AgentExecutor.__init__

        def capture_executor(self, config):
            created_configs.append(config)
            original_init(self, config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            return AgentResponse(response="ok", token_usage=TokenUsage())

        with (
            patch("cafe.agents.executor.AgentExecutor.__init__", capture_executor),
            patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect),
        ):
            manager.execute("David", "test prompt", phase_name="develop")

        backup_configs = [c for c in created_configs if c.cli == AgentCLI.GEMINI]
        assert len(backup_configs) >= 1
        assert backup_configs[0].model is None

    def test_cli_not_found_backup_is_skipped(self) -> None:
        """Test that a backup agent with cli_not_found is skipped, continuing to the next backup."""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            if call_count == 2:
                raise AgentExecutionError("cli not found", error_type="cli_not_found")
            return AgentResponse(response="copilot success", token_usage=TokenUsage())

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt")

        assert response == "copilot success"
        assert call_count == 3


class TestRateLimitErrorDisplay:
    """Tests for rate limit error display messages."""

    def _make_critical_rate_limit_error(self, message: str) -> CriticalPhaseError:
        return CriticalPhaseError(message=message, error_type="rate_limit", phase_name="develop")

    def test_rate_limit_suggests_backup_config_when_no_backups(self, capsys) -> None:
        """Test that the error message suggests configuring backup agents when none are set."""
        import typer
        from cafe.ui.cli import _handle_phase_exception

        error = self._make_critical_rate_limit_error("rate limit reached")
        with pytest.raises(typer.Exit):
            _handle_phase_exception(error, "develop")

        captured = capsys.readouterr()
        assert ".cafe/phases.yaml" in captured.out

    def test_rate_limit_shows_tried_agents_when_all_exhausted(self, capsys) -> None:
        """Test that the error message shows the list of tried agents when all are exhausted."""
        import typer
        from cafe.ui.cli import _handle_phase_exception

        message = (
            "All agents failed. Tried: claude (rate limit reached), gemini (rate limit reached). "
            "Please wait for rate limits to reset or add more backup agents."
        )
        error = self._make_critical_rate_limit_error(message)
        with pytest.raises(typer.Exit):
            _handle_phase_exception(error, "develop")

        captured = capsys.readouterr()
        # Should show that multiple agents were tried
        assert "claude" in captured.out or "All agents failed" in captured.out
