"""Current native formats, resumed invocation deltas and complete thinking costs."""

import copy
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

from cafe.agents.cli.cursor import CursorCLI
from cafe.agents.cli.gemini import GeminiCLI
from cafe.agents.cli.provider_usage import CopilotUsage, GeminiUsage
from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.core.cost import account_cost, summarize_cost
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage
from cafe.core.usage import merge_token_usage_stats
from tests.unit.test_provider_pricing import state

SESSION = "6b53138b-831c-4dcf-87ce-22f99eaf683d"
COPILOT_METRICS = {
    "sessionStartTime": "2026-10-09T06:07:46.386Z",
    "totalNanoAiu": 2558740000,
    "totalApiDurationMs": 1203,
    "totalPremiumRequestCost": 1,
    "modelMetrics": {
        "claude-sonnet-5": {
            "requests": {"count": 1, "cost": 1},
            "totalNanoAiu": 2558740000,
            "usage": {
                "inputTokens": 24377,
                "outputTokens": 4,
                "cacheReadTokens": 15372,
                "cacheWriteTokens": 8926,
                "reasoningTokens": 0,
            },
        }
    },
}


def copilot_baseline(home, metrics=COPILOT_METRICS):
    path = home / ".copilot/session-state" / SESSION / "events.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"type": "session.start", "data": {"sessionId": SESSION}},
        {"type": "session.shutdown", "data": metrics},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def gemini_journal(home, messages, *, session=SESSION):
    path = home / ".gemini/tmp/project/chats" / f"session-2026-10-09T06-05-{SESSION[:8]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"sessionId": session, "startTime": "2026-10-09T06:05:31.414Z"}, *messages]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def gemini_message(
    id="new", model="gemini-3-flash-preview", input=10035, output=1, cached=0, thoughts=34
):
    return {
        "id": id,
        "type": "gemini",
        "model": model,
        "tokens": {
            "input": input,
            "output": output,
            "cached": cached,
            "thoughts": thoughts,
            "tool": 0,
        },
    }


def gemini_stream(models=None):
    counters = {"input_tokens": 10035, "output_tokens": 1, "cached": 0}
    return {
        "type": "result",
        "status": "success",
        "stats": {
            **counters,
            "models": models
            or {
                "gemini-3.1-pro-preview-customtools": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached": 0,
                },
                "gemini-3-flash-preview": counters,
            },
        },
    }


def test_copilot_native_credit_amount_takes_precedence_over_rates_and_premium_multiplier(tmp_path):
    observer = CopilotUsage(["copilot"], {"HOME": str(tmp_path)})
    try:
        raw = copy.deepcopy(COPILOT_METRICS)
        raw["agentMetrics"] = {"main": {"modelMetrics": raw["modelMetrics"]}}
        raw["totalPremiumRequestCost"] = 999  # Premium request multipliers are never USD.
        observer.path.write_text(json.dumps(raw))
        usage = observer(TokenUsage(), [])
        result = account_cost(usage, cli="copilot", model=None, state=None)
        record = result.cost_records[0]
        assert usage.input_tokens == 24377
        assert len(result.cost_records) == 1
        assert Decimal(record["amount_usd"]) == Decimal("0.0255874")
        assert record["provenance"] == "reported"
        assert record["billing"]["amount"] == "2558740000"
        assert "pricing" not in record
    finally:
        path = observer.path
        observer.close()
    assert not path.parent.exists()


def test_copilot_without_native_money_uses_pinned_model_rates(tmp_path):
    observer = CopilotUsage(["copilot"], {"HOME": str(tmp_path)})
    try:
        raw = copy.deepcopy(COPILOT_METRICS)
        raw.pop("totalNanoAiu")
        raw["modelMetrics"]["claude-sonnet-5"].pop("totalNanoAiu")
        observer.path.write_text(json.dumps(raw))
        result = account_cost(
            observer(TokenUsage(), []), cli="copilot", model=None, state=state("copilot")
        )
        assert result.cost_records[0]["provenance"] == "estimated"
        assert Decimal(result.cost_records[0]["amount_usd"]) == Decimal("0.0255874")
        assert result.cost_records[0]["billed_tokens"]["input"] == 79
    finally:
        observer.close()


