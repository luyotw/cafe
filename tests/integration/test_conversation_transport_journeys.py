"""Real caller composition with process doubles (Plan I1–I6/U8–U9)."""

import json
from unittest.mock import MagicMock

import pytest
import yaml

from cafe.agents.transport import ConversationTransport
from cafe.core.blackboard import BlackboardStore
from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI
from cafe.ui import chat
from tests.unit.test_conversation_transport import provider_process, init
from tests.unit.test_event_driver_callback import _callback_module, _v3_event_context, _contract_event_context


@pytest.fixture
def phase_chat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAFE_MOCK_AGENTS", raising=False)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global")
    monkeypatch.setattr(chat, "get_git_toplevel", lambda: tmp_path)
    monkeypatch.setattr(chat, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(chat, "_prepare_chat_environment", lambda **_kwargs: None)
    monkeypatch.setattr(chat.PlaybookLoader, "load", lambda *args: {
        "entry": "implementation", "steps": {"implementation": {"role": "developer"}}})
    issue = tmp_path / ".cafe/issues/x"
    metadata = issue / "implementation/iteration_004/iteration.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps(dict(iteration=4, timestamp="pinned", other="kept")))
    (tmp_path / ".cafe/phases.yaml").write_text(yaml.safe_dump({"implementation": {
        "name": "David", "role": "developer", "clis": [{"cli": "claude", "model": "selected"}]}}))
    BlackboardStore(issue).load_or_create("implementation")
    return issue, metadata


def test_interactive_custom_phase_keeps_configured_chain_and_terminal(phase_chat, monkeypatch):
    issue, metadata = phase_chat
    SessionManager().save_session("David", AgentCLI.CODEX, "removed", "x", "implementation")
    launch = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("subprocess.run", launch)
    calls = []
    original = ConversationTransport.open_interactive_session
    def observe(self, *args, **kwargs):
        calls.append(self.executor.config)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ConversationTransport, "open_interactive_session", observe)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 0
    assert len(calls) == 1
    assert calls[0].cli == AgentCLI.CLAUDE and calls[0].model == "selected"
    assert "removed" not in launch.call_args.args[0]
    assert launch.call_args.kwargs["env"]["CAFE_CHAT_PHASE"] == "implementation"
    assert json.loads(metadata.read_text())["other"] == "kept"


def test_one_shot_custom_phase_streams_saves_and_accounts_once(phase_chat, provider_process, capsys, monkeypatch):
    issue, metadata = phase_chat
    provider_process([init("new", model="selected"), dict(type="result", content="reply", usage=dict(input_tokens=3))])
    calls = []
    original = ConversationTransport.run_one_shot
    def observe(self, *args, **kwargs):
        calls.append(True)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ConversationTransport, "run_one_shot", observe)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    assert calls == [True]
    assert capsys.readouterr().out.count("reply") == 1
    assert SessionManager().load_session("David", AgentCLI.CLAUDE, "x", "implementation").session_id == "new"
    assert json.loads(metadata.read_text())["stats"]["input_tokens"] == 3


@pytest.mark.parametrize("failure", ["stale", "prompt_too_long"])
def test_chat_authorizes_stale_session_recovery_one_transport_call_per_attempt(phase_chat, provider_process, monkeypatch, failure):
    issue, metadata = phase_chat
    SessionManager().save_session("David", AgentCLI.CLAUDE, "stale", "x", "implementation")
    first = provider_process([], returncode=1, stderr="no conversation found") if failure == "stale" else provider_process([
        dict(error="invalid_request", message=dict(content=[dict(type="text", text="prompt is too long")]))])
    failed = first.return_value
    second = provider_process([init("new", model="selected"), dict(type="result", content="done", usage=dict(input_tokens=1))])
    good = second.return_value
    second.side_effect = [failed, good]
    calls = []
    original = ConversationTransport.run_one_shot
    def observe(self, *args, **kwargs):
        calls.append(self.executor.config.session_id)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ConversationTransport, "run_one_shot", observe)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    assert calls == ["stale", ""]
    assert second.call_count == 2
    assert "--resume" in second.call_args_list[0].args[0]
    assert "--resume" not in second.call_args_list[1].args[0]


def _callback_processes(provider_process, event, *, failed_output=False):
    launch = provider_process([init("bound", model="exact"), dict(type="result", usage=dict(input_tokens=2))])
    bootstrap = launch.return_value
    provider_process([init("bound", model="exact"), dict(type="stream_event", event=dict(type="message_start")),
        dict(type="result", usage=dict(input_tokens=3))], returncode=1 if failed_output else 0)
    delivery = launch.return_value
    launch.side_effect = [bootstrap, delivery]
    return launch, delivery


