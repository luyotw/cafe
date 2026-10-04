"""Subprocess regressions for concurrent pipe draining and completion evidence."""

import json
import sys
import textwrap

import pytest

from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
from cafe.core.types import AgentCLI, AgentConfig


def run_fixture(tmp_path, script, **kwargs):
    fixture = tmp_path / "provider.py"
    fixture.write_text(textwrap.dedent(script))
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CODEX))
    executor.stream_output = False
    return executor._execute_with_streaming(
        [sys.executable, "-u", str(fixture)],
        "Codex",
        parse_stream_json=True,
        response_parser=lambda lines: executor._parse_using_strategy(
            executor._get_cli_strategy(), lines
        ),
        require_terminal_stream_event=True,
        streaming_output_file=str(tmp_path / "stream.jsonl"),
        execution_control=AgentExecutionControl(max_duration_seconds=3),
        **kwargs,
    )


@pytest.mark.parametrize("stderr_bytes", [320_000, 2_000_000])
def test_stderr_backpressure_does_not_block_completion(tmp_path, stderr_bytes):
    result = run_fixture(tmp_path, f"""
        import json, os, time
        print(json.dumps({{'type':'thread.started','thread_id':'fixture'}}), flush=True)
        time.sleep(.6)
        os.write(2, b'd' * {stderr_bytes})
        print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'done'}}}}), flush=True)
        print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':2,'output_tokens':1}}}}), flush=True)
    """)
    assert result.response == "done"
    assert result.transport_result.completed is True
    assert result.token_usage.input_tokens == 2
    saved = [json.loads(line) for line in (tmp_path / "stream.jsonl").read_text().splitlines()]
    stderr = next(record["content"] for record in saved if record.get("type") == "stderr")
    assert 0 < len(stderr) <= 1_048_576


def test_batched_terminal_event_is_read_before_child_exit(tmp_path):
    acknowledgement = tmp_path / "observed"

    def observe(record):
        if record.get("type") == "turn.completed":
            acknowledgement.touch()

    result = run_fixture(tmp_path, f"""
        import json, os, time
        from pathlib import Path
        events = [{{'type':'thread.started','thread_id':'fixture'}},
                  {{'type':'turn.started'}},
                  {{'type':'item.completed','item':{{'type':'agent_message','text':'done'}}}},
                  {{'type':'turn.completed','usage':{{'input_tokens':1,'output_tokens':1}}}}]
        os.write(1, ('\\n'.join(json.dumps(e) for e in events)+'\\n').encode())
        deadline = time.monotonic() + 5
        while not Path({str(acknowledgement)!r}).exists():
            if time.monotonic() > deadline:
                raise SystemExit(1)
            time.sleep(.01)
    """, structured_records=[], structured_record_observer=observe)
    assert acknowledgement.exists()
    assert result.transport_result.completed is True
    assert result.response == "done"


def test_failure_retains_safe_progress_without_raw_provider_data(tmp_path):
    secret = "private-prompt-or-credential-fixture"
    with pytest.raises(AgentExecutionError):
        run_fixture(tmp_path, f"""
            import json, os, time
            print(json.dumps({{'type':'thread.started','thread_id':'fixture'}}), flush=True)
            print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{secret!r}}}}}), flush=True)
            time.sleep(.6)
            os.write(2, {secret.encode()!r})
            raise SystemExit(1)
        """)
    text = (tmp_path / "stream.jsonl").read_text()
    saved = json.loads(text)
    assert secret not in text
    assert saved["stream_diagnostics"]["stdout_lines"] >= 2
    assert saved["stream_diagnostics"]["stderr_bytes"] > 0
    assert saved["stream_diagnostics"]["terminal_event_observed"] is False


def test_partial_output_and_stderr_do_not_bypass_execution_deadline(tmp_path):
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, """
            import json, os, time
            print(json.dumps({'type':'thread.started','thread_id':'fixture'}), flush=True)
            time.sleep(.6)
            while True:
                os.write(2, b'progress\\n')
                time.sleep(.05)
        """)
    assert caught.value.error_type == "execution_limit"
    saved = json.loads((tmp_path / "stream.jsonl").read_text())
    assert saved["stream_diagnostics"]["terminal_event_observed"] is False


def test_pipe_decode_failure_is_not_success_and_keeps_safe_diagnostics(tmp_path):
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, """
            import json, os, time
            print(json.dumps({'type':'thread.started','thread_id':'fixture'}), flush=True)
            time.sleep(.6)
            os.write(2, b'\\xffprivate-provider-data')
            time.sleep(5)
        """)
    assert caught.value.error_type == "pipe_read_error"
    text = (tmp_path / "stream.jsonl").read_text()
    assert "private-provider-data" not in text
    assert json.loads(text)["stream_diagnostics"]["stderr_read_failed"] is True


@pytest.mark.parametrize("rejection", ["native option rejected", "unknown option", "unrecognized argument"])
def test_native_option_rejection_keeps_actionable_safe_diagnostics(tmp_path, rejection):
    secret = "private-provider-configuration-fixture"
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, f"""
            import sys
            print({(rejection + ': ' + secret)!r}, file=sys.stderr, flush=True)
            raise SystemExit(1)
        """)
    assert caught.value.error_type == "unsupported_option"
    assert "native option rejected" in caught.value.display_message
    assert "do not retry with weaker restrictions" in caught.value.display_message
    assert secret not in caught.value.display_message
    text = (tmp_path / "stream.jsonl").read_text()
    assert secret not in text
    assert json.loads(text)["error_type"] == "unsupported_option"
