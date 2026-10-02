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
