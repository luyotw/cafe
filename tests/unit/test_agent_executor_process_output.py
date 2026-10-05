"""Subprocess regressions for concurrent pipe draining and completion evidence."""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from cafe.agents.diagnostics import build_failed_attempt
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
    metadata = next(record for record in saved if record.get("type") == "stderr_diagnostics")
    stderr = Path(metadata["stderr_log"]).read_text()
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


def test_failure_saves_private_stderr_and_summary_reference(tmp_path):
    raw = "connection closed unexpectedly; token=private-fixture\n"
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, f"""
            import json, sys, time
            print(json.dumps({{'type':'thread.started','thread_id':'fixture'}}), flush=True)
            time.sleep(.6)
            sys.stderr.write({raw!r})
            raise SystemExit(7)
        """)
    metadata = caught.value.stderr_diagnostics
    log = Path(metadata["stderr_log"])
    assert log.read_text() == raw
    assert metadata["returncode"] == 7
    assert metadata["stderr_bytes"] == len(raw.encode())
    assert not metadata["stderr_truncated"]
    assert metadata["stderr_complete"]
    if os.name != "nt":
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
    summary = json.loads((tmp_path / "stream.jsonl").read_text())
    assert summary["stderr_diagnostics"] == metadata
    assert "private-fixture" not in json.dumps(summary)


def test_early_native_failure_also_saves_stderr(tmp_path):
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, """
            import sys, time
            sys.stderr.write('Error: failed to start provider\\n')
            sys.stderr.flush()
            time.sleep(5)
        """)
    log = Path(caught.value.stderr_diagnostics["stderr_log"])
    assert log.read_text() == "Error: failed to start provider\n"
    summary = json.loads((tmp_path / "stream.jsonl").read_text())
    assert summary["stderr_diagnostics"]["stderr_log"] == str(log)


def test_retry_attempts_keep_distinct_stderr_files(tmp_path):
    attempts = []
    for index in (1, 2):
        with pytest.raises(AgentExecutionError) as caught:
            run_fixture(tmp_path, f"""
                import sys, time
                time.sleep(.6)
                sys.stderr.write('attempt {index} detail\\n')
                raise SystemExit(1)
            """)
        attempts.append(build_failed_attempt(
            cli=AgentCLI.CODEX, chain_role="primary", attempt=index, error=caught.value
        ))
    paths = [Path(attempt["stderr_diagnostics"]["stderr_log"]) for attempt in attempts]
    assert paths[0] != paths[1]
    assert [path.read_text() for path in paths] == ["attempt 1 detail\n", "attempt 2 detail\n"]


def test_execution_deadline_saves_stderr_and_actual_returncode(tmp_path):
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, """
            import sys, time
            time.sleep(.6)
            sys.stderr.write('waiting for provider\\n')
            sys.stderr.flush()
            time.sleep(10)
        """)
    assert caught.value.error_type == "execution_limit"
    metadata = caught.value.stderr_diagnostics
    assert Path(metadata["stderr_log"]).read_text() == "waiting for provider\n"
    assert metadata["returncode"] != 0
    assert metadata["timeout_kind"] == "execution_limit"


def test_successful_stderr_snapshot_marks_truncation(tmp_path):
    run_fixture(tmp_path, """
        import json, os, time
        time.sleep(.6)
        os.write(2, b'A' * 600_000 + b'B' * 600_000)
        terminal = {'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}
        print(json.dumps(terminal), flush=True)
    """)
    records = [json.loads(row) for row in (tmp_path / "stream.jsonl").read_text().splitlines()]
    metadata = next(row for row in records if row["type"] == "stderr_diagnostics")
    text = Path(metadata["stderr_log"]).read_text()
    assert metadata["stderr_bytes"] == 1_200_000
    assert metadata["stderr_retained_bytes"] == len(text.encode()) == 1_048_576
    assert metadata["stderr_truncated"]
    assert text.startswith("A") and text.endswith("B")


@pytest.mark.parametrize("conflicting_session", [False, True])
def test_returned_attempt_keeps_private_stderr_reference(tmp_path, conflicting_session):
    raw = "token=private-fixture\n"
    result = run_fixture(tmp_path, f"""
        import json, sys, time
        time.sleep(.6)
        sys.stderr.write({raw!r})
        print(json.dumps({{'type':'thread.started','thread_id':'first'}}), flush=True)
        if {conflicting_session!r}:
            print(json.dumps({{'type':'thread.started','thread_id':'second'}}), flush=True)
        terminal = {{'type':'turn.completed','usage':{{'input_tokens':1,'output_tokens':1}}}}
        print(json.dumps(terminal), flush=True)
    """)
    assert result.transport_result.failure_code == (
        "conflicting_session_evidence" if conflicting_session else None
    )
    summary = (tmp_path / "stream.jsonl").read_text()
    assert "private-fixture" not in summary
    records = [json.loads(row) for row in summary.splitlines()]
    metadata = next(row for row in records if row["type"] == "stderr_diagnostics")
    assert metadata["returncode"] == 0
    assert Path(metadata["stderr_log"]).read_text() == raw


def test_diagnostic_write_failure_preserves_provider_error(tmp_path, monkeypatch, capsys):
    def reject_write(*args, **kwargs):
        raise OSError("token=private-fixture")

    monkeypatch.setattr("cafe.agents.executor.save_stderr_diagnostics", reject_write)
    with pytest.raises(AgentExecutionError) as caught:
        run_fixture(tmp_path, """
            import sys, time
            time.sleep(.6)
            sys.stderr.write('provider connection closed unexpectedly\\n')
            raise SystemExit(7)
        """)
    assert caught.value.error_type is None
    assert "code 7" in str(caught.value)
    diagnostic_output = capsys.readouterr().out
    assert "Failed to save stderr diagnostics: OSError" in diagnostic_output
    assert "private-fixture" not in diagnostic_output
    assert "private-fixture" not in (tmp_path / "stream.jsonl").read_text()
