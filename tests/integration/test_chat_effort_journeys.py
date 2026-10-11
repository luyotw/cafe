"""Issue #408 U3/U5/I3/I5: current chat tuples reach the native transport."""

import json
import sys

import pytest
import yaml

from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI
from cafe.ui.chat import launch_chat_session
from tests.integration.test_chat_read_only import diagnostic_workspace
from tests.unit.test_chat_read_only import inventory
from tests.unit.test_reasoning_effort_adapters import install_provider_clients


@pytest.fixture
def chat_provider(diagnostic_workspace, monkeypatch):
    repo, issue, _ = diagnostic_workspace
    clients = repo.parent / "clients"
    clients.mkdir()
    install_provider_clients(clients, monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)
    capture = clients / "launches.jsonl"
    monkeypatch.setenv("CHAT_CAPTURE", str(capture))
    binary = clients / "bin/claude"
    binary.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
if '--version' in sys.argv:
    print('2.1.289')
    sys.exit(0)
args = sys.argv[1:]
with open(os.environ['CHAT_CAPTURE'], 'a') as stream:
    stream.write(json.dumps({'argv': args, 'effort_env': os.environ.get('CLAUDE_CODE_EFFORT_LEVEL')}) + '\\n')
effort = args[args.index('--effort') + 1] if '--effort' in args else None
model = args[args.index('--model') + 1]
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'chat-session', 'model': model, 'effortLevel': effort}))
print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'chat complete'}]}}))
print(json.dumps({'type': 'result', 'subtype': 'success', 'result': 'chat complete', 'session_id': 'chat-session'}))
""")
    return repo, issue, capture


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
def test_chat_launch_uses_current_selected_effort_and_preserves_session_authority(
    chat_provider, fallback, interactive, explicit, read_only
):
    repo, issue, capture = chat_provider
    selected = {"cli": "claude", "model": "claude-opus-4-6"}
    initial_effort = "low" if fallback else "high"
    if explicit:
        selected["reasoning_effort"] = initial_effort
    entries = [selected]
    if fallback:
        entries.insert(0, {"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "xhigh"})
        SessionManager().save_session("Ada", AgentCLI.CLAUDE, "chat-session", "issue520", "inspect")

    def configure():
        (repo / ".cafe/phases.yaml").write_text(yaml.safe_dump({
            "inspect": {"role": "analyst", "name": "Ada", "clis": entries}
        }))

    def launch(expected_effort, resumed):
        before = inventory(repo)
        assert launch_chat_session(
            "analyst", "issue520", phase_name="inspect", read_only=read_only,
            **({"initial_prompt": "inspect"} if interactive else {"prompt": "inspect"}),
        ) == 0
        record = json.loads(capture.read_text().splitlines()[-1])
        command = record["argv"]
        assert command[command.index("--model") + 1] == selected["model"]
        if expected_effort is None:
            assert "--effort" not in command
            assert record["effort_env"] is None
        else:
            assert command[command.index("--effort") + 1] == expected_effort
            assert record["effort_env"] == expected_effort
        if resumed:
            assert command[command.index("--resume") + 1] == "chat-session"
        if read_only:
            assert command[command.index("--permission-mode") + 1] == "plan"
            assert inventory(repo) == before

    configure()
    launch(initial_effort if explicit else None, fallback)
    if explicit:
        selected["reasoning_effort"] = "high" if fallback else "low"
        configure()
        launch(selected["reasoning_effort"], fallback or (not read_only and not interactive))


@pytest.mark.parametrize("interactive", [False, True])
def test_chat_sticky_cli_uses_current_configured_tuple(chat_provider, interactive):
    repo, issue, capture = chat_provider
    (repo / ".cafe/phases.yaml").write_text(yaml.safe_dump({
        "inspect": {"role": "analyst", "name": "Ada", "clis": [
            {"cli": "codex", "model": "gpt-5.5", "reasoning_effort": "xhigh"},
            {"cli": "claude", "model": "claude-opus-4-6", "reasoning_effort": "low"},
        ]}
    }))
    (issue / "active_clis.json").write_text(json.dumps({"Ada": {
        "cli": "claude", "model": "old-model", "reasoning_effort": "high",
        "configured_primary": "codex", "chain": ["codex", "claude"],
    }}))
    before = inventory(repo)
    assert launch_chat_session(
        "analyst", "issue520", read_only=True,
        **({"initial_prompt": "inspect"} if interactive else {"prompt": "inspect"}),
    ) == 0
    record = json.loads(capture.read_text().splitlines()[-1])
    command = record["argv"]
    assert command[command.index("--model") + 1] == "claude-opus-4-6"
    assert command[command.index("--effort") + 1] == "low"
    assert record["effort_env"] == "low"
    assert inventory(repo) == before