@pytest.mark.parametrize("failed_output", [False, True])
def test_callback_persists_acquisition_and_acceptance_before_output_finishes(tmp_path, monkeypatch, provider_process, failed_output):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    callback = _callback_module()
    directory, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact")])
    target = directory.parent / event["step"] / "iteration_004/iteration.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(dict(iteration=4, timestamp="2020-01-01T00:00:00+00:00", other="kept")))
    future = target.parent.parent / "iteration_005/iteration.json"
    future.parent.mkdir()
    future.write_text(json.dumps(dict(iteration=5, timestamp="2099-01-01T00:00:00+00:00")))
    launch, delivery = _callback_processes(provider_process, event, failed_output=failed_output)
    calls = []
    acquire = ConversationTransport.acquire_session
    deliver = ConversationTransport.deliver_to_exact_session
    def acquire_observer(self, *args, **kwargs):
        persisted = json.loads((directory / "dispatch_state.json").read_text())
        assert persisted["events"][event["event_id"]]["attempts"][-1]["status"] == "pending"
        calls.append("acquire")
        return acquire(self, *args, **kwargs)
    def deliver_observer(self, *args, **kwargs):
        persisted = json.loads((directory / "dispatch_state.json").read_text())
        assert persisted["entries"][0]["session"]["id"] == "bound"
        calls.append("deliver")
        return deliver(self, *args, **kwargs)
    monkeypatch.setattr(ConversationTransport, "acquire_session", acquire_observer)
    monkeypatch.setattr(ConversationTransport, "deliver_to_exact_session", deliver_observer)
    original_read = delivery.stdout.readline.side_effect
    records = iter(original_read)
    line_number = 0
    def read():
        nonlocal line_number
        line_number += 1
        if line_number == 3:
            persisted = json.loads((directory / "dispatch_state.json").read_text())
            assert persisted["events"][event["event_id"]]["status"] == "accepted"
        return next(records)
    delivery.stdout.readline.side_effect = read
    updated = callback._run_v3_callback(directory, state, event, repository_root=tmp_path)
    assert calls == ["acquire", "deliver"]
    assert launch.call_count == 2
    assert updated["events"][event["event_id"]]["status"] == "accepted"
    assert json.loads(target.read_text())["stats"]["input_tokens"] == 5
    assert "stats" not in json.loads(future.read_text())
    callback._run_v3_callback(directory, updated, event, repository_root=tmp_path)
    assert launch.call_count == 2


@pytest.mark.parametrize("problem", ["model", "session", "truncated", "write"])
def test_callback_uncertainty_never_replays_or_falls_forward(tmp_path, monkeypatch, provider_process, problem):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    callback = _callback_module()
    directory, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact"), ("gemini", "later")])
    launch, delivery = _callback_processes(provider_process, event)
    if problem != "write":
        records = [init("other" if problem == "session" else "bound", model="other" if problem == "model" else "exact")]
        if problem != "truncated":
            records += [dict(type="result")]
        delivery.stdout.readline.side_effect = [json.dumps(record) + "\n" for record in records] + [""]
    else:
        monkeypatch.setattr(callback, "_accept_delivery", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")))
    if problem == "write":
        with pytest.raises(OSError):
            callback._run_v3_callback(directory, state, event, repository_root=tmp_path)
        updated = callback._load_or_initialize_dispatch_state(directory, workflow_id=state["workflow_id"], config=callback._load_config(directory))
    else:
        updated = callback._run_v3_callback(directory, state, event, repository_root=tmp_path)
        assert updated["events"][event["event_id"]]["recovery_pending"] is True
    assert launch.call_count == 2
    callback._run_v3_callback(directory, updated, event, repository_root=tmp_path)
    assert launch.call_count == 2


def test_callback_only_conclusive_rejection_moves_to_next_provider(tmp_path, monkeypatch, provider_process):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    callback = _callback_module()
    directory, state, event = _v3_event_context(callback, tmp_path, [("claude", "exact"), ("gemini", "later")])
    launch = provider_process([])
    provider_process([dict(type="init", session_id="g", model="later"), dict(type="result")])
    bootstrap = launch.return_value
    provider_process([dict(type="init", session_id="g", model="later"),
        dict(type="message", role="user", content=event["event_id"]), dict(type="result")])
    delivery = launch.return_value
    launch.side_effect = [FileNotFoundError("claude"), bootstrap, delivery]
    updated = callback._run_v3_callback(directory, state, event, repository_root=tmp_path)
    assert updated["active_index"] == 1
    assert updated["events"][event["event_id"]]["status"] == "accepted"
    assert launch.call_count == 3


def test_public_callback_uses_confirmed_transport_and_accounts_existing_iteration(tmp_path, monkeypatch, provider_process):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    callback = _callback_module()
    directory, state, event = _contract_event_context(callback, tmp_path, [("claude", "exact")])
    target = directory.parent / "develop/iteration_007/iteration.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(dict(iteration=7, timestamp="2020-01-01T00:00:00+00:00", other="kept")))
    launch, _delivery = _callback_processes(provider_process, event)
    callback.run_callback(event, repository_root=tmp_path)
    persisted = json.loads((directory / "dispatch_state.json").read_text())
    assert persisted["events"][event["event_id"]]["status"] == "accepted"
    assert json.loads(target.read_text())["stats"]["input_tokens"] == 5
    callback.run_callback(event, repository_root=tmp_path)
    assert launch.call_count == 2
    assert json.loads(target.read_text())["stats"]["input_tokens"] == 5
