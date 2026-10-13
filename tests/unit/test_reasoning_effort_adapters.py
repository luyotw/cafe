"""Issue #408 U2/U3: verified capabilities, scoped projection and fail-closed controls."""

import json

import pytest

from cafe.agents.cli.codex import CodexCLI
from cafe.agents.cli.claude import ClaudeCLI
from cafe.agents.cli.cursor import CursorCLI
from cafe.core.types import AgentCLI, AgentConfig


def install_provider_clients(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, version in [
        ("codex", "0.159.3"),
        ("claude", "2.1.289"),
        ("gemini", "0.58.0"),
        ("copilot", "1.0.83"),
    ]:
        binary = bindir / name
        binary.write_text(f'#!/bin/sh\necho "{version}"\n')
        binary.chmod(0o755)
    import os

    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    cache = tmp_path / "codex" / "models_cache.json"
    cache.parent.mkdir()
    cache.write_text(
        json.dumps(
            {
                "client_version": "0.159.3",
                "models": [
                    {
                        "slug": "gpt-5.5",
                        "supported_reasoning_levels": [
                            {"effort": v} for v in ("low", "medium", "high", "xhigh")
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def provider_clients(tmp_path, monkeypatch):
    return install_provider_clients(tmp_path, monkeypatch)


@pytest.mark.parametrize("session", [None, "existing-session"])
@pytest.mark.parametrize(
    "cli,model,effort",
    [(AgentCLI.CODEX, "gpt-5.5", "xhigh"), (AgentCLI.CLAUDE, "claude-opus-4-6", "max")],
)
def test_fresh_and_resume_command_apply_exact_effort(provider_clients, session, cli, model, effort):
    strategy = {AgentCLI.CODEX: CodexCLI, AgentCLI.CLAUDE: ClaudeCLI}[cli](
        AgentConfig(name="David", cli=cli, model=model, session_id=session, reasoning_effort=effort)
    )
    command = strategy.build_command("bounded prompt")
    if cli == AgentCLI.CODEX:
        assert 'model_reasoning_effort="xhigh"' in command
        assert ("resume" in command) == bool(session)
    else:
        assert command[command.index("--effort") + 1] == effort
        assert strategy.build_environment()["CLAUDE_CODE_EFFORT_LEVEL"] == effort


@pytest.mark.parametrize(
    "cli,model,effort",
    [
        (AgentCLI.CLAUDE, "claude-opus-4-6", "xhigh"),
        (AgentCLI.CODEX, "unknown", "high"),
        (AgentCLI.CURSOR, "auto", "high"),
    ],
)
def test_unsupported_explicit_combination_fails_before_launch(provider_clients, cli, model, effort):
    from cafe.agents.executor import AgentExecutor

    executor = AgentExecutor(
        AgentConfig(name="David", cli=cli, model=model, reasoning_effort=effort)
    )
    with pytest.raises(ValueError):
        executor.preview_cli_command_args("prompt")


def test_claude_explicit_effort_is_scoped_and_managed_cap_is_not_bypassed(
    provider_clients, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    strategy = ClaudeCLI(
        AgentConfig(
            name="David", cli=AgentCLI.CLAUDE, model="claude-opus-4-6", reasoning_effort="high"
        )
    )
    assert strategy.build_environment()["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    import os

    assert os.environ["CLAUDE_CODE_EFFORT_LEVEL"] == "low"
    settings = provider_clients / "claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"maxEffortLevel": "medium"}))
    with pytest.raises(ValueError):
        strategy.build_environment()


@pytest.mark.parametrize(
    "strategy,cli",
    [(CodexCLI, AgentCLI.CODEX), (ClaudeCLI, AgentCLI.CLAUDE), (CursorCLI, AgentCLI.CURSOR)],
)
def test_omission_leaves_provider_defaults(strategy, cli):
    command = strategy(AgentConfig(name="David", cli=cli, model="legacy")).build_command("prompt")
    assert "--effort" not in command
    assert not any("model_reasoning_effort=" in part for part in command)
