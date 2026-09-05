"""Tests for explicit workflow session-continuation policy."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.agents.cli.codex import CodexCLI
from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.phase import Phase
from cafe.core.session_continuation import (
    SessionContinuation,
    SessionContinuationPolicy,
    exact_continuation_from_context,
)
from cafe.core.types import (
    AgentCLI,
    AgentConfig,
    AgentResponse,
    CliEntry,
    CriticalPhaseError,
    TokenUsage,
)


def _manager(tmp_path: Path, monkeypatch) -> AgentManager:
    monkeypatch.chdir(tmp_path)
    manager = AgentManager(issue_name="issue-381")
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CODEX,
            clis=[
                CliEntry(cli=AgentCLI.CODEX, model="codex-model"),
                CliEntry(cli=AgentCLI.GEMINI, model="gemini-model"),
            ],
        )
    )
    manager.session_manager.save_session(
        "David",
        AgentCLI.CODEX,
        "persisted-codex",
        "issue-381",
        "develop",
    )
    manager.session_manager.save_session(
        "David",
        AgentCLI.GEMINI,
        "persisted-gemini",
        "issue-381",
        "develop",
    )
    return manager


def test_auto_preserves_legacy_persisted_session(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    config = manager.get_execution_config(
        "David",
        phase_name="develop",
        continuation=SessionContinuation.auto(),
    )

    assert config.cli == AgentCLI.CODEX
    assert config.session_id == "persisted-codex"


def test_new_ignores_persisted_primary_session(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    config = manager.get_execution_config(
        "David",
        phase_name="develop",
        continuation=SessionContinuation.new(),
    )

    assert config.cli == AgentCLI.CODEX
    assert config.session_id is None
    assert "resume" not in CodexCLI(config).build_command("prompt")


def test_resume_exact_beats_primary_and_sticky_order(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    config = manager.get_execution_config(
        "David",
        phase_name="develop",
        continuation=SessionContinuation.resume_exact(
            AgentCLI.GEMINI,
            "exact-gemini",
        ),
    )

    assert config.cli == AgentCLI.GEMINI
    assert config.session_id == "exact-gemini"
    assert [entry.cli for entry in config.clis] == [AgentCLI.GEMINI, AgentCLI.CODEX]


def test_resume_exact_builds_codex_resume_command(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    config = manager.get_execution_config(
        "David",
        phase_name="develop",
        continuation=SessionContinuation.resume_exact(
            AgentCLI.CODEX,
            "exact-codex",
        ),
    )

    command = CodexCLI(config).build_command("prompt")

    assert command[command.index("exec") : command.index("exec") + 3] == [
        "exec",
        "resume",
        "exact-codex",
    ]


def test_unconfigured_exact_cli_fails_closed(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not configured"):
        manager.get_execution_config(
            "David",
            phase_name="develop",
            continuation=SessionContinuation.resume_exact(
                AgentCLI.CLAUDE,
                "removed-session",
            ),
        )


def test_empty_execution_chain_fails_closed_for_new(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    manager.get_agent("David").config.session_id = "sticky-codex"

    with patch.object(manager, "_resolve_execution_chain", return_value=[]):
        config = manager.get_execution_config(
            "David",
            phase_name="develop",
            continuation=SessionContinuation.new(),
        )

    assert config.session_id is None


def test_resolution_error_fails_closed_for_new(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    manager.get_agent("David").config.session_id = "sticky-codex"
    attempted_sessions: list[str | None] = []

    def execute(self, *args, **kwargs):
        attempted_sessions.append(self.config.session_id)
        return AgentResponse(
            response="fresh",
            token_usage=TokenUsage(),
            cli=AgentCLI.CODEX,
            session_id="new-codex",
        )

    with (
        patch.object(
            manager,
            "get_execution_config",
            side_effect=RuntimeError("resolution failed"),
        ),
        patch("cafe.agents.executor.AgentExecutor.execute", execute),
    ):
        response, *_ = manager.execute(
            "David",
            "prompt",
            phase_name="develop",
            continuation=SessionContinuation.new(),
        )

    assert response == "fresh"
    assert attempted_sessions == [None]


def test_exact_backup_execution_does_not_change_next_new_primary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    def execute(self, *args, **kwargs):
        return AgentResponse(
            response="exact",
            token_usage=TokenUsage(),
            cli=self.config.cli,
            session_id="actual-session",
        )

    with patch("cafe.agents.executor.AgentExecutor.execute", execute):
        response, *_ = manager.execute(
            "David",
            "prompt",
            phase_name="develop",
            continuation=SessionContinuation.resume_exact(
                AgentCLI.GEMINI,
                "exact-gemini",
            ),
        )

    base = manager.get_agent("David").config
    next_new = manager.get_execution_config(
        "David",
        phase_name="develop",
        continuation=SessionContinuation.new(),
    )

    assert response == "exact"
    assert base.cli == AgentCLI.CODEX
    assert [entry.cli for entry in base.clis] == [AgentCLI.CODEX, AgentCLI.GEMINI]
    assert next_new.cli == AgentCLI.CODEX
    assert next_new.session_id is None


def test_exact_backup_records_canonical_chain_in_active_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    class ConcretePhase(Phase):
        def __init__(self):
            super().__init__(interactive=False)
            self.agent_manager = manager
            self.issue_dir = tmp_path / ".cafe" / "issues" / "issue-381"
            self.phase_dir = self.issue_dir / "develop"
            self.iteration = 1
            self._session_continuation = SessionContinuation.resume_exact(
                AgentCLI.GEMINI,
                "exact-gemini",
            )

        def execute(self):
            raise NotImplementedError

    def execute(self, *args, **kwargs):
        return AgentResponse(
            response="confirmed",
            token_usage=TokenUsage(),
            cli=self.config.cli,
            session_id="actual-gemini",
        )

    phase = ConcretePhase()
    with patch("cafe.agents.executor.AgentExecutor.execute", execute):
        phase._execute_agent_iteration(
            agent_name="David",
            prompt="repair",
            user_input="baton retry",
            valid_intents=[],
            require_status_code=False,
            allowed_tools=[],
            phase_specific_data={"step_name": "develop"},
        )

    active = json.loads((phase.issue_dir / "active_clis.json").read_text(encoding="utf-8"))["David"]
    assert active["cli"] == "gemini"
    assert [entry["cli"] for entry in active["chain"]] == ["codex", "gemini"]


def test_canonical_chain_includes_legacy_backup_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = AgentManager(issue_name="issue-381")
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CODEX,
            model="codex-model",
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"develop": "gemini-model"}},
        )
    )

    chain = manager.configured_execution_chain("David")

    assert [entry.cli for entry in chain] == [AgentCLI.CODEX, AgentCLI.GEMINI]


def test_legacy_exact_backup_records_canonical_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = AgentManager(issue_name="issue-381")
    manager.register_agent(
        AgentConfig(
            name="David",
            cli=AgentCLI.CODEX,
            model="codex-model",
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"develop": "gemini-model"}},
        )
    )

    class ConcretePhase(Phase):
        def __init__(self):
            super().__init__(interactive=False)
            self.agent_manager = manager
            self.issue_dir = tmp_path / ".cafe" / "issues" / "issue-381"
            self.phase_dir = self.issue_dir / "develop"
            self.iteration = 1
            self._session_continuation = SessionContinuation.resume_exact(
                AgentCLI.GEMINI,
                "exact-gemini",
            )

        def execute(self):
            raise NotImplementedError

    def execute(self, *args, **kwargs):
        return AgentResponse(
            response="confirmed",
            token_usage=TokenUsage(),
            cli=self.config.cli,
            session_id="actual-gemini",
        )

    phase = ConcretePhase()
    with patch("cafe.agents.executor.AgentExecutor.execute", execute):
        phase._execute_agent_iteration(
            agent_name="David",
            prompt="repair",
            user_input="baton retry",
            valid_intents=[],
            require_status_code=False,
            allowed_tools=[],
            phase_specific_data={"step_name": "develop"},
        )

    active = json.loads((phase.issue_dir / "active_clis.json").read_text(encoding="utf-8"))["David"]
    assert active["cli"] == "gemini"
    assert [entry["cli"] for entry in active["chain"]] == ["codex", "gemini"]


def test_new_primary_fallback_is_also_fresh(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    created_sessions: list[tuple[AgentCLI, str | None]] = []

    def execute(self, *args, **kwargs):
        created_sessions.append((self.config.cli, self.config.session_id))
        if self.config.cli == AgentCLI.CODEX:
            raise AgentExecutionError("rate limit", error_type="rate_limit")
        return AgentResponse(
            response="fallback",
            token_usage=TokenUsage(),
            cli=AgentCLI.GEMINI,
            session_id="fresh-gemini",
        )

    with patch("cafe.agents.executor.AgentExecutor.execute", execute):
        response, *_ = manager.execute(
            "David",
            "prompt",
            phase_name="develop",
            continuation=SessionContinuation.new(),
        )

    assert response == "fallback"
    assert created_sessions == [
        (AgentCLI.CODEX, None),
        (AgentCLI.GEMINI, None),
    ]
    assert manager.get_last_session_id() == "fresh-gemini"


def test_exact_primary_without_takeover_context_does_not_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    attempted_clis: list[AgentCLI] = []

    def execute(self, *args, **kwargs):
        attempted_clis.append(self.config.cli)
        raise AgentExecutionError("rate limit", error_type="rate_limit")

    with (
        patch("cafe.agents.executor.AgentExecutor.execute", execute),
        pytest.raises(AgentExecutionError, match="rate limit"),
    ):
        manager.execute(
            "David",
            "prompt",
            phase_name="develop",
            continuation=SessionContinuation.resume_exact(
                AgentCLI.CODEX,
                "exact-codex",
            ),
        )

    assert attempted_clis == [AgentCLI.CODEX]


def test_phase_exact_retry_without_takeover_context_remains_on_primary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    attempted_clis: list[AgentCLI] = []

    class ConcretePhase(Phase):
        def __init__(self):
            super().__init__(interactive=False)
            self.agent_manager = manager
            self.issue_dir = tmp_path / ".cafe" / "issues" / "issue-381"
            self.phase_dir = self.issue_dir / "develop"
            self.iteration = 1
            self._session_continuation = SessionContinuation.resume_exact(
                AgentCLI.CODEX,
                "exact-codex",
            )

        def execute(self):
            raise NotImplementedError

    def execute(self, *args, **kwargs):
        attempted_clis.append(self.config.cli)
        raise AgentExecutionError("rate limit", error_type="rate_limit")

    with (
        patch("cafe.agents.executor.AgentExecutor.execute", execute),
        pytest.raises(CriticalPhaseError, match="rate limit"),
    ):
        ConcretePhase()._execute_agent_iteration(
            agent_name="David",
            prompt="complete checklist",
            user_input="workflow execute",
            valid_intents=[],
            require_status_code=False,
            allowed_tools=[],
            phase_specific_data={"step_name": "develop"},
        )

    assert attempted_clis == [AgentCLI.CODEX]


def test_exact_primary_with_empty_takeover_context_does_not_run_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    attempted_clis: list[AgentCLI] = []

    def execute(self, *args, **kwargs):
        attempted_clis.append(self.config.cli)
        raise AgentExecutionError("rate limit", error_type="rate_limit")

    with (
        patch("cafe.agents.executor.AgentExecutor.execute", execute),
        pytest.raises(AgentExecutionError, match="All agents failed"),
    ):
        manager.execute(
            "David",
            "prompt",
            phase_name="develop",
            continuation=SessionContinuation.resume_exact(
                AgentCLI.CODEX,
                "exact-codex",
            ),
            backup_context_callback=lambda _error: "   ",
        )

    assert attempted_clis == [AgentCLI.CODEX]


def test_exact_primary_fallback_is_a_fresh_context_takeover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    attempts: list[tuple[AgentCLI, str | None, str, bool]] = []

    def execute(self, prompt, *args, **kwargs):
        attempts.append(
            (
                self.config.cli,
                self.config.session_id,
                prompt,
                bool(kwargs.get("exact_session")),
            )
        )
        if self.config.cli == AgentCLI.CODEX:
            raise AgentExecutionError("rate limit", error_type="rate_limit")
        return AgentResponse(
            response="fallback",
            token_usage=TokenUsage(),
            cli=AgentCLI.GEMINI,
            session_id="fresh-gemini",
        )

    with patch("cafe.agents.executor.AgentExecutor.execute", execute):
        response, *_ = manager.execute(
            "David",
            "primary prompt",
            phase_name="develop",
            continuation=SessionContinuation.resume_exact(
                AgentCLI.CODEX,
                "exact-codex",
            ),
            backup_context_callback=lambda _error: '{"operation":{"state":"running"}}',
        )

    assert response == "fallback"
    assert attempts[0] == (AgentCLI.CODEX, "exact-codex", "primary prompt", True)
    backup_cli, backup_session, backup_prompt, backup_exact = attempts[1]
    assert backup_cli == AgentCLI.GEMINI
    assert backup_session is None
    assert backup_exact is False
    assert "Cold backup takeover context" in backup_prompt
    assert '"operation":{"state":"running"}' in backup_prompt
    assert "exact-codex" not in backup_prompt
    assert manager.get_last_session_id() == "fresh-gemini"


def test_exact_continuation_requires_configured_complete_pair() -> None:
    assert (
        exact_continuation_from_context(
            {"cli": "codex", "session_id": "sid"},
            configured_clis=[AgentCLI.CODEX],
        ).policy
        == SessionContinuationPolicy.RESUME_EXACT
    )
    assert (
        exact_continuation_from_context(
            {"cli": "codex", "session_id": "sid"},
            configured_clis=[AgentCLI.GEMINI],
        )
        is None
    )
    assert exact_continuation_from_context({"cli": "codex"}) is None
