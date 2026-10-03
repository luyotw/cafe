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


@pytest.fixture(params=["iteration", "issue"])
def accounting_target(phase_chat, request):
    """Existing JSON/YAML targets through the public caller (Plan U11/I6)."""
    issue, iteration = phase_chat
    if request.param == "issue":
        iteration.unlink()
        target = issue / "issue.yaml"
        target.write_text(yaml.safe_dump({"feature_branch": "x", "unrelated": "kept"}))
        load, dump = yaml.safe_load, yaml.safe_dump
    else:
        target = iteration
        load, dump = json.loads, json.dumps
    return issue, target, load, dump


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_completed_parent_replacement_prevents_all_accounting_side_effects(
    accounting_target, provider_process, replacement
):
    from cafe.core.workspace_lock import workspace_execution_lock

    issue, target, _load, _dump = accounting_target
    original = target.read_bytes()
    outside = Path.cwd() / "outside"
    outside.mkdir()
    (outside / "unrelated").write_bytes(b"preserved")
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    held = issue.with_name("held")
    launch = provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 2})]
    )
    process = launch.return_value

    def finish_replacement(command, **kwargs):
        # Completed cooperating mutation during the provider call, before persist.
        with workspace_execution_lock(Path.cwd()):
            issue.rename(held)
            if replacement == "symlink":
                issue.symlink_to(outside, target_is_directory=True)
            else:
                issue.mkdir()
                (issue / "unrelated").write_bytes(b"preserved")
        return process

    launch.side_effect = finish_replacement
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 1
    )
    assert launch.call_count == 1
    assert (held / target.relative_to(issue)).read_bytes() == original
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
    if replacement == "directory":
        assert {p.name: p.read_bytes() for p in issue.iterdir()} == {"unrelated": b"preserved"}


def _malformed_chat_groups(case):
    from cafe.core.usage import CHAT_USAGE_FIELDS

    group = dict(
        cli="claude",
        requested_model="selected",
        reported_model="selected",
        mode="one_shot",
        phase="implementation",
        stats={},
        calls=1,
        incomplete_calls=1,
        unknown_fields=list(CHAT_USAGE_FIELDS),
    )
    if case == "list":
        return "not-a-list"
    if case == "group":
        return [None]
    if case == "missing_coverage":
        del group["unknown_fields"]
    elif case == "coverage_string":
        group["unknown_fields"] = "input_tokens"
    elif case == "coverage_name":
        group["unknown_fields"] = ["not-a-counter"]
    elif case == "stats":
        group["stats"] = []
    elif case == "counter":
        group["stats"] = {"input_tokens": "three"}
    elif case == "cost":
        group["stats"] = {"total_cost_usd": float("inf")}
    elif case == "calls":
        group["calls"] = True
    elif case == "incomplete_calls":
        group["incomplete_calls"] = 2
    elif case == "model":
        group["reported_model"] = {}
    elif case == "duplicate":
        return [group, dict(group)]
    return [group]


@pytest.mark.parametrize(
    "case",
    [
        "list",
        "group",
        "missing_coverage",
        "coverage_string",
        "coverage_name",
        "stats",
        "counter",
        "cost",
        "calls",
        "incomplete_calls",
        "model",
        "duplicate",
    ],
)
def test_malformed_accounting_is_rejected_before_paid_one_shot(
    accounting_target, provider_process, capsys, case
):
    _issue, target, load, dump = accounting_target
    data = load(target.read_text())
    data["chat_usage"] = _malformed_chat_groups(case)
    target.write_text(dump(data))
    original = target.read_bytes()
    launch = provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 3})]
    )
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 1
    )
    assert launch.call_count == 0
    assert "incomplete" in capsys.readouterr().out.lower()
    assert target.read_bytes() == original
    assert list(target.parent.glob(".usage-*")) == []


@pytest.mark.parametrize("case", ["list", "missing_coverage"])
def test_changed_accounting_is_revalidated_without_schema_crash_or_recovery(
    accounting_target, provider_process, capsys, case
):
    from cafe.core.workspace_lock import workspace_execution_lock

    _issue, target, load, dump = accounting_target
    SessionManager().save_session("David", AgentCLI.CLAUDE, "stale", "x", "implementation")
    launch = provider_process(
        [init("stale", model="selected"), dict(type="result", usage={"input_tokens": 3})],
        returncode=1,
        stderr="no conversation found",
    )
    process = launch.return_value
    changed = None

    def change_metadata(command, **kwargs):
        nonlocal changed
        with workspace_execution_lock(Path.cwd()):
            current = load(target.read_text())
            current["chat_usage"] = _malformed_chat_groups(case)
            target.write_text(dump(current))
            changed = target.read_bytes()
        return process

    launch.side_effect = change_metadata
    assert (
        chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 1
    )
    assert launch.call_count == 1
    assert "incomplete" in capsys.readouterr().out.lower()
    assert target.read_bytes() == changed and list(target.parent.glob(".usage-*")) == []


