"""Chat cost coverage through real callers/providers (Plan U6/U8/U11/I1/I2/I6)."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI
from cafe.services.status_service import StatusService
from cafe.ui import chat
from tests.integration.test_conversation_transport_journeys import phase_chat
from tests.unit.test_conversation_transport import provider_process, init


def groups(metadata):
    return json.loads(metadata.read_text())["chat_usage"]


@pytest.fixture
def terminal(monkeypatch):
    original = subprocess.run

    def install(launch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kwargs: (
                original(command, **kwargs) if command[0] == "git" else launch(command, **kwargs)
            ),
        )

    return install


@pytest.mark.parametrize("reported", ["actual", None])
def test_one_shot_keeps_reported_model_and_unknown_fields(phase_chat, provider_process, reported):
    _issue, target = phase_chat
    records = [
        init("new", **({"model": reported} if reported else {})),
        dict(type="result", usage=dict(input_tokens=3), content="reply"),
    ]
    provider_process(records)
    chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello")
    (group,) = groups(target)
    assert group["cli"] == "claude"
    assert group["requested_model"] == "selected"
    assert group["reported_model"] == reported
    assert group["stats"] == {"input_tokens": 3}
    assert group["incomplete_calls"] == 1
    assert "total_cost_usd" in group["unknown_fields"]
    assert group["calls"] == 1


@pytest.mark.parametrize("mode", ["interactive", "one_shot"])
def test_chat_without_iteration_uses_existing_issue_metadata(
    phase_chat, provider_process, terminal, mode
):
    issue, target = phase_chat
    target.unlink()
    config = issue / "issue.yaml"
    config.write_text(yaml.safe_dump({"issue_name": "x", "existing": "kept"}))
    if mode == "interactive":
        terminal(lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    else:
        provider_process(
            [init("new", model="selected"), dict(type="result", usage={"input_tokens": 2})]
        )
    kwargs = {"prompt": "hello"} if mode == "one_shot" else {}
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", **kwargs) == 0
    data = yaml.safe_load(config.read_text())
    assert data["existing"] == "kept"
    assert data["chat_usage"][0]["mode"] == mode
    assert data["chat_usage"][0]["incomplete_calls"] == 1
    assert not target.exists()
    assert list(issue.glob("**/iteration.json")) == []


@pytest.mark.parametrize("mode", ["interactive", "one_shot"])
def test_missing_targets_are_visible_without_inventing_authority(
    phase_chat, provider_process, terminal, capsys, mode
):
    _issue, target = phase_chat
    target.unlink()
    launch = provider_process([])
    interactive = []

    def run(*a, **kw):
        interactive.append(a)
        return subprocess.CompletedProcess(a, 0)

    terminal(run)
    kwargs = {"prompt": "hello"} if mode == "one_shot" else {}
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", **kwargs) == (
        0 if mode == "interactive" else 1
    )
    assert launch.call_count == 0
    assert len(interactive) == (1 if mode == "interactive" else 0)
    assert "incomplete" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("returncode", [0, 7])
def test_interactive_native_usage_excludes_history_and_deduplicates_snapshots(
    phase_chat, monkeypatch, terminal, returncode
):
    issue, target = phase_chat
    native = issue.parent.parent.parent / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))
    session = "b3a45cc1-5221-4ee6-9fc3-5517448c7c14"
    SessionManager().save_session("David", AgentCLI.CLAUDE, session, "x", "implementation")
    journal = (
        native / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd())) / (session + ".jsonl")
    )
    journal.parent.mkdir(parents=True)

    def record(identity, tokens, model="actual"):
        return {
            "type": "assistant",
            "sessionId": session,
            "message": {
                "id": identity,
                "model": model,
                "content": "PRIVATE",
                "usage": {"input_tokens": tokens, "output_tokens": 2},
            },
        }

    historical = record("historical", 100)
    journal.write_text(json.dumps(historical) + "\n")

    def launch(command, **kwargs):
        assert set(kwargs) == {"env"}  # The terminal is inherited, never captured.
        assert "--resume" in command and session in command
        with journal.open("a") as handle:
            for entry in [
                historical,
                record("current", 3),
                record("current", 4),
                record("other", 5, "second"),
            ]:
                handle.write(json.dumps(entry) + "\n")
        return subprocess.CompletedProcess(command, returncode)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == returncode
    recorded = {group["reported_model"]: group for group in groups(target)}
    assert recorded["actual"]["stats"]["input_tokens"] == 4
    assert recorded["second"]["stats"]["input_tokens"] == 5
    assert all(group["incomplete_calls"] == 1 for group in recorded.values())
    assert "PRIVATE" not in target.read_text()
    assert "historical" not in target.read_text()


def test_interactive_new_session_has_bound_native_source(phase_chat, monkeypatch, terminal):
    _issue, target = phase_chat
    native = Path.cwd() / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))

    def launch(command, **kwargs):
        session = command[command.index("--session-id") + 1]
        journal = (
            native
            / "projects"
            / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd()))
            / (session + ".jsonl")
        )
        journal.parent.mkdir(parents=True)
        journal.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session,
                    "message": {"id": "new", "model": "actual", "usage": {"input_tokens": 6}},
                }
            )
            + "\n"
        )
        return subprocess.CompletedProcess(command, 0)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 0
    (group,) = groups(target)
    assert group["reported_model"] == "actual" and group["stats"]["input_tokens"] == 6


def test_status_keeps_chat_model_and_missing_cost_visible(phase_chat, provider_process):
    issue, target = phase_chat
    provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 3})]
    )
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    )
    service = StatusService(issues_root=issue.parent)
    loaded = service.load_chat_usage("x", ["implementation"])
    assert loaded[0]["stats"] == {"input_tokens": 3}
    assert "total_cost_usd" in loaded[0]["unknown_fields"]
    # Chat must not also be billed as the phase's requested model.
    iterations = service.load_iteration_statuses("x", "implementation")
    assert iterations[0]["stats"]["input_tokens"] == 0


@pytest.mark.parametrize("failure", [False, True])
def test_one_shot_partial_result_and_cumulative_totals_are_saved_once(
    phase_chat, provider_process, failure
):
    _issue, target = phase_chat
    provider_process(
        [
            init("new", model="selected"),
            dict(type="assistant", usage={"input_tokens": 2}, total_cost_usd=0.1),
            dict(type="result", usage={"input_tokens": 5}, total_cost_usd=0.3),
        ],
        returncode=1 if failure else 0,
    )
    assert chat.launch_chat_session(
        "developer", "x", phase_name="implementation", prompt="hello"
    ) == int(failure)
    (group,) = groups(target)
    assert group["stats"]["input_tokens"] == 5 and group["stats"]["total_cost_usd"] == 0.3
    assert group["calls"] == 1
    assert json.loads(target.read_text())["stats"]["input_tokens"] == 5


def test_recovery_accounts_each_physical_attempt_without_replaying_success(
    phase_chat, provider_process
):
    _issue, target = phase_chat
    SessionManager().save_session("David", AgentCLI.CLAUDE, "stale", "x", "implementation")
    first = provider_process(
        [init("stale", model="selected"), dict(type="result", usage={"input_tokens": 2})],
        returncode=1,
        stderr="no conversation found",
    )
    failed = first.return_value
    launch = provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 3})]
    )
    successful = launch.return_value
    launch.side_effect = [failed, successful]
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    )
    (group,) = groups(target)
    assert group["stats"]["input_tokens"] == 5 and group["calls"] == 2
    assert launch.call_count == 2


@pytest.mark.parametrize("cli", ["codex", "copilot", "gemini", "cursor-agent"])
@pytest.mark.parametrize("failure", [False, True])
def test_interactive_without_native_reader_is_durably_incomplete(
    phase_chat, terminal, cli, failure
):
    _issue, target = phase_chat
    config = Path.cwd() / ".cafe/phases.yaml"
    data = yaml.safe_load(config.read_text())
    data["implementation"]["clis"] = [{"cli": cli, "model": "selected"}]
    config.write_text(yaml.safe_dump(data))
    terminal(lambda command, **kwargs: subprocess.CompletedProcess(command, 7 if failure else 0))
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == (
        7 if failure else 0
    )
    (group,) = groups(target)
    assert group["cli"] == cli and group["requested_model"] == "selected"
    assert group["reported_model"] is None and group["stats"] == {}
    assert group["calls"] == 1 and group["incomplete_calls"] == 1


def test_native_malformed_tail_keeps_verified_partial_usage(phase_chat, terminal, monkeypatch):
    _issue, target = phase_chat
    native = Path.cwd() / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))

    def launch(command, **kwargs):
        session = command[command.index("--session-id") + 1]
        journal = (
            native
            / "projects"
            / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd()))
            / (session + ".jsonl")
        )
        journal.parent.mkdir(parents=True)
        journal.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session,
                    "message": {"id": "new", "model": "actual", "usage": {"input_tokens": 6}},
                }
            )
            + "\n{broken"
        )
        return subprocess.CompletedProcess(command, 7)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 7
    (group,) = groups(target)
    assert group["reported_model"] == "actual" and group["stats"]["input_tokens"] == 6
    assert group["incomplete_calls"] == 1


@pytest.mark.parametrize("error", [FileNotFoundError("missing cli"), OSError("failed launch")])
@pytest.mark.parametrize("mode", ["interactive", "one_shot"])
def test_chat_launch_failure_leaves_one_explicit_gap(
    phase_chat, terminal, provider_process, monkeypatch, mode, error
):
    _issue, target = phase_chat

    def fail(*args, **kwargs):
        raise error

    if mode == "interactive":
        terminal(fail)
    else:
        provider_process([]).side_effect = error
    kwargs = {"prompt": "hello"} if mode == "one_shot" else {}
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", **kwargs) == 1
    (group,) = groups(target)
    assert group["calls"] == 1 and group["incomplete_calls"] == 1
    assert group["reported_model"] is None and group["stats"] == {}


def test_resumed_native_source_without_baseline_is_never_billed_as_current(
    phase_chat, terminal, monkeypatch
):
    _issue, target = phase_chat
    native = Path.cwd() / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))
    session = "b3a45cc1-5221-4ee6-9fc3-5517448c7c14"
    SessionManager().save_session("David", AgentCLI.CLAUDE, session, "x", "implementation")

    def launch(command, **kwargs):
        journal = (
            native
            / "projects"
            / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd()))
            / (session + ".jsonl")
        )
        journal.parent.mkdir(parents=True)
        journal.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session,
                    "message": {"id": "historical", "model": "old", "usage": {"input_tokens": 900}},
                }
            )
            + "\n"
        )
        return subprocess.CompletedProcess(command, 0)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 0
    (group,) = groups(target)
    assert group["stats"] == {} and group["reported_model"] is None
    assert group["incomplete_calls"] == 1


def test_public_status_consumes_cost_coverage_without_writing_authority(
    phase_chat, provider_process, monkeypatch
):
    from typer.testing import CliRunner
    from cafe.ui.cli import app
    from cafe.services import status_display

    issue, target = phase_chat
    provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 3})]
    )
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    )
    monkeypatch.setattr(StatusService, "get_current_issue", lambda self: "x")
    monkeypatch.setattr(
        "cafe.ui.commands.workflow._load_issue_step_names", lambda _: ["implementation"]
    )
    monkeypatch.setattr(status_display, "RICH_AVAILABLE", False)
    before = {str(path): path.read_bytes() for path in issue.rglob("*") if path.is_file()}
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "selected" in result.stdout and "unknown" in result.stdout
    assert "incomplete" in result.stdout and "one_shot" in result.stdout
    assert before == {str(path): path.read_bytes() for path in issue.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "cli,records,stderr,expected",
    [
        (
            "codex",
            [
                {"type": "thread.started", "thread_id": "new"},
                {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 0}},
            ],
            "",
            {"input_tokens": 3, "output_tokens": 0},
        ),
        (
            "gemini",
            [
                {"type": "init", "session_id": "new", "model": "selected"},
                {"type": "result", "stats": {"input_tokens": 3}},
            ],
            "",
            {"input_tokens": 3},
        ),
        (
            "cursor-agent",
            [init("new", model="selected"), {"type": "result", "duration_ms": 10}],
            "",
            {},
        ),
        (
            "copilot",
            [],
            "Breakdown by AI model:\n  selected 3 in, 0 out\n",
            {"input_tokens": 3, "output_tokens": 0},
        ),
    ],
)
def test_one_shot_uses_existing_each_provider_usage_parser(
    phase_chat, provider_process, cli, records, stderr, expected
):
    _issue, target = phase_chat
    config = Path.cwd() / ".cafe/phases.yaml"
    data = yaml.safe_load(config.read_text())
    data["implementation"]["clis"] = [{"cli": cli, "model": "selected"}]
    config.write_text(yaml.safe_dump(data))
    provider_process(records, stderr=stderr)
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    )
    (group,) = groups(target)
    assert group["stats"] == expected
    assert group["reported_model"] == (None if cli == "codex" else "selected")
    assert group["incomplete_calls"] == 1 and "total_cost_usd" in group["unknown_fields"]
    if cli == "cursor-agent":
        assert json.loads(target.read_text())["stats"]["duration_ms"] == 10


def test_usage_publication_failure_never_retries_stale_provider_attempt(
    phase_chat, provider_process, monkeypatch, capsys
):
    import cafe.core.usage as usage_module

    _issue, target = phase_chat
    SessionManager().save_session("David", AgentCLI.CLAUDE, "stale", "x", "implementation")
    launch = provider_process(
        [init("stale", model="selected"), dict(type="result", usage={"input_tokens": 2})],
        returncode=1,
        stderr="no conversation found",
    )

    def fail(*args, **kwargs):
        raise OSError("cannot publish usage")

    monkeypatch.setattr(usage_module, "_exchange_usage_file", fail)
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 1
    )
    assert launch.call_count == 1 and "incomplete" in capsys.readouterr().out.lower()
    assert "chat_usage" not in json.loads(target.read_text())
    assert list(target.parent.glob(".usage-*")) == []


@pytest.mark.parametrize("state", ["oversized", "replaced", "truncated"])
def test_ambiguous_native_baseline_is_explicitly_incomplete(
    phase_chat, terminal, monkeypatch, state
):
    _issue, target = phase_chat
    native = Path.cwd() / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))
    session = "b3a45cc1-5221-4ee6-9fc3-5517448c7c14"
    SessionManager().save_session("David", AgentCLI.CLAUDE, session, "x", "implementation")
    journal = (
        native / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd())) / (session + ".jsonl")
    )
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session,
                "message": {"id": "old", "model": "old", "usage": {"input_tokens": 900}},
            }
        )
        + "\n"
    )
    if state == "oversized":
        with journal.open("r+b") as handle:
            handle.truncate(16 * 1024 * 1024 + 1)

    def launch(command, **kwargs):
        if state == "replaced":
            journal.rename(journal.with_suffix(".old"))
            journal.write_text("")
        elif state == "truncated":
            journal.write_text("")
        return subprocess.CompletedProcess(command, 0)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 0
    (group,) = groups(target)
    assert group["stats"] == {} and group["reported_model"] is None
    assert group["incomplete_calls"] == 1


@pytest.mark.parametrize("mode", ["interactive", "one_shot"])
def test_malformed_issue_metadata_has_visible_coverage_gap(
    phase_chat, terminal, provider_process, capsys, mode
):
    issue, target = phase_chat
    target.unlink()
    config = issue / "issue.yaml"
    config.write_text("broken: [")
    launch = provider_process([])
    terminal(lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    kwargs = {"prompt": "hello"} if mode == "one_shot" else {}
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", **kwargs) == (
        0 if mode == "interactive" else 1
    )
    assert "incomplete" in capsys.readouterr().out.lower()
    assert launch.call_count == 0 and config.read_text() == "broken: ["