def test_copilot_resume_subtracts_prelaunch_counters_duration_and_money(tmp_path):
    copilot_baseline(tmp_path)
    observer = CopilotUsage(["copilot", "--resume", SESSION], {"HOME": str(tmp_path)})
    try:
        current = copy.deepcopy(COPILOT_METRICS)
        current["totalNanoAiu"] *= 2
        current["totalApiDurationMs"] *= 2
        model = current["modelMetrics"]["claude-sonnet-5"]
        model["totalNanoAiu"] *= 2
        model["usage"] = {key: value * 2 for key, value in model["usage"].items()}
        observer.path.write_text(json.dumps(current))
        usage = observer(TokenUsage(), [], SESSION)
        assert usage.input_tokens == 24377
        assert usage.duration_api_ms == 1203
        assert usage.turn_usages[0]["reported_nano_aiu"] == 2558740000
    finally:
        observer.close()


@pytest.mark.parametrize("have_file", [True, False])
def test_copilot_unknown_resume_baseline_never_bills_session_history(tmp_path, have_file):
    observer = CopilotUsage(["copilot", "--resume", SESSION], {"HOME": str(tmp_path)})
    try:
        if have_file:
            observer.path.write_text(json.dumps(COPILOT_METRICS))
        usage = observer(TokenUsage(input_tokens=999999, output_tokens=999), [])
        result = account_cost(usage, cli="copilot", model=None, state=state("copilot"))
        assert "input_tokens" not in usage.model_fields_set
        assert result.cost_records[0]["provenance"] == "unavailable"
        assert (
            "baseline" in result.cost_records[0]["reason"]
            or "metrics" in result.cost_records[0]["reason"]
        )
    finally:
        observer.close()


def test_copilot_active_session_is_not_a_verified_baseline(tmp_path):
    path = copilot_baseline(tmp_path)
    with path.open("a") as handle:
        handle.write(json.dumps({"type": "assistant.turn_start", "data": {}}) + "\n")
    observer = CopilotUsage(["copilot", "--resume", SESSION], {"HOME": str(tmp_path)})
    try:
        assert observer.baseline is None
    finally:
        observer.close()


