"""Public transport invariants (Plan U1–U3, U7)."""

import json
from dataclasses import fields
from unittest.mock import MagicMock

import pytest

from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.agents.transport import ConversationTransport
from cafe.agents.transport_types import TransportResult
from cafe.core.types import AgentCLI, AgentConfig


@pytest.fixture
def provider_process(monkeypatch):
    """Replace only the external process and terminal polling boundary."""
    monkeypatch.setattr("sys.platform", "win32")
    launch = MagicMock()
    monkeypatch.setattr("subprocess.Popen", launch)

    def supply(records, returncode=0, stderr=""):
        process = MagicMock()
        process.stdout.readline.side_effect = [json.dumps(r) + "\n" for r in records] + [""]
        process.stderr.read.return_value = stderr
        process.wait.return_value = returncode
        process.poll.return_value = None
        launch.return_value = process
        return launch

    return supply


def transport(cli=AgentCLI.CLAUDE, model=None, session_id=None):
    return ConversationTransport(AgentExecutor(
        AgentConfig(name="conversation", cli=cli, model=model, session_id=session_id),
        stream_output=False,
    ))


def init(session="s", **kwargs):
    return dict(type="system", subtype="init", session_id=session, **kwargs)


def test_unsupported_guarantee_never_launches(provider_process):
    launch = provider_process([])
    with pytest.raises(AgentExecutionError) as caught:
        transport().open_interactive_session(required_evidence=frozenset({"session"}))
    assert caught.value.transport_result.failure_code == "unsupported"
    assert caught.value.transport_result.accepted is False
    launch.assert_not_called()


@pytest.mark.parametrize("cli,identity", [
    (AgentCLI.CLAUDE, init()),
    (AgentCLI.CODEX, dict(type="thread.started", thread_id="s")),
    (AgentCLI.GEMINI, dict(type="init", session_id="s")),
    (AgentCLI.CURSOR, init()),
    (AgentCLI.COPILOT, dict(type="result", sessionId="s")),
])
def test_acquisition_uses_only_verified_provider_identity(provider_process, cli, identity):
    records = [identity]
    if cli != AgentCLI.COPILOT:
        records.append(dict(type="turn.completed" if cli == AgentCLI.CODEX else "result"))
    launch = provider_process(records)
    result = transport(cli).acquire_session('say "HI"')
    assert result.observed_session_id == "s"
    assert result.reported_model is None
    assert result.usage is None
    assert result.accepted is False
    assert result.completed is True
    assert launch.call_count == 1


@pytest.mark.parametrize("records", [
    [dict(type="assistant", session_id="s"), dict(type="result")],
    [init("a"), init("b"), dict(type="result")],
    [init("s" * 513), dict(type="result")],
    [init()],
])
def test_invalid_or_incomplete_acquisition_cannot_succeed(provider_process, records):
    launch = provider_process(records)
    with pytest.raises(AgentExecutionError) as caught:
        transport().acquire_session("bootstrap")
    assert caught.value.transport_result.failure_code
    assert caught.value.transport_result.accepted is False
    assert launch.call_count == 1


@pytest.mark.parametrize("identity", [init("other"), init(model="other-model")])
def test_exact_mismatch_has_no_acceptance_or_replacement(provider_process, identity):
    launch = provider_process([identity, dict(type="stream_event", event=dict(type="message_start")), dict(type="result")])
    selected = transport(model="selected", session_id="s")
    with pytest.raises(AgentExecutionError) as caught:
        selected.deliver_to_exact_session("event-1", "s", "event-1")
    assert caught.value.transport_result.accepted is not True
    assert selected.executor.config.session_id == "s"
    assert selected.executor.config.model == "selected"
    assert launch.call_count == 1


def test_results_are_compact_and_diagnostics_bounded(provider_process):
    provider_process([init(), dict(type="result")], returncode=1, stderr="x" * 1000)
    with pytest.raises(AgentExecutionError) as caught:
        transport().acquire_session("bootstrap")
    result = caught.value.transport_result
    assert len(result.error_excerpt) <= 400
    assert {f.name for f in fields(TransportResult)} == {
        "observed_session_id", "reported_model", "accepted", "completed", "usage",
        "failure_code", "error_excerpt", "returncode",
    }