def test_invalid_accounting_preserves_interactive_terminal_with_explicit_gap(
    accounting_target, terminal, capsys
):
    _issue, target, load, dump = accounting_target
    current = load(target.read_text())
    current["chat_usage"] = _malformed_chat_groups("missing_coverage")
    target.write_text(dump(current))
    original = target.read_bytes()
    calls = []

    def launch(command, **kwargs):
        assert set(kwargs) == {"env"}
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    terminal(launch)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 0
    assert len(calls) == 1 and "incomplete" in capsys.readouterr().out.lower()
    assert target.read_bytes() == original


def test_public_chat_settings_lock_uses_admitted_descriptor(
    phase_chat, provider_process, monkeypatch
):
    import io

    issue, iteration = phase_chat
    iteration.unlink()
    target = issue / "issue.yaml"
    target.write_text(yaml.safe_dump({"feature_branch": "x", "unrelated": "kept"}))
    original_open = io.open

    def require_descriptor(file, *args, **kwargs):
        # External filesystem boundary: reopening this sidecar by path is
        # unavailable; opening it within the admitted directory remains valid.
        if isinstance(file, (str, Path)) and Path(file).name == "issue-settings.lock":
            raise PermissionError("settings lock requires its admitted descriptor")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", require_descriptor)
    launch = provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 2})]
    )
    assert chat.launch_chat_session("developer", "x", phase_name="implementation", prompt="hello") == 0
    assert launch.call_count == 1
    current = yaml.safe_load(target.read_text())
    assert current["unrelated"] == "kept"
    assert current["chat_usage"][0]["stats"]["input_tokens"] == 2


@pytest.mark.parametrize("requested_length", [512, 513])
@pytest.mark.parametrize("partial_input", [None, 2])
def test_requested_model_boundary_does_not_poison_corrected_chat(
    accounting_target, provider_process, terminal, monkeypatch, requested_length, partial_input
):
    """An errored call's bounded group stays reusable (Plan U6/U8/U11/I1/I2/I6)."""
    issue, target, load, _dump = accounting_target
    phases = Path.cwd() / ".cafe/phases.yaml"
    configuration = yaml.safe_load(phases.read_text())
    requested = "m" * requested_length
    configuration["implementation"]["clis"][0]["model"] = requested
    phases.write_text(yaml.safe_dump(configuration))
    records = [init("failed")]
    if partial_input is not None:
        records.append(dict(type="result", usage={"input_tokens": partial_input}))
    launch = provider_process(records, returncode=7, stderr="provider rejected request")
    assert chat.launch_chat_session(
        "developer", "x", phase_name="implementation", prompt="hello"
    ) == 1
    assert launch.call_count == 1
    assert requested in launch.call_args.args[0]
    first = load(target.read_text())["chat_usage"]
    assert len(first) == 1
    assert first[0]["requested_model"] == (requested if requested_length == 512 else None)
    assert first[0]["reported_model"] is None
    assert first[0]["calls"] == first[0]["incomplete_calls"] == 1
    assert first[0]["stats"] == ({} if partial_input is None else {"input_tokens": partial_input})
    assert "total_cost_usd" in first[0]["unknown_fields"]

    # Correct the actual configuration, then exercise both public launch modes.
    configuration["implementation"]["clis"][0]["model"] = "selected"
    phases.write_text(yaml.safe_dump(configuration))
    launch = provider_process(
        [init("new", model="selected"), dict(type="result", usage={"input_tokens": 3})]
    )
    launch.reset_mock()
    assert chat.launch_chat_session(
        "developer", "x", phase_name="implementation", prompt="hello"
    ) == 0
    assert launch.call_count == 1

    session = "b3a45cc1-5221-4ee6-9fc3-5517448c7c14"
    SessionManager().save_session("David", AgentCLI.CLAUDE, session, "x", "implementation")
    native = Path.cwd() / "native"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native))
    journal = native / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd())) / (session + ".jsonl")
    journal.parent.mkdir(parents=True)
    journal.write_text("")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert set(kwargs) == {"env"}
        assert "--resume" in command and session in command
        with journal.open("a") as handle:
            handle.write(json.dumps({
                "type": "assistant", "sessionId": session,
                "message": {"id": "current", "model": "selected", "usage": {"input_tokens": 5}},
            }) + "\n")
        return subprocess.CompletedProcess(command, 7)

    terminal(run)
    assert chat.launch_chat_session("developer", "x", phase_name="implementation") == 7
    assert len(calls) == 1
    stored = load(target.read_text())["chat_usage"]
    assert sum(group["calls"] for group in stored) == 3
    assert sum(group["stats"].get("input_tokens", 0) for group in stored) == 8 + (partial_input or 0)
    assert all(
        group[field] is None or len(group[field]) <= 512
        for group in stored for field in ("requested_model", "reported_model")
    )
    assert next(group for group in stored if group["mode"] == "interactive")["incomplete_calls"] == 1
    consumed = StatusService(issues_root=issue.parent).load_chat_usage("x", ["implementation"])
    assert sum(group["stats"].get("input_tokens", 0) for group in consumed) == 8 + (partial_input or 0)
    assert list(target.parent.glob(".usage-*")) == []
