"""測試 AbstractCLI 抽象基底類別."""

import json
from abc import ABC

import pytest

from cafe.agents.cli.abstract import AbstractCLI
from cafe.agents.cli.claude import ClaudeCLI
from cafe.agents.cli.codex import CodexCLI
from cafe.agents.cli.cursor import CursorCLI
from cafe.agents.cli.gemini import GeminiCLI
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage


def test_abstract_cli_cannot_be_instantiated():
    """測試抽象類別無法直接實例化."""
    config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)

    # 嘗試直接實例化抽象類別應該會引發 TypeError
    with pytest.raises(TypeError):
        AbstractCLI(config)


def test_abstract_cli_is_abc():
    """測試 AbstractCLI 是 ABC 的子類別."""
    assert issubclass(AbstractCLI, ABC)


def test_abstract_cli_has_required_methods():
    """測試 AbstractCLI 定義了所有必要的抽象方法."""
    required_methods = {
        'build_command',
        'parse_response',
        'translate_allowed_tools',
        'add_directories',
        'get_output_format',
        'extract_session_id',
    }

    # 取得所有抽象方法
    abstract_methods = set(AbstractCLI.__abstractmethods__)

    # 確認所有必要方法都被定義為抽象方法
    assert required_methods.issubset(abstract_methods)


class ConcreteCLI(AbstractCLI):
    """具體實作的 CLI 類別，用於測試."""

    def build_command(self, prompt, allowed_tools=None, allowed_directories=None):
        return ["test-cli", "-p", prompt]

    def parse_response(self, output_lines, streaming_log=None):
        return "test response", TokenUsage(), []

    def translate_allowed_tools(self, tools):
        return tools

    def add_directories(self, cmd, directories):
        return cmd

    def get_output_format(self):
        return []

    def extract_session_id(self, output_lines):
        return None


def test_concrete_cli_can_be_instantiated():
    """測試具體實作的 CLI 類別可以正常實例化."""
    config = AgentConfig(name="test", cli=AgentCLI.CLAUDE)
    cli = ConcreteCLI(config)

    assert cli.config == config
    assert cli.config.name == "test"
    assert cli.config.cli == AgentCLI.CLAUDE
    assert cli.event_driver_conforming is False
    assert cli.extract_event_driver_session([]) is None
    assert (
        cli.accepts_event_driver_callback(
            [], session_id="session", event_id="event-1"
        )
        is False
    )


@pytest.mark.parametrize(
    ("strategy_type", "agent_cli", "record"),
    [
        (CodexCLI, AgentCLI.CODEX, {"type": "thread.started", "thread_id": "session"}),
        (
            ClaudeCLI,
            AgentCLI.CLAUDE,
            {"type": "system", "subtype": "init", "session_id": "session", "model": "exact"},
        ),
        (
            GeminiCLI,
            AgentCLI.GEMINI,
            {"type": "init", "session_id": "session", "model": "exact"},
        ),
        (
            CursorCLI,
            AgentCLI.CURSOR,
            {"type": "system", "subtype": "init", "session_id": "session", "model": "exact"},
        ),
    ],
)
def test_event_driver_adapters_require_verified_session_evidence(
    strategy_type, agent_cli: AgentCLI, record: dict[str, object]
) -> None:
    strategy = strategy_type(AgentConfig(name="driver", cli=agent_cli, model="exact"))

    assert strategy.event_driver_conforming is True
    assert strategy.extract_event_driver_session([record]) == "session"
    assert (
        strategy.accepts_event_driver_callback(
            [record], session_id="session", event_id="event-1"
        )
        is False
    )
    acknowledgement = {**record, "_cafe_event_id": "event-1"}
    assert (
        strategy.accepts_event_driver_callback(
            [acknowledgement], session_id="session", event_id="event-1"
        )
        is True
    )
    assert strategy.extract_event_driver_session([{**record, "model": "wrong"}]) is None
    assert (
        strategy.accepts_event_driver_callback(
            [acknowledgement], session_id="other", event_id="event-1"
        )
        is False
    )
    assert (
        strategy.accepts_event_driver_callback(
            [acknowledgement], session_id="session", event_id="event-2"
        )
        is False
    )
    assert strategy.extract_event_driver_session([{"type": "result", "session_id": "session"}]) is None
    empty_record = {
        key: ("" if key in {"session_id", "thread_id"} else value)
        for key, value in record.items()
    }
    assert strategy.extract_event_driver_session([empty_record]) is None

    encoded = [json.dumps(record)]
    assert strategy.extract_session_id(encoded) == "session"


@pytest.mark.parametrize(
    ("agent_cli", "session_id", "model", "initial_prompt", "expected"),
    [
        (AgentCLI.CLAUDE, None, None, None, ["claude"]),
        (
            AgentCLI.CLAUDE,
            "claude-session",
            "sonnet",
            "Review this",
            ["claude", "--resume", "claude-session", "--model", "sonnet", "Review this"],
        ),
        (
            AgentCLI.COPILOT,
            "copilot-session",
            None,
            "ignored",
            ["copilot", "--resume", "copilot-session"],
        ),
        (
            AgentCLI.GEMINI,
            "gemini-session",
            "gemini-pro",
            None,
            ["gemini", "--resume", "gemini-session", "--model", "gemini-pro"],
        ),
        (
            AgentCLI.CURSOR,
            "cursor-session",
            None,
            None,
            ["cursor-agent", "--resume", "cursor-session"],
        ),
        (
            AgentCLI.CODEX,
            "codex-session",
            "gpt-test",
            "Review this",
            ["codex", "--model", "gpt-test", "resume", "codex-session", "Review this"],
        ),
    ],
)
def test_build_interactive_command(
    agent_cli: AgentCLI,
    session_id: str | None,
    model: str | None,
    initial_prompt: str | None,
    expected: list[str],
) -> None:
    cli = ConcreteCLI(
        AgentConfig(name="test", cli=agent_cli, session_id=session_id, model=model)
    )

    assert cli.build_interactive_command(initial_prompt=initial_prompt) == expected