@pytest.mark.parametrize("damage", ["first", "start_data", "last", "shutdown_data"])
def test_malformed_copilot_baseline_does_not_prevent_executor_launch(tmp_path, damage):
    path = copilot_baseline(tmp_path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "first":
        rows[0] = None
    elif damage == "last":
        rows[-1] = None
    elif damage == "start_data":
        rows[0]["data"] = None
    else:
        rows[-1]["data"] = None
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.COPILOT, session_id=SESSION),
                             stream_output=False)
    script = (
        "import sys; from pathlib import Path; "
        "path=Path(sys.argv[sys.argv.index('--usage-output-file')+1]); "
        f"path.write_text({json.dumps(COPILOT_METRICS)!r}); print('ok')"
    )
    response = executor._execute_streaming_process(
        [sys.executable, "-u", "-c", script, "--resume", SESSION], "Copilot",
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert "ok" in response.response
    record, = response.token_usage.cost_records
    assert record["reason"] == "native_baseline_unavailable"
    assert record["provenance"] == "unavailable"
    assert "input_tokens" not in record["usage"]


def test_copilot_model_native_amounts_must_reconcile_with_session_total(tmp_path):
    observer = CopilotUsage(["copilot"], {"HOME": str(tmp_path)})
    try:
        raw = copy.deepcopy(COPILOT_METRICS)
        raw["totalNanoAiu"] += 1
        observer.path.write_text(json.dumps(raw))
        with pytest.raises(ValueError, match="disagree"):
            observer(TokenUsage(), [])
    finally:
        observer.close()


def test_copilot_native_explicit_zero_is_known_money(tmp_path):
    observer = CopilotUsage(["copilot"], {"HOME": str(tmp_path)})
    try:
        raw = copy.deepcopy(COPILOT_METRICS)
        raw["totalNanoAiu"] = 0
        raw["modelMetrics"]["claude-sonnet-5"]["totalNanoAiu"] = 0
        observer.path.write_text(json.dumps(raw))
        result = account_cost(observer(TokenUsage(), []), cli="copilot", model=None)
        assert result.total_cost_usd == 0
        assert summarize_cost(result.cost_records)["counts"]["reported"] == 1
        assert not summarize_cost(result.cost_records)["unknown"]
    finally:
        observer.close()


def test_cursor_current_result_exposes_all_native_token_categories_and_leaves_auto_unknown():
    cli = CursorCLI(AgentConfig(name="fixture", cli=AgentCLI.CURSOR))
    record = {
        "type": "result",
        "usage": {
            "inputTokens": 8646,
            "outputTokens": 30,
            "cacheReadTokens": 3840,
            "cacheWriteTokens": 0,
        },
        "result": "OK",
    }
    _, usage, _ = cli.parse_response([json.dumps(record), json.dumps(record)])
    assert usage.input_tokens == 8646  # Repeated final telemetry is not additive.
    assert usage.cache_read_input_tokens == 3840
    assert "cache_write_input_tokens" in usage.model_fields_set
    result = account_cost(usage, cli="cursor", model="Auto", state=state("cursor"))
    assert result.cost_records[0]["reason"] == "model_unavailable"


@pytest.mark.parametrize("value", [True, -1, 1.5, "100", None])
def test_cursor_invalid_token_counters_are_rejected(value):
    cli = CursorCLI(AgentConfig(name="fixture", cli=AgentCLI.CURSOR))
    with pytest.raises(ValueError):
        cli.parse_response([json.dumps({"type": "result", "usage": {"inputTokens": value}})])


def test_gemini_reconciles_native_thinking_with_stream_stats_before_billing(tmp_path):
    observer = GeminiUsage(["gemini"], {"HOME": str(tmp_path)})
    observer.started_at = 0
    gemini_journal(tmp_path, [gemini_message()])
    cli = GeminiCLI(AgentConfig(name="fixture", cli=AgentCLI.GEMINI))
    _, usage, _ = cli.parse_response([json.dumps(gemini_stream())])
    assert len(usage.turn_usages) == 1  # Zero router metrics are not a billable model.
    assert usage.output_tokens == 1
    enriched = observer(usage, [], SESSION)
    assert enriched.output_tokens == 35 and enriched.reasoning_output_tokens == 34
    result = account_cost(enriched, cli="gemini", model="auto", state=state("gemini"))
    assert result.cost_records[0]["model"] == "gemini-3-flash-preview"
    assert Decimal(result.cost_records[0]["amount_usd"]) == Decimal("0.0051225")
    assert result.cost_records[0]["candidate_output_tokens"] == 1


@pytest.mark.parametrize("damage", ["document", "messages", "message", "timestamp", "row", "patch"])
def test_malformed_gemini_journal_keeps_successful_executor_and_stdout_usage(tmp_path, damage):
    journal = tmp_path / ".gemini/tmp/project/chats" / f"session-test-{SESSION[:8]}.json"
    journal.parent.mkdir(parents=True)
    document = {"sessionId": SESSION, "startTime": "2999-01-01T00:00:00Z",
                "messages": [gemini_message()]}
    if damage == "document":
        document = None
    elif damage == "messages":
        document["messages"] = None
    elif damage == "message":
        document["messages"] = [None]
    elif damage == "timestamp":
        document["startTime"] = None
    if damage in {"row", "patch"}:
        journal = journal.with_suffix(".jsonl")
        journal.write_text(json.dumps(document) + "\n" + json.dumps(
            None if damage == "row" else {"$set": None}
        ) + "\n")
    else:
        journal.write_text(json.dumps(document))
    rows = [{"type": "init", "session_id": SESSION, "model": "auto"}, gemini_stream()]
    script = "import sys; sys.stdout.write(" + repr(
        "".join(json.dumps(row) + "\n" for row in rows)
    ) + ")"
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.GEMINI), stream_output=False)
    response = executor._execute_streaming_process(
        [sys.executable, "-u", "-c", script], "Gemini", parse_stream_json=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert response.transport_result.returncode == 0
    assert response.token_usage.input_tokens == 10035 and response.token_usage.output_tokens == 1
    record, = response.token_usage.cost_records
    assert record["provenance"] == "unavailable"
    assert record["reason"] == "reasoning_usage_unavailable"


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("messages", [None, {}, "invalid", [None]],
                         ids=["null", "object", "string", "invalid_member"])
def test_invalid_gemini_messages_patch_before_append_preserves_success(tmp_path, messages, resume):
    journal = tmp_path / ".gemini/tmp/project/chats" / f"session-test-{SESSION[:8]}.jsonl"
    journal.parent.mkdir(parents=True)
    records = [
        {"sessionId": SESSION, "startTime": "2999-01-01T00:00:00Z"},
        {"$set": {"messages": messages}},
        gemini_message(),
    ]
    journal.write_text("".join(json.dumps(record) + "\n" for record in records))
    rows = [{"type": "init", "session_id": SESSION, "model": "auto"}, gemini_stream()]
    script = "import sys; sys.stdout.write(" + repr(
        "".join(json.dumps(row) + "\n" for row in rows)
    ) + ")"
    command = [sys.executable, "-u", "-c", script]
    if resume:
        command.extend(["--resume", SESSION])
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.GEMINI,
                                       session_id=SESSION if resume else None), stream_output=False)
    response = executor._execute_streaming_process(
        command, "Gemini", parse_stream_json=True, env={**os.environ, "HOME": str(tmp_path)},
    )
    assert response.transport_result.returncode == 0
    assert response.transport_result.completed and response.transport_result.failure_code is None
    assert response.token_usage.input_tokens == 10035 and response.token_usage.output_tokens == 1
    record, = response.token_usage.cost_records
    assert record["provenance"] == "unavailable"
    assert record["reason"] == "reasoning_usage_unavailable"


