"""Tests for clis-chain-based fallback in AgentManager."""

import pytest
from unittest.mock import patch
from pathlib import Path
import json

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


def _transient_cli_unavailable() -> AgentExecutionError:
    return AgentExecutionError(
        "socket connection was closed unexpectedly",
        error_type="cli_unavailable",
    )


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

    def test_primary_transient_error_retries_once_before_fallback(self) -> None:
        """A known transient primary error gets one same-CLI retry before fallback."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
        ])
        errors = [_transient_cli_unavailable(), _transient_cli_unavailable()]

        def side_effect(*args, **kwargs):
            if errors:
                raise errors.pop(0)
            return _ok("gemini response")

        with patch(
            "cafe.agents.executor.AgentExecutor.execute",
            side_effect=side_effect,
        ) as execute:
            response, *_ = manager.execute("David", "unchanged prompt")

        assert response == "gemini response"
        assert execute.call_count == 3
        assert manager.get_failed_attempts() == [
            {
                "cli": "claude",
                "chain_role": "primary",
                "attempt": 1,
                "error_type": "cli_unavailable",
                "error_excerpt": "socket connection was closed unexpectedly",
            },
            {
                "cli": "claude",
                "chain_role": "primary",
                "attempt": 2,
                "error_type": "cli_unavailable",
                "error_excerpt": "socket connection was closed unexpectedly",
            },
        ]

    def test_fallback_transient_error_retries_once_without_reordering_chain(self) -> None:
        """Each fallback gets one bounded retry before the next configured CLI."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
            CliEntry(cli=AgentCLI.COPILOT),
        ])
        errors = [_rate_limit(), _transient_cli_unavailable(), _transient_cli_unavailable()]

        def side_effect(*args, **kwargs):
            if errors:
                raise errors.pop(0)
            return _ok("copilot response")

        with patch(
            "cafe.agents.executor.AgentExecutor.execute",
            side_effect=side_effect,
        ) as execute:
            response, *_ = manager.execute("David", "unchanged prompt")

        assert response == "copilot response"
        assert execute.call_count == 4
        assert [record["cli"] for record in manager.get_failed_attempts()] == [
            "claude",
            "gemini",
            "gemini",
        ]
        assert [record["attempt"] for record in manager.get_failed_attempts()] == [1, 1, 2]

    @pytest.mark.parametrize(
        ("error", "expected_type"),
        [
            (_rate_limit(), "rate_limit"),
            (_cli_not_found(), "cli_not_found"),
            (_model_not_found(), "model_not_found"),
            (_cli_unavailable(), "cli_unavailable"),
            (AgentExecutionError("HTTP 403", error_type="cli_unavailable"), "cli_unavailable"),
        ],
    )
    def test_non_transient_errors_do_not_receive_same_cli_retry(
        self,
        error: AgentExecutionError,
        expected_type: str,
    ) -> None:
        """Fallbackable, non-transient errors preserve the existing one-call behavior."""
        manager = _make_manager([
            CliEntry(cli=AgentCLI.CLAUDE),
            CliEntry(cli=AgentCLI.GEMINI),
        ])

        with patch(
            "cafe.agents.executor.AgentExecutor.execute",
            side_effect=[error, _ok("fallback response")],
        ) as execute:
            response, *_ = manager.execute("David", "prompt")

        assert response == "fallback response"
        assert execute.call_count == 2
        assert manager.get_failed_attempts()[0]["error_type"] == expected_type

    def test_failed_attempt_diagnostics_reset_for_each_execute(self) -> None:
        """A later execution cannot inherit diagnostics from an earlier failure."""
        manager = _make_manager([CliEntry(cli=AgentCLI.CLAUDE)])

        with patch(
            "cafe.agents.executor.AgentExecutor.execute",
            side_effect=[_transient_cli_unavailable(), _ok("retried")],
        ):
            response, *_ = manager.execute("David", "prompt")

        assert response == "retried"
        assert len(manager.get_failed_attempts()) == 1

        with patch(
            "cafe.agents.executor.AgentExecutor.execute",
            return_value=_ok("clean execution"),
        ):
            response, *_ = manager.execute("David", "another prompt")

        assert response == "clean execution"
        assert manager.get_failed_attempts() == []

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

    def test_active_cli_is_preferred_when_saved(self, tmp_path, monkeypatch) -> None:
        """Last successful CLI is preferred even when not primary."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CLAUDE,
                model="opus",
                clis=[
                    CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
                    CliEntry(cli=AgentCLI.GEMINI),
                ],
            )
        )
        manager.session_manager.save_session("David", AgentCLI.GEMINI, "gemini-session-xyz", "issue-1")
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps({"David": {"cli": "gemini", "model": None, "configured_primary": "claude", "updated_at": "2026-06-13T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        created_configs = []
        original_init = __import__(
            "cafe.agents.executor", fromlist=["AgentExecutor"]
        ).AgentExecutor.__init__

        def capture_init(self, config):
            created_configs.append(config)
            original_init(self, config)

        with patch(
            "cafe.agents.executor.AgentExecutor.__init__",
            capture_init,
        ), patch(
            "cafe.agents.executor.AgentExecutor.execute",
            return_value=_ok("fallback"),
        ):
            response, *_ = manager.execute("David", "prompt")

        assert response == "fallback"
        assert len(created_configs) == 1
        assert created_configs[0].cli == AgentCLI.GEMINI
        assert created_configs[0].session_id == "gemini-session-xyz"

    def test_stale_active_cli_falls_back_to_configured_primary(self, tmp_path, monkeypatch) -> None:
        """Active CLI not in chain is ignored and fallback order stays configured."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CLAUDE,
                model="opus",
                clis=[
                    CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
                    CliEntry(cli=AgentCLI.GEMINI),
                ],
            )
        )
        manager.session_manager.save_session("David", AgentCLI.GEMINI, "gemini-session-abc", "issue-1")
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps({"David": {"cli": "cursor", "model": None, "updated_at": "2026-06-13T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        executed_clis: list[AgentCLI] = []

        def side_effect(*args, **kwargs):
            if args and hasattr(args[0], "config"):
                executed_clis.append(args[0].config.cli)
            elif args:
                # Patch signature can vary depending on how AgentExecutor.execute is
                # mocked (class-level or bound-method patching), so infer by index.
                executed_clis.append(AgentCLI.GEMINI if len(executed_clis) == 1 else AgentCLI.CLAUDE)
            else:
                executed_clis.append(AgentCLI.CLAUDE)

            if len(executed_clis) == 1:
                raise _rate_limit()
            return _ok("fallback-ok")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "prompt")

        assert response == "fallback-ok"
        assert executed_clis == [AgentCLI.CLAUDE, AgentCLI.GEMINI]

    def test_changed_crew_primary_overrides_stale_sticky_cli(self, tmp_path, monkeypatch) -> None:
        """A crew.yaml primary change beats the recorded sticky CLI (was: sticky always won)."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        # Crew now lists codex first; the sticky record was written when copilot was primary.
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CODEX,
                model="gpt-5.5",
                clis=[
                    CliEntry(cli=AgentCLI.CODEX, model="gpt-5.5"),
                    CliEntry(cli=AgentCLI.COPILOT),
                ],
            )
        )
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps({"David": {"cli": "copilot", "model": None, "configured_primary": "copilot", "updated_at": "2026-07-07T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        execution = manager.get_execution_config("David", phase_name="build")
        assert execution.cli == AgentCLI.CODEX

    def test_stale_chain_signature_is_ignored(self, tmp_path, monkeypatch) -> None:
        """A stale chain fingerprint does not promote a non-configured sticky CLI."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CODEX,
                model="gpt-5.5",
                clis=[
                    CliEntry(cli=AgentCLI.CODEX, model="gpt-5.5"),
                    CliEntry(cli=AgentCLI.GEMINI),
                ],
            )
        )
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps(
                {
                    "David": {
                        "cli": "gemini",
                        "model": None,
                        "configured_primary": "codex",
                        "chain": ["copilot", "gemini"],
                        "updated_at": "2026-06-13T00:00:00+08:00",
                    }
                },
            ),
            encoding="utf-8",
        )

        execution = manager.get_execution_config("David", phase_name="develop")
        assert execution.cli == AgentCLI.CODEX
        assert execution.model == "gpt-5.5"

    def test_chain_signature_includes_models_for_sticky_invalidation(
        self, tmp_path, monkeypatch
    ) -> None:
        """A model change in the effective chain resets sticky CLI promotion."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CODEX,
                model="old-codex",
                clis=[
                    CliEntry(cli=AgentCLI.CODEX, model="old-codex"),
                    CliEntry(cli=AgentCLI.GEMINI, model=None),
                ],
            )
        )
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps(
                {
                    "David": {
                        "cli": "gemini",
                        "model": None,
                        "configured_primary": "codex",
                        "chain": [
                            {"cli": "codex", "model": "legacy-codex"},
                            {"cli": "gemini", "model": None},
                        ],
                        "updated_at": "2026-06-13T00:00:00+08:00",
                    }
                }
            ),
            encoding="utf-8",
        )

        execution = manager.get_execution_config("David", phase_name="develop")
        assert execution.cli == AgentCLI.CODEX

    def test_legacy_record_without_configured_primary_does_not_override_primary(self, tmp_path, monkeypatch) -> None:
        """Pre-fix records (no configured_primary) no longer demote the crew primary."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CODEX,
                model="gpt-5.5",
                clis=[
                    CliEntry(cli=AgentCLI.CODEX, model="gpt-5.5"),
                    CliEntry(cli=AgentCLI.COPILOT),
                ],
            )
        )
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps({"David": {"cli": "copilot", "model": None, "updated_at": "2026-07-07T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        execution = manager.get_execution_config("David", phase_name="build")
        assert execution.cli == AgentCLI.CODEX

    def test_active_cli_without_model_does_not_inherit_base_model(self, tmp_path, monkeypatch) -> None:
        """Active CLI model=None keeps CLI-specific model resolution, not base model."""
        monkeypatch.chdir(tmp_path)
        manager = AgentManager(issue_name="issue-1")
        manager.register_agent(
            AgentConfig(
                name="David",
                cli=AgentCLI.CLAUDE,
                model="base-claude",
                clis=[
                    CliEntry(cli=AgentCLI.CLAUDE, model="claude-model"),
                    CliEntry(cli=AgentCLI.GEMINI),
                ],
            )
        )
        active_file = Path(".cafe/issues/issue-1/active_clis.json")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(
            json.dumps({"David": {"cli": "gemini", "model": None, "configured_primary": "claude", "updated_at": "2026-06-13T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        created_configs = []
        original_init = __import__(
            "cafe.agents.executor", fromlist=["AgentExecutor"]
        ).AgentExecutor.__init__

        def capture_init(self, config):
            created_configs.append(config)
            original_init(self, config)

        with (
            patch("cafe.agents.executor.AgentExecutor.__init__", capture_init),
            patch("cafe.agents.executor.AgentExecutor.execute", return_value=_ok("ok")),
        ):
            response, *_ = manager.execute("David", "prompt")

        assert response == "ok"
        assert len(created_configs) == 1
        assert created_configs[0].cli == AgentCLI.GEMINI
        assert created_configs[0].model is None

    def test_session_lookup_is_isolated_by_issue_name(self, tmp_path) -> None:
        """Session lookup for fallback continuation stays issue-local."""
        manager_issue_1 = AgentManager(issue_name="issue-1")
        manager_issue_2 = AgentManager(issue_name="issue-2")
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            model="opus",
            clis=[
                CliEntry(cli=AgentCLI.CLAUDE, model="opus"),
                CliEntry(cli=AgentCLI.GEMINI),
            ],
        )
        manager_issue_1.register_agent(config)
        manager_issue_2.register_agent(config)

        manager_issue_1.session_manager.save_session(
            "David", AgentCLI.GEMINI, "session-issue-1", "issue-1", "develop"
        )
        manager_issue_2.session_manager.save_session(
            "David", AgentCLI.GEMINI, "session-issue-2", "issue-2", "develop"
        )

        issue_1_dir = Path(".cafe/issues/issue-1")
        issue_2_dir = Path(".cafe/issues/issue-2")
        issue_1_dir.mkdir(parents=True, exist_ok=True)
        issue_2_dir.mkdir(parents=True, exist_ok=True)
        (issue_1_dir / "active_clis.json").write_text(
            json.dumps({"David": {"cli": "gemini", "model": None, "configured_primary": "claude", "updated_at": "2026-06-13T00:00:00+08:00"}}),
            encoding="utf-8",
        )
        (issue_2_dir / "active_clis.json").write_text(
            json.dumps({"David": {"cli": "gemini", "model": None, "configured_primary": "claude", "updated_at": "2026-06-13T00:00:00+08:00"}}),
            encoding="utf-8",
        )

        execution_1 = manager_issue_1.get_execution_config("David", phase_name="develop")
        execution_2 = manager_issue_2.get_execution_config("David", phase_name="develop")
        assert execution_1.session_id == "session-issue-1"
        assert execution_2.session_id == "session-issue-2"


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
