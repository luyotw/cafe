"""Tests for CliEntry model."""

import pytest

from cafe.core.types import AgentCLI, CliEntry


class TestCliEntryDefaults:
    """Tests for CliEntry default values."""

    def test_model_defaults_to_none(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE)
        assert entry.model is None

    def test_phase_models_defaults_to_empty_dict(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE)
        assert entry.phase_models == {}

    def test_cli_is_required(self) -> None:
        with pytest.raises(Exception):
            CliEntry()  # type: ignore[call-arg]


class TestCliEntryResolveModel:
    """Tests for CliEntry.resolve_model()."""

    def test_returns_phase_override_when_phase_matches(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE, model="opus", phase_models={"plan": "sonnet"})
        assert entry.resolve_model("plan") == "sonnet"

    def test_returns_model_when_phase_not_in_overrides(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE, model="opus", phase_models={"plan": "sonnet"})
        assert entry.resolve_model("develop") == "opus"

    def test_returns_none_when_no_phase_no_model(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE)
        assert entry.resolve_model("plan") is None

    def test_returns_none_when_phase_none(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE, model="opus")
        assert entry.resolve_model(None) == "opus"

    def test_empty_string_phase_override_returns_none(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE, model="opus", phase_models={"plan": ""})
        assert entry.resolve_model("plan") is None

    def test_empty_string_model_returns_none(self) -> None:
        entry = CliEntry(cli=AgentCLI.CLAUDE, model="")
        assert entry.resolve_model(None) is None

    def test_returns_phase_override_even_when_model_also_set(self) -> None:
        entry = CliEntry(
            cli=AgentCLI.GEMINI,
            model="gemini-2.5-pro",
            phase_models={"develop": "gemini-2-flash"},
        )
        assert entry.resolve_model("develop") == "gemini-2-flash"
        assert entry.resolve_model("plan") == "gemini-2.5-pro"

    def test_returns_none_for_unrecognized_phase_with_no_model(self) -> None:
        entry = CliEntry(cli=AgentCLI.COPILOT, phase_models={"plan": "x"})
        assert entry.resolve_model("review") is None
