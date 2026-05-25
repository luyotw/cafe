"""Tests for clis-chain-based fallback in AgentManager."""

import pytest
from unittest.mock import patch

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CliEntry, TokenUsage


def _ok(text: str = "ok") -> AgentResponse:
    return AgentResponse(response=text, token_usage=TokenUsage())


def _rate_limit() -> AgentExecutionError:
    return AgentExecutionError("rate limit", error_type="rate_limit")


def _cli_not_found() -> AgentExecutionError:
    return AgentExecutionError("cli not found", error_type="cli_not_found")


def _cli_unavailable() -> AgentExecutionError:
    return AgentExecutionError("subscription disabled", error_type="cli_unavailable")


def _model_not_found() -> AgentExecutionError:
    return AgentExecutionError("bad model", error_type="model_not_found")


def _make_manager(clis: list) -> AgentManager:
    manager = AgentManager()
    config = AgentConfig(
        name="David",
        cli=clis[0].cli,
        model=clis[0].model,
        clis=clis,
        backup_clis=[e.cli for e in clis[1:]],
    )
    manager.register_agent(config)
    return manager


class TestClisChainFallbackBasic:
    """Basic fallback scenarios driven by the clis chain."""

    def test_single_entry_chain_rate_limit_reraises(self) -> None:
        """Chain of length 1 with rate_limit re-raises immediately (no fallback)."""
        manager = _make_manager([CliEntry(cli=AgentCLI.CLAUDE, model="opus")])
        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=_rate_limit()):
            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "prompt")
        assert exc_info.value.error_type == "rate_limit"

    def test_two_entry_chain_fallback_succeeds(self) -> None:
        """Primary rate_limit, second entry succeeds."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _rate_limit()
            return _ok("gemini response")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "gemini response"
        assert call_count == 2
        assert manager.get_last_cli() == AgentCLI.GEMINI

    def test_three_entry_chain_first_two_fail(self) -> None:
        """Primary and first fallback fail, second fallback succeeds."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
            CliEntry(cli=AgentCLI.COPILOT),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise _rate_limit()
            return _ok("copilot response")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "copilot response"
        assert call_count == 3

    def test_all_chain_entries_fail_raises_error_with_cli_list(self) -> None:
        """All entries fail; raised error mentions all tried CLIs."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
        ])
        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=_rate_limit()):
            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "prompt")

        assert exc_info.value.error_type == "rate_limit"
        msg = str(exc_info.value)
        assert "claude" in msg.lower()
        assert "gemini" in msg.lower()

    def test_non_rate_limit_error_not_continued(self) -> None:
        """Non-rate-limit error on a fallback entry stops the chain immediately."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
            CliEntry(cli=AgentCLI.COPILOT),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _rate_limit()
            raise AgentExecutionError("permission denied", error_type="permission_denied")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "prompt")

        # Stopped at gemini, did not reach copilot
        assert call_count == 2
        assert exc_info.value.error_type == "permission_denied"

    def test_cli_not_found_also_triggers_fallback(self) -> None:
        """cli_not_found is treated the same as rate_limit for chain traversal."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _cli_not_found()
            return _ok("gemini ok")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "gemini ok"

    def test_cli_unavailable_also_triggers_fallback(self) -> None:
        """Account or org-policy CLI unavailability should try the next configured CLI."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.CODEX),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _cli_unavailable()
            return _ok("codex ok")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "codex ok"
        assert call_count == 2

    def test_model_not_found_also_triggers_fallback(self) -> None:
        """Bad model configuration on one CLI should try the next configured CLI."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE, model="bad-claude-model"),
            CliEntry(cli=AgentCLI.CODEX, model="gpt-5.3-codex"),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _model_not_found()
            return _ok("codex ok")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "codex ok"
        assert call_count == 2

    def test_fallback_prints_original_error_details(self, capsys) -> None:
        """Fallback logging should include the raw CLI error immediately."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.CODEX),
            CliEntry(cli=AgentCLI.CURSOR),
        ])
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError(
                    "Claude execution failed: oauth_org_not_allowed",
                    error_type="cli_unavailable",
                )
            if call_count == 2:
                raise AgentExecutionError(
                    "Codex execution failed: You've hit your usage limit. Try again at May 26th, 2026 1:20 AM.",
                    error_type="rate_limit",
                )
            return _ok("cursor ok")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        captured = capsys.readouterr().out
        assert response == "cursor ok"
        assert "Original error:" in captured
        assert "Claude execution failed: oauth_org_not_allowed" in captured
        assert "Codex execution failed: You've hit your usage limit" in captured


class TestClisChainModelResolution:
    """Tests that verify CliEntry.resolve_model is used for each fallback entry."""

    def _capture_executor_configs(self, chain: list, phase_name: str):
        """Run a simulated fallback and return configs used per executor call."""
        manager = _make_manager(chain)
        created_configs = []
        original_init = __import__(
            "cafe.agents.executor", fromlist=["AgentExecutor"]
        ).AgentExecutor.__init__

        def capture_init(self, config):
            created_configs.append(config)
            original_init(self, config)

        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _rate_limit()
            return _ok()

        with (
            patch("cafe.agents.executor.AgentExecutor.__init__", capture_init),
            patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect),
        ):
            manager.execute("David", "prompt", phase_name=phase_name)

        return created_configs

    def test_fallback_entry_uses_phase_model_override(self) -> None:
        """Fallback executor is built with the phase-specific model from CliEntry."""
        chain = [
            CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
            CliEntry(cli=AgentCLI.GEMINI, model="gemini-2.5-pro",
                     phase_models={"develop": "gemini-2-flash"}),
        ]
        configs = self._capture_executor_configs(chain, phase_name="develop")
        gemini_configs = [c for c in configs if c.cli == AgentCLI.GEMINI]
        assert len(gemini_configs) >= 1
        assert gemini_configs[0].model == "gemini-2-flash"

    def test_fallback_entry_falls_back_to_entry_model_when_no_phase_override(self) -> None:
        """If no phase override, fallback entry uses its own model."""
        chain = [
            CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
            CliEntry(cli=AgentCLI.GEMINI, model="gemini-2.5-pro"),
        ]
        configs = self._capture_executor_configs(chain, phase_name="plan")
        gemini_configs = [c for c in configs if c.cli == AgentCLI.GEMINI]
        assert gemini_configs[0].model == "gemini-2.5-pro"

    def test_fallback_entry_uses_none_when_no_model_at_all(self) -> None:
        """CliEntry with no model and no phase override results in model=None."""
        chain = [
            CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
            CliEntry(cli=AgentCLI.COPILOT),
        ]
        configs = self._capture_executor_configs(chain, phase_name="develop")
        copilot_configs = [c for c in configs if c.cli == AgentCLI.COPILOT]
        assert copilot_configs[0].model is None