def test_acceptance_precedes_output_failure_and_keeps_partial_usage(provider_process):
    provider_process([init(), dict(type="stream_event", event=dict(type="message_start")),
                      dict(type="result", usage=dict(input_tokens=7, output_tokens=0))],
                     returncode=1, stderr="output failed")
    accepted = []
    usages = []
    selected = transport(session_id="s")
    with pytest.raises(AgentExecutionError) as caught:
        selected.deliver_to_exact_session("event-1", "s", "event-1",
            on_acceptance=lambda: accepted.append(True), on_usage=usages.append)
    result = caught.value.transport_result
    assert accepted == [True]
    assert result.accepted is True
    assert result.completed is True
    assert result.returncode == 1
    assert result.usage.input_tokens == 7
    assert len(usages) == 1
    assert selected.executor.get_total_token_usage().input_tokens == 7


def test_acceptance_observer_error_is_not_provider_rejection(provider_process):
    launch = provider_process([init(), dict(type="stream_event", event=dict(type="message_start"))])
    problem = OSError("caller persistence failed")
    def reject():
        raise problem
    with pytest.raises(OSError) as caught:
        transport(session_id="s").deliver_to_exact_session("event-1", "s", "event-1", on_acceptance=reject)
    assert caught.value is problem
    assert launch.call_count == 1
    launch.return_value.terminate.assert_called_once()


@pytest.mark.parametrize("cli,records", [
    (AgentCLI.CLAUDE, [init(), dict(type="stream_event", event=dict(type="message_start"), event_id="other")]),
    (AgentCLI.CODEX, [dict(type="thread.started", thread_id="s"), dict(type="turn.started", event_id="other")]),
    (AgentCLI.GEMINI, [dict(type="init", session_id="s"), dict(type="message", role="user", content="other")]),
    (AgentCLI.CURSOR, [init(), dict(type="user", message="other")]),
    (AgentCLI.COPILOT, [dict(type="user.message", data="other"), dict(type="result", sessionId="s")]),
])
def test_unrelated_acknowledgements_cannot_accept_delivery(provider_process, cli, records):
    if cli != AgentCLI.COPILOT:
        records += [dict(type="turn.completed" if cli == AgentCLI.CODEX else "result")]
    provider_process(records)
    observed = []
    result = transport(cli, session_id="s").deliver_to_exact_session(
        "event-1", "s", "event-1", on_acceptance=lambda: observed.append(True))
    assert result.accepted is False
    assert observed == []


@pytest.mark.parametrize("cli,identity,terminal,usage_field,usage", [
    (AgentCLI.CLAUDE, init(), "result", "usage", dict(input_tokens=0, output_tokens=0)),
    (AgentCLI.CODEX, dict(type="thread.started", thread_id="s"), "turn.completed", "usage", dict(input_tokens=0, output_tokens=0)),
    (AgentCLI.GEMINI, dict(type="init", session_id="s"), "result", "stats", dict(input_tokens=0, output_tokens=0)),
    (AgentCLI.CURSOR, init(), "result", "duration_ms", 0),
])
def test_reported_zero_is_distinct_from_missing_usage(provider_process, cli, identity, terminal, usage_field, usage):
    provider_process([identity, dict(type=terminal, **{usage_field: usage})])
    selected = transport(cli)
    result = selected.acquire_session("bootstrap")
    assert result.usage is not None
    assert result.usage.input_tokens == 0


def test_prefix_conflict_prevents_acceptance_and_later_conflict_retains_it(provider_process):
    for later in (False, True):
        acknowledgement = dict(type="stream_event", event=dict(type="message_start"))
        middle = [acknowledgement, init("other")] if later else [init("other"), acknowledgement]
        provider_process([init(), *middle, dict(type="result")])
        observed = []
        with pytest.raises(AgentExecutionError) as caught:
            transport(session_id="s").deliver_to_exact_session("event-1", "s", "event-1",
                on_acceptance=lambda: observed.append(True))
        assert observed == ([True] if later else [])
        assert caught.value.transport_result.accepted is later


def test_model_conflict_in_acknowledgement_prevents_acceptance(provider_process):
    provider_process([init(model="selected"), dict(type="stream_event", event=dict(
        type="message_start", message=dict(model="other"))), dict(type="result")])
    observed = []
    with pytest.raises(AgentExecutionError) as caught:
        transport(model="selected", session_id="s").deliver_to_exact_session(
            "event-1", "s", "event-1", on_acceptance=lambda: observed.append(True))
    assert observed == []
    assert caught.value.transport_result.failure_code == "model_mismatch"
