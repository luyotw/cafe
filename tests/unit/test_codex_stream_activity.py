"""Real stream activity affects liveness without becoming completion evidence."""

import json
import os
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cafe.agents.codex_stream_activity import CodexStreamActivity
from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig


def payload(
    *, session="session", source="codex.sse_event", kind="response.output_text.delta", age=0
):
    attributes = {
        "event.name": source,
        "event.kind": kind,
        "conversation.id": session,
        "event.timestamp": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
        "prompt": "private-prompt",
        "delta": "private-model-content",
    }
    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "attributes": {
                                    key: {"stringValue": value} for key, value in attributes.items()
                                },
                                "body": {"stringValue": "private-tool-output"},
                            }
                        ]
                    }
                ]
            }
        ]
    }


@pytest.mark.parametrize("list_attributes", [False, True])
def test_live_activity_is_bound_and_contains_metadata_only(list_attributes):
    activity = CodexStreamActivity()
    data = payload()
    record = data["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    if list_attributes:
        record["attributes"] = [
            {"key": key, "value": value} for key, value in record["attributes"].items()
        ]
    activity.receive(data)
    assert activity.drain() is None
    activity.bind("session")
    result = activity.drain()
    assert result["kind"] == "response.output_text.delta"
    assert "private" not in json.dumps(result)
    activity.receive(data)
    assert activity.drain() is None  # Replayed exports cannot refresh the watchdog.


@pytest.mark.parametrize(
    "changes",
    [
        {"session": "other-session"},
        {"source": "codex.user_prompt"},
        {"source": "codex.api_request"},
        {"kind": "plugin.update"},
        {"age": 60},
        {"age": -60},
    ],
)
def test_unrelated_stale_or_future_events_are_not_activity(changes):
    activity = CodexStreamActivity()
    activity.bind("session")
    activity.receive(payload(**changes))
    assert activity.drain() is None
    assert activity.diagnostics()["stream_activity_events"] == 0


def test_websocket_transport_is_observed():
    activity = CodexStreamActivity()
    activity.bind("session")
    activity.receive(payload(source="codex.websocket_event"))
    assert activity.drain()["source"] == "codex.websocket_event"


def test_existing_exporter_is_preserved(tmp_path):
    config = tmp_path / "config.toml"
    original = '[otel]\nexporter={otlp-http={endpoint="https://configured.invalid/v1/logs",protocol="json"}}\n'
    config.write_text(original)
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(["codex", "exec", "prompt"], {"CODEX_HOME": str(tmp_path)})
    assert config.read_text() == original


def run_codex_fixture(tmp_path: Path, monkeypatch, mode, **limits):
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent("""
        import datetime, json, re, sys, time, urllib.request
        configuration = next(s for s in sys.argv if s.startswith('otel.exporter='))
        endpoint = json.loads(re.search(r'endpoint=("[^"]+")', configuration).group(1))
        mode = sys.argv[sys.argv.index('exec') + 1]
        print(json.dumps({'type':'thread.started','thread_id':'session'}),flush=True)
        print(json.dumps({'type':'turn.started'}),flush=True)
        def event(kind):
            values = {'event.name':'codex.sse_event','event.kind':kind,
                      'conversation.id':'other-session' if mode=='foreign' else 'session',
                      'event.timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'delta':'private-token-text','prompt':'private-prompt'}
            data={'resourceLogs':[{'scopeLogs':[{'logRecords':[{'attributes':{
                k:{'stringValue':v} for k,v in values.items()}}]}]}]}
            with urllib.request.urlopen(urllib.request.Request(endpoint,json.dumps(data).encode(),
                headers={'Content-Type':'application/json'}),timeout=2) as response:
                response.read()
        for i in range(12):
            time.sleep(.2)
            if mode!='stalled' or i==0:event('response.output_text.delta')
        event('response.completed')
        if mode!='telemetry_only':
            print(json.dumps({'type':'item.completed','item':{
                'type':'agent_message','text':'done'}}),flush=True)
            print(json.dumps({'type':'turn.completed','usage':{
                'input_tokens':1,'output_tokens':1}}),flush=True)
    """)
    )
    executable.chmod(0o755)
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CODEX))
    executor.stream_output = False
    wall = time.time()
    epoch = time.monotonic()
    # Accelerate only the watchdog clock. Child/transport timestamps remain real.
    monkeypatch.setattr(time, "time", lambda: wall + (time.monotonic() - epoch) * 1000)
    return executor._execute_with_streaming(
        [str(executable), "exec", mode],
        "Codex",
        env={**os.environ, "CODEX_HOME": str(tmp_path)},
        parse_stream_json=True,
        require_terminal_stream_event=True,
        response_parser=lambda lines: executor._parse_using_strategy(
            executor._get_cli_strategy(), lines
        ),
        streaming_output_file=str(tmp_path / "stream.jsonl"),
        execution_control=AgentExecutionControl(**{"max_duration_seconds": 5, **limits}),
    )


