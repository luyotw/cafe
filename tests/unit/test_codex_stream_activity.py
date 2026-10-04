"""Real stream activity affects liveness without becoming completion evidence."""

import json
import os
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cafe.agents.cli.codex_stream_activity import CodexStreamActivity
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


def test_native_sized_otel_batch_is_not_discarded():
    data = payload()
    records = data["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    # The installed native exporter sends 512-record batches around 1.16 MB.
    records[0]["body"]["stringValue"] = "private-model-content" * 140
    records *= 512
    encoded = json.dumps(data).encode()
    assert len(encoded) > 1_048_576
    with CodexStreamActivity() as activity:
        activity.bind("session")
        endpoint = f"http://127.0.0.1:{activity._server.server_port}{activity._path}"
        with urllib.request.urlopen(urllib.request.Request(endpoint, encoded), timeout=2) as response:
            assert response.status == 200
        result = activity.drain()
        assert result is not None
        assert "private" not in json.dumps(result)
        assert activity.diagnostics()["stream_activity_rejected_requests"] == 0
        assert activity.diagnostics()["stream_activity_max_request_bytes"] == len(encoded)


def test_receiver_rejects_unbounded_requests_without_counting_activity():
    with CodexStreamActivity() as activity:
        activity.bind("session")
        endpoint = f"http://127.0.0.1:{activity._server.server_port}{activity._path}"
        request = urllib.request.Request(
            endpoint, b"{}", headers={"Content-Length": str(activity.MAX_REQUEST_BYTES + 1)}
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        assert error.value.code == 400
        assert activity.drain() is None
        assert activity.diagnostics()["stream_activity_rejected_requests"] == 1


def test_existing_exporter_is_preserved(tmp_path):
    config = tmp_path / "config.toml"
    original = '[otel]\nexporter={otlp-http={endpoint="https://configured.invalid/v1/logs",protocol="json"}}\n'
    config.write_text(original)
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(["codex", "exec", "prompt"], {"CODEX_HOME": str(tmp_path)})
    assert config.read_text() == original


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize(
    "contents",
    [
        '[otel]\nexporter={otlp-http={endpoint="https://existing.invalid/v1/logs",protocol="json"}}\n',
        "[otel\n",
    ],
)
def test_isolated_decision_command_ignores_inactive_user_config(tmp_path, resume, contents):
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    config.write_text(contents)
    executor = AgentExecutor(
        AgentConfig(
            name="decision",
            cli=AgentCLI.CODEX,
            session_id="existing-session" if resume else None,
        )
    )
    strategy = executor._get_cli_strategy()
    cmd, _ = executor._build_controlled_command(
        strategy,
        "prompt",
        [],
        [],
        AgentExecutionControl(working_directory=tmp_path / "decision"),
    )
    assert "--ignore-user-config" in cmd
    with strategy.create_stream_activity(cmd) as activity:
        observed_cmd = activity.command(cmd, {"CODEX_HOME": str(home)})
    assert observed_cmd[: len(cmd)] == cmd
    assert any(arg.startswith("otel.exporter=") for arg in observed_cmd)
    assert config.read_text() == contents


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "monitoring"],
        ["-p", "monitoring"],
        ["--profile=monitoring"],
        ["-pmonitoring"],
        ["-p=monitoring"],
    ],
)
def test_active_profile_exporter_is_preserved(tmp_path, arguments):
    base = tmp_path / "config.toml"
    base.write_text('[otel]\nexporter="none"\n')
    profile = tmp_path / "monitoring.config.toml"
    contents = '[otel]\nexporter={otlp-http={endpoint="https://existing.invalid/v1/logs",protocol="json"}}\n'
    profile.write_text(contents)
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(
            ["codex", "exec", *arguments, "--json", "prompt"], {"CODEX_HOME": str(tmp_path)}
        )
    assert profile.read_text() == contents
    assert base.read_text() == '[otel]\nexporter="none"\n'


def test_profile_without_exporter_retains_base_exporter_protection(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[otel]\nexporter={otlp-http={endpoint="https://existing.invalid/v1/logs",protocol="json"}}\n'
    )
    (tmp_path / "monitoring.config.toml").write_text("[otel]\nlog_user_prompt=false\n")
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(
            ["codex", "exec", "--profile", "monitoring", "prompt"], {"CODEX_HOME": str(tmp_path)}
        )


def test_profile_can_disable_base_exporter(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[otel]\nexporter={otlp-http={endpoint="https://existing.invalid/v1/logs",protocol="json"}}\n'
    )
    (tmp_path / "monitoring.config.toml").write_text('[otel]\nexporter="none"\n')
    with CodexStreamActivity() as activity:
        cmd = activity.command(
            ["codex", "exec", "--profile", "monitoring", "prompt"], {"CODEX_HOME": str(tmp_path)}
        )
    assert any(arg.startswith("otel.exporter=") for arg in cmd)


@pytest.mark.parametrize(
    "contents",
    [
        '[otel]\nexporter={otlp-http={endpoint="https://existing.invalid/v1/logs",protocol="json"}}\n',
        "[otel\n",
    ],
)
def test_ignore_user_config_also_skips_selected_profile(tmp_path, contents):
    (tmp_path / "config.toml").write_text(contents)
    profile = tmp_path / "monitoring.config.toml"
    profile.write_text(contents)
    with CodexStreamActivity() as activity:
        cmd = activity.command(
            [
                "codex",
                "exec",
                "--ignore-user-config",
                "--profile",
                "monitoring",
                "prompt",
            ],
            {"CODEX_HOME": str(tmp_path)},
        )
    assert any(arg.startswith("otel.exporter=") for arg in cmd)
    assert profile.read_text() == contents


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
            if mode=='batch':
                records=data['resourceLogs'][0]['scopeLogs'][0]['logRecords']
                records[0]['body']={'stringValue':'private-model-content'*140}
                records*=512
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


@pytest.mark.parametrize("mode", ["live", "batch"])
def test_streaming_without_stdout_is_visible_and_does_not_idle_timeout(tmp_path, monkeypatch, mode):
    result = run_codex_fixture(tmp_path, monkeypatch, mode)
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
        ['-cotel.exporter="none"'],
        ['-c=otel.exporter="none"'],
        ["--config", 'otel.exporter.otlp-http.endpoint="https://existing.invalid"'],
    ],
)
def test_existing_invocation_export_settings_are_not_overwritten(tmp_path, arguments):
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(["codex", "exec", "prompt", *arguments], {"CODEX_HOME": str(tmp_path)})


def test_ignore_user_config_does_not_hide_invocation_exporter_override(tmp_path):
    with CodexStreamActivity() as activity, pytest.raises(ValueError):
        activity.command(
            [
                "codex",
                "exec",
                "--ignore-user-config",
                "prompt",
                "-c",
                'otel.exporter="none"',
            ],
            {"CODEX_HOME": str(tmp_path)},
        )


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
