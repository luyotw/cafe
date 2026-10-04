"""The executor consumes activity contracts without choosing a provider."""

import json
import sys

import pytest

from cafe.agents.cli.claude import ClaudeCLI
from cafe.agents.cli.codex import CodexCLI
from cafe.agents.cli.copilot import CopilotCLI
from cafe.agents.cli.cursor import CursorCLI
from cafe.agents.cli.gemini import GeminiCLI
from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig


class NativeActivity:
    """A non-Codex adapter double with one session-bound observation."""

    def __init__(self):
        self.opened = False
        self.closed = False
        self.session = None
        self.reported = False

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, *_args):
        self.closed = True

    def command(self, cmd, environment):
        assert self.opened
        return [cmd[0], "-u", *cmd[1:]]

    def bind(self, session):
        self.session = session

    def drain(self):
        if self.session is None or self.reported:
            return None
        self.reported = True
        return {
            "type": "cafe.stream_activity",
            "session_id": self.session,
            "source": "other_cli.native_stream",
            "kind": "text_delta",
            "event_count": 1,
        }

    def diagnostics(self):
        return {"stream_activity_events": int(self.reported)}


@pytest.mark.parametrize(
    ("cli", "strategy_type"),
    [
        (AgentCLI.CLAUDE, ClaudeCLI),
        (AgentCLI.GEMINI, GeminiCLI),
        (AgentCLI.CURSOR, CursorCLI),
        (AgentCLI.COPILOT, CopilotCLI),
    ],
)
def test_default_adapters_retain_stdout_only(cli, strategy_type):
    strategy = strategy_type(AgentConfig(name="default", cli=cli))
    assert strategy.create_stream_activity(["provider", "prompt"]) is None


@pytest.mark.parametrize("cmd", [[], ["codex"], [sys.executable, "fixture.py"]])
def test_codex_adapter_does_not_capture_interactive_or_other_executables(cmd):
    strategy = CodexCLI(AgentConfig(name="codex", cli=AgentCLI.CODEX))
    assert strategy.create_stream_activity(cmd) is None


def test_non_codex_adapter_supplies_activity_to_common_executor(tmp_path, monkeypatch):
    activity = NativeActivity()
    monkeypatch.setattr(ClaudeCLI, "create_stream_activity", lambda self, cmd: activity)
    executor = AgentExecutor(AgentConfig(name="other", cli=AgentCLI.CLAUDE), stream_output=False)
    script = tmp_path / "provider.py"
    script.write_text("""
import json, time
print(json.dumps({'type':'system','subtype':'init','session_id':'other-session'}), flush=True)
time.sleep(.2)
print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'done'}]}}), flush=True)
print(json.dumps({'type':'result','result':'done','is_error':False}), flush=True)
""")
    observers = []
    result = executor._execute_with_streaming(
        [sys.executable, str(script)],
        "Other provider",
        parse_stream_json=True,
        streaming_output_file=str(tmp_path / "stream.jsonl"),
        structured_records=[],
        structured_record_observer=observers.append,
        response_parser=lambda lines: executor._parse_using_strategy(
            ClaudeCLI(executor.config), lines
        ),
    )
    records = [json.loads(line) for line in (tmp_path / "stream.jsonl").read_text().splitlines()]
    assert activity.opened and activity.closed
    assert activity.session == "other-session"
    assert any(record.get("source") == "other_cli.native_stream" for record in records)
    assert all(record.get("type") != "cafe.stream_activity" for record in observers)
    assert result.response == "done"
    assert result.transport_result.completed is True


def test_generic_activity_setup_failure_closes_resources(monkeypatch):
    activity = NativeActivity()

    def fail_command(*args):
        raise ValueError("private adapter details")

    monkeypatch.setattr(activity, "command", fail_command)
    monkeypatch.setattr(ClaudeCLI, "create_stream_activity", lambda self, cmd: activity)
    executor = AgentExecutor(AgentConfig(name="other", cli=AgentCLI.CLAUDE))
    with pytest.raises(AgentExecutionError) as caught:
        executor._execute_with_streaming(["provider"], "Other provider")
    assert activity.opened and activity.closed
    assert caught.value.error_type == "stream_activity_unavailable"
    assert "Other provider" in str(caught.value)
    assert "Codex" not in str(caught.value)
    assert "private" not in str(caught.value)


def test_generic_activity_closes_on_caller_exception(monkeypatch):
    activity = NativeActivity()
    monkeypatch.setattr(ClaudeCLI, "create_stream_activity", lambda self, cmd: activity)
    executor = AgentExecutor(AgentConfig(name="other", cli=AgentCLI.CLAUDE))

    def fail_execution(*args, **kwargs):
        raise ValueError("observer failed")

    monkeypatch.setattr(executor, "_execute_streaming_process", fail_execution)
    with pytest.raises(ValueError, match="observer failed"):
        executor._execute_with_streaming(["provider"], "Other provider")
    assert activity.opened and activity.closed