def test_valid_gemini_messages_patch_still_reconciles_thinking_and_cost(tmp_path):
    old = gemini_message(id="old", input=999999, output=999, thoughts=999)
    gemini_journal(tmp_path, [old, {"$set": {"messages": []}}, gemini_message()])
    observer = GeminiUsage(["gemini"], {"HOME": str(tmp_path)})
    observer.started_at = 0
    cli = GeminiCLI(AgentConfig(name="fixture", cli=AgentCLI.GEMINI))
    usage = cli.parse_response([json.dumps(gemini_stream())])[1]
    enriched = observer(usage, [], SESSION)
    assert enriched.input_tokens == 10035 and enriched.output_tokens == 35
    result = account_cost(enriched, cli="gemini", model="auto", state=state("gemini"))
    record, = result.cost_records
    assert record["provenance"] == "estimated"
    assert Decimal(record["amount_usd"]) == Decimal("0.0051225")


@pytest.mark.parametrize(
    "failure", ["missing", "different_session", "different_counts", "tool_tokens", "old_session"]
)
def test_unverified_gemini_journal_keeps_usage_but_does_not_underestimate_output(tmp_path, failure):
    observer = GeminiUsage(["gemini"], {"HOME": str(tmp_path)})
    observer.started_at = 0 if failure != "old_session" else time.time()
    message = gemini_message()
    if failure == "different_counts":
        message["tokens"]["input"] += 1
    elif failure == "tool_tokens":
        message["tokens"]["tool"] = 10
    if failure != "missing":
        gemini_journal(
            tmp_path, [message], session="other" if failure == "different_session" else SESSION
        )
    cli = GeminiCLI(AgentConfig(name="fixture", cli=AgentCLI.GEMINI))
    usage = cli.parse_response([json.dumps(gemini_stream())])[1]
    enriched = observer(usage, [], SESSION)
    assert enriched.input_tokens == 10035 and enriched.output_tokens == 1
    result = account_cost(enriched, cli="gemini", model="auto", state=state("gemini"))
    assert result.cost_records[0]["reason"] == "reasoning_usage_unavailable"
    assert result.cost_records[0]["amount_usd"] is None


def test_gemini_resume_reads_only_messages_after_prelaunch_baseline(tmp_path):
    old = gemini_message(id="old", input=999999, output=999, thoughts=999)
    gemini_journal(tmp_path, [old])
    observer = GeminiUsage(["gemini", "--resume", SESSION], {"HOME": str(tmp_path)})
    gemini_journal(tmp_path, [old, gemini_message()])
    cli = GeminiCLI(AgentConfig(name="fixture", cli=AgentCLI.GEMINI))
    usage = cli.parse_response([json.dumps(gemini_stream())])[1]
    enriched = observer(usage, [], SESSION)
    assert enriched.input_tokens == 10035 and enriched.output_tokens == 35


def test_gemini_model_records_sum_once_and_preserve_missing_model_coverage():
    known = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 30,
        "reasoning_output_tokens": 5,
    }
    usage = TokenUsage(
        input_tokens=200,
        output_tokens=40,
        cache_read_input_tokens=60,
        reasoning_output_tokens=10,
        duration_ms=20,
        turn_usages=[
            {"model": "gemini-3-flash-preview", "usage": known},
            {"model": "gemini-unknown", "usage": known},
        ],
    )
    result = account_cost(
        usage, cli="gemini", model="auto", state=state("gemini"), invocation_id="attempt"
    )
    assert len(result.cost_records) == 2
    assert summarize_cost(result.cost_records)["estimated"] == Decimal("0.0000965")
    assert summarize_cost(result.cost_records)["unknown"] == 1
    assert summarize_cost(result.cost_records)["incomplete"]
    first = merge_token_usage_stats({}, result)
    assert merge_token_usage_stats(first, result) == first
    assert account_cost(result, cli="gemini", model="auto", invocation_id="attempt") is result