def test_streaming_without_stdout_is_visible_and_does_not_idle_timeout(tmp_path, monkeypatch):
    result = run_codex_fixture(tmp_path, monkeypatch, "live")
    records = [json.loads(line) for line in (tmp_path / "stream.jsonl").read_text().splitlines()]
    activity = [record for record in records if record["type"] == "cafe.stream_activity"]
    assert activity
    assert result.response == "done"
    assert result.transport_result.completed is True
    assert result.token_usage.output_tokens == 1
    assert "private" not in json.dumps(activity)


@pytest.mark.parametrize("mode", ["stalled", "foreign"])
def test_genuine_inactivity_still_times_out(tmp_path, monkeypatch, mode):
    with pytest.raises(AgentExecutionError) as caught:
        run_codex_fixture(tmp_path, monkeypatch, mode)
    assert caught.value.error_type == "timeout"
    error = json.loads((tmp_path / "stream.jsonl").read_text())
    assert error["stream_diagnostics"]["terminal_event_observed"] is False


def test_transport_completion_is_not_turn_completion(tmp_path, monkeypatch):
    with pytest.raises(AgentExecutionError):
        run_codex_fixture(tmp_path, monkeypatch, "telemetry_only")
    error = json.loads((tmp_path / "stream.jsonl").read_text())
    assert error["stream_diagnostics"]["stream_activity_events"] > 0
    assert error["stream_diagnostics"]["terminal_event_observed"] is False


@pytest.mark.parametrize(
    "limits",
    [
        {"max_duration_seconds": 1.6},
        {"max_output_lines": 2},
        {"max_output_bytes": 200},
    ],
)
def test_stream_activity_cannot_extend_explicit_execution_limits(tmp_path, monkeypatch, limits):
    with pytest.raises(AgentExecutionError) as caught:
        run_codex_fixture(tmp_path, monkeypatch, "live", **limits)
    assert caught.value.error_type == "execution_limit"
    error = json.loads((tmp_path / "stream.jsonl").read_text())
    assert error["stream_diagnostics"]["stream_activity_events"] > 0
    assert error["stream_diagnostics"]["terminal_event_observed"] is False


@pytest.mark.parametrize("resume", [False, True])
def test_export_settings_belong_to_executed_subcommand(tmp_path, resume):
    original = ["codex", "-a", "never", "exec"]
    if resume:
        original.extend(["resume", "existing-session"])
    original.extend(["prompt", "--model", "gpt-6.1-sol", "--json"])
    with CodexStreamActivity() as activity:
        cmd = activity.command(original, {"CODEX_HOME": str(tmp_path)})
    assert cmd[: len(original)] == original
    assert cmd[len(original) :][::2] == ["-c", "-c"]
    assert "127.0.0.1" in cmd[-3]
    assert cmd[-1] == "otel.log_user_prompt=false"


@pytest.mark.parametrize(
    "arguments",
    [
        ["-c", 'otel.exporter="none"'],
        ['--config=otel.exporter="none"'],
        ["--config", 'otel.exporter.otlp-http.endpoint="https://existing.invalid"'],
    ],
)
def test_existing_invocation_export_settings_are_not_overwritten(tmp_path, arguments):
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(["codex", "exec", "prompt", *arguments], {"CODEX_HOME": str(tmp_path)})


@pytest.mark.parametrize(
    "malformed",
    [
        {"resourceLogs": None},
        {"resourceLogs": {}},
        {"resourceLogs": [{"scopeLogs": "invalid"}]},
        {"resourceLogs": [{"scopeLogs": [{"logRecords": False}]}]},
    ],
)
def test_malformed_transport_payload_is_not_activity(malformed):
    activity = CodexStreamActivity()
    activity.bind("session")
    activity.receive(malformed)
    assert activity.drain() is None