def test_executor_collects_copilot_unique_json_file_and_removes_it_after_execution(
    tmp_path, monkeypatch
):
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.COPILOT))
    captured = tmp_path / "telemetry-path"
    script = (
        "import sys,json; from pathlib import Path; "
        "path=Path(sys.argv[sys.argv.index('--usage-output-file')+1]); "
        f"Path({str(captured)!r}).write_text(str(path)); "
        f"path.write_text({json.dumps(COPILOT_METRICS)!r}); print('OK')"
    )
    response = executor._execute_streaming_process(
        [sys.executable, "-u", "-c", script], "Copilot", parse_stream_json=False
    )
    assert response.response.strip() == "OK"
    assert response.token_usage.input_tokens == 24377
    assert Decimal(response.token_usage.cost_records[0]["amount_usd"]) == Decimal("0.0255874")
    assert not Path(captured.read_text()).exists()


def test_executor_collects_cursor_usage_and_prices_verified_init_model(tmp_path):
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.CURSOR))
    rows = [
        {"type": "system", "subtype": "init", "session_id": SESSION, "model": "Claude 4.6 Sonnet"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": SESSION,
            "usage": {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 30,
                "cacheWriteTokens": 10,
            },
        },
    ]
    script = (
        "import sys; sys.stdout.write("
        + repr("".join(json.dumps(row) + "\n" for row in rows))
        + ")"
    )
    response = executor._execute_streaming_process(
        [sys.executable, "-u", "-c", script], "Cursor", parse_stream_json=True
    )
    assert response.token_usage.input_tokens == 100
    assert response.token_usage.cost_records[0]["provenance"] == "estimated"
    assert response.token_usage.cost_records[0]["model_source"] == "provider"


def test_executor_gemini_uses_observed_session_to_include_thinking(tmp_path):
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.GEMINI))
    journal = (
        tmp_path / ".gemini/tmp/project/chats" / f"session-2026-10-09T06-05-{SESSION[:8]}.jsonl"
    )
    rows = [{"type": "init", "session_id": SESSION, "model": "auto"}, gemini_stream()]
    script = (
        "import json,sys; from pathlib import Path; from datetime import datetime,timezone; "
        f"path=Path({str(journal)!r}); path.parent.mkdir(parents=True); "
        f"metadata={{'sessionId':{SESSION!r},'startTime':datetime.now(timezone.utc).isoformat()}}; "
        f"path.write_text(json.dumps(metadata)+'\\n'+{(json.dumps(gemini_message())+chr(10))!r}); "
        "sys.stdout.write(" + repr("".join(json.dumps(row) + "\n" for row in rows)) + ")"
    )
    response = executor._execute_streaming_process(
        [sys.executable, "-u", "-c", script],
        "Gemini",
        env={**os.environ, "HOME": str(tmp_path)},
        parse_stream_json=True,
    )
    record = response.token_usage.cost_records[0]
    assert response.token_usage.output_tokens == 35
    assert record["model_source"] == "native_journal"
    assert record["session_id"] == SESSION
    assert Decimal(record["amount_usd"]) == Decimal("0.0051225")


def test_copilot_partial_process_failure_keeps_known_reported_cost(tmp_path):
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.COPILOT))
    script = (
        "import sys; from pathlib import Path; "
        "path=Path(sys.argv[sys.argv.index('--usage-output-file')+1]); "
        f"path.write_text({json.dumps(COPILOT_METRICS)!r}); print('partial'); sys.exit(2)"
    )
    with pytest.raises(AgentExecutionError) as caught:
        executor._execute_streaming_process([sys.executable, "-u", "-c", script], "Copilot")
    record = caught.value.accounting_usage.cost_records[0]
    assert record["provenance"] == "reported"
    assert Decimal(record["amount_usd"]) == Decimal("0.0255874")
    assert not record["complete"]


def test_copilot_telemetry_directory_is_removed_when_launch_fails(monkeypatch, tmp_path):
    observers = []

    def prepare(cli, cmd, environment):
        observer = CopilotUsage(cmd, {"HOME": str(tmp_path)})
        observers.append(observer)
        return observer

    monkeypatch.setattr("cafe.agents.cli.provider_usage.prepare_provider_usage", prepare)
    executor = AgentExecutor(AgentConfig(name="fixture", cli=AgentCLI.COPILOT))
    with pytest.raises(AgentExecutionError, match="not found"):
        executor._execute_streaming_process([str(tmp_path / "missing-cli")], "Copilot")
    assert len(observers) == 1
    assert not observers[0].path.parent.exists()
