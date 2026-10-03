"""Read-only inspection journeys; successful enforcement must use a real backend."""

import json
import subprocess
import sys

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.blackboard import BlackboardStore
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.chat import _load_chat_workflow_context
from tests.unit.test_chat_read_only import inventory


def materialize_pending(issue, workflow_id):
    return HumanTaskRecordStore(issue).materialize(
        workflow_id=workflow_id,
        step="inspect",
        iteration=1,
        trigger="need_clarification",
        policy_id="clarification-feedback",
        prompt="Inspect this pending question.",
        expected_result={"input_schema": "feedback", "required": True},
        continuations={"submit": "inspect"},
        assignee_type="user",
    )


@pytest.mark.integration
def test_i4_pending_work_is_readable_without_preparation_or_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cafe.utils.config.Path.home", lambda: tmp_path / "missing-home")
    issue = tmp_path / ".cafe/issues/issue520"
    store = BlackboardStore(issue)
    store.load_or_create("develop")
    materialize_pending(issue, store.load_read_only().workflow_id)
    before = inventory(tmp_path)
    current_step, steps, playbook = _load_chat_workflow_context(issue, read_only=True)
    assert current_step == "develop"
    assert current_step in steps
    assert playbook == "standard"
    assert inventory(tmp_path) == before
    assert not (issue / "sessions").exists()
    store.file_path.unlink()
    before = inventory(tmp_path)
    with pytest.raises((OSError, ValueError)):
        _load_chat_workflow_context(issue, read_only=True)
    assert inventory(tmp_path) == before


@pytest.mark.integration
def test_i4_unsafe_catalog_returns_error_without_recovering_pending_work(tmp_path):
    global_root = tmp_path / "global"
    transaction = global_root / ".catalog-transactions" / "pending"
    transaction.mkdir(parents=True)
    before = inventory(tmp_path)
    loader = PlaybookLoader(project_root=tmp_path, global_root=global_root, read_only=True)
    with pytest.raises(ValueError):
        loader.load("standard")
    assert inventory(tmp_path) == before


from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from typer.testing import CliRunner

from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI
from cafe.ui import cli


@pytest.fixture
def diagnostic_workspace(tmp_path, monkeypatch):
    """Real declarations/state including a custom role and step; no model call."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "issue520", str(repo)], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CAFE_MOCK_AGENTS", raising=False)
    # A valid existing playbook is adapted to test declarative custom topology.
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data/playbooks/standard.yaml"

    def rename(value):
        if isinstance(value, dict):
            return {rename(k): rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, str):
            return {"developer": "analyst", "develop": "inspect"}.get(value, value)
        return value

    declaration = rename(yaml.safe_load(builtin.read_text()))
    declaration["steps"]["spec"]["initial_input"].pop("legacy_presentation")
    text = yaml.safe_dump(declaration)
    catalog = repo / ".cafe/playbooks"
    catalog.mkdir(parents=True)
    (catalog / "standard.yaml").write_text(text)
    issue = repo / ".cafe/issues/issue520"
    store = BlackboardStore(issue)
    state = store.load_or_create("inspect")
    materialize_pending(issue, state.workflow_id)
    # Real pre-existing accounting metadata, produced through its public owner.
    from cafe.core.usage import chat_usage_sink
    from cafe.agents.transport_types import TransportResult
    from cafe.core.types import TokenUsage

    (issue / "issue.yaml").write_text("feature_branch: issue520\n")
    chat_usage_sink(
        repo,
        issue / "issue.yaml",
        cli="claude",
        requested_model="old-model",
        phase="inspect",
        mode="one_shot",
        issue_metadata=True,
    )((TransportResult(usage=TokenUsage(input_tokens=3)),))
    (issue / "artifacts").mkdir()
    (issue / "artifacts/report.md").write_text("diagnostic context")
    return repo, issue, store


def configure_provider(repo, provider, resumed=False):
    (repo / ".cafe/phases.yaml").write_text(
        yaml.safe_dump(
            {
                "inspect": {
                    "role": "analyst",
                    "name": "Ada",
                    "clis": [{"cli": provider, "model": "selected-model"}],
                },
            }
        )
    )
    if resumed:
        SessionManager().save_session(
            "Ada", AgentCLI(provider), "stored-session", "issue520", "inspect"
        )


@pytest.fixture
def native_io(monkeypatch):
    """Only native subprocess I/O is replaced; Git/discovery remains real.

    Native-shaped stream fixtures are parameter/response evidence, not inference
    or native mutation-denial evidence. Interactive fixture retains inherited IO.
    """
    real_run, real_popen = subprocess.run, subprocess.Popen
    launches = []
    scenario = {"returncode": 0, "stderr": "", "missing": False}

    def native_run(command, **kwargs):
        if command[0] not in {"codex", "claude", "gemini", "cursor-agent", "copilot"}:
            return real_run(command, **kwargs)
        launches.append((command, kwargs))
        current = (
            scenario.get("attempts", [scenario]).pop(0) if scenario.get("attempts") else scenario
        )
        if current.get("missing", False):
            raise FileNotFoundError(command[0])
        return subprocess.CompletedProcess(command, current["returncode"], stderr=current["stderr"])

    def native_popen(command, **kwargs):
        if command[0] not in {"codex", "claude", "gemini", "cursor-agent", "copilot"}:
            return real_popen(command, **kwargs)
        launches.append((command, kwargs))
        current = (
            scenario.get("attempts", [scenario]).pop(0) if scenario.get("attempts") else scenario
        )
        if current.get("missing", False):
            raise FileNotFoundError(command[0])
        session = "stored-session" if "stored-session" in command else "fresh-session"
        records = (
            [
                {"type": "thread.started", "thread_id": session},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "diagnostic response"},
                },
                {"type": "turn.completed"},
            ]
            if command[0] == "codex"
            else [
                {"type": "system", "subtype": "init", "session_id": session},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "diagnostic response"}]},
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "diagnostic response",
                    "session_id": session,
                },
            ]
        )
        if current.get("invalid_request"):
            records.insert(
                1,
                {
                    "type": "assistant",
                    "error": "invalid_request",
                    "message": {"content": [{"type": "text", "text": current["stderr"]}]},
                },
            )
        process = MagicMock()
        process.stdout.readline.side_effect = [json.dumps(r) + "\n" for r in records] + [""]
        process.stderr.read.return_value = current["stderr"]
        process.poll.return_value = None
        process.wait.return_value = current["returncode"]
        return process

    monkeypatch.setattr(subprocess, "run", native_run)
    monkeypatch.setattr(subprocess, "Popen", native_popen)
    # The deterministic process has no OS descriptor for select; this is only
    # the terminal polling boundary, as in existing transport tests.
    monkeypatch.setattr("sys.platform", "win32")
    return launches, scenario


def assert_native_options(command, provider):
    assert command[0] == provider
    assert "selected-model" in command
    if provider == "codex":
        assert command[command.index("--sandbox") + 1] == "read-only"
        approval = "-a" if "-a" in command else "--ask-for-approval"
        assert command[command.index(approval) + 1] == "never"
    else:
        assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
        assert command[command.index("--permission-mode") + 1] == "plan"


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("mode", [None, "--prompt", "-p"])
@pytest.mark.parametrize("explicit_phase", [False, True])
def test_i1_i2_i5_diagnose_without_cafe_state_changes(
    diagnostic_workspace, native_io, provider, resumed, mode, explicit_phase
):
    repo, issue, _ = diagnostic_workspace
    configure_provider(repo, provider, resumed)
    before = inventory(repo.parent)
    args = ["chat", "analyst", "--read-only"]
    if explicit_phase:
        args += ["--phase", "inspect"]
    if mode:
        args += [mode, "inspect existing workflow"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    launches, _ = native_io
    assert len(launches) == 1
    command, kwargs = launches[0]
    assert_native_options(command, provider)
    assert ("stored-session" in command) == resumed
    assert kwargs["env"]["CAFE_CHAT_CURRENT_STEP"] == "inspect"
    assert kwargs["env"]["CAFE_ISSUE_DIR"] == str(issue)
    if mode:
        assert "diagnostic response" in result.output
    else:
        assert "stdin" not in kwargs and "stdout" not in kwargs and "stderr" not in kwargs
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["gemini", "cursor-agent", "copilot"])
def test_i6_unsupported_provider_preserves_state_and_never_launches(
    diagnostic_workspace, native_io, provider
):
    repo, _, _ = diagnostic_workspace
    configure_provider(repo, provider)
    before = inventory(repo.parent)
    result = CliRunner().invoke(cli.app, ["chat", "analyst", "--read-only", "-p", "inspect"])
    assert result.exit_code != 0
    assert provider in result.output and "run_one_shot" in result.output
    assert native_io[0] == []
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("mode", [None, "-p"])
@pytest.mark.parametrize("missing", [False, True])
def test_i6_native_error_is_visible_without_writable_fallback(
    diagnostic_workspace, native_io, provider, mode, missing
):
    repo, _, _ = diagnostic_workspace
    configure_provider(repo, provider, True)
    native_io[1].update(returncode=1, stderr="native backend unavailable", missing=missing)
    before = inventory(repo.parent)
    args = ["chat", "analyst", "--read-only"] + ([mode, "inspect"] if mode else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code != 0
    assert len(native_io[0]) == 1
    assert_native_options(native_io[0][0][0], provider)
    assert inventory(repo.parent) == before


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("interactive", [False, True])
def test_u5_final_transport_spawn_restricts_without_accounting_or_streaming_file(
    diagnostic_workspace, native_io, provider, resumed, interactive
):
    from cafe.agents.executor import AgentExecutor
    from cafe.agents.transport import ConversationTransport
    from cafe.core.types import AgentConfig

    repo, _, _ = diagnostic_workspace
    executor = AgentExecutor(
        AgentConfig(
            name="Ada",
            cli=AgentCLI(provider),
            model="selected-model",
            session_id="stored-session" if resumed else None,
        ),
        stream_output=False,
    )
    transport = ConversationTransport(executor)
    before = inventory(repo.parent)
    if interactive:
        accounting = []
        transport.open_interactive_session(
            "inspect", read_only=True, on_accounting=accounting.append
        )
        assert accounting == []
    else:
        responses = []
        transport.run_one_shot(
            "inspect",
            read_only=True,
            on_response=responses.append,
            streaming_output_file=str(repo / "streaming.jsonl"),
        )
        assert responses[0].response == "diagnostic response"
    assert len(native_io[0]) == 1
    command, kwargs = native_io[0][0]
    assert_native_options(command, provider)
    assert ("stored-session" in command) == resumed
    assert inventory(repo.parent) == before


def test_u2_custom_nested_playbook_read_does_not_recover(diagnostic_workspace):
    repo, _, _ = diagnostic_workspace
    before = inventory(repo.parent)
    PlaybookLoader(project_root=repo, read_only=True).load("standard")
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("unsafe", [None, "blackboard", "catalog", "session"])
def test_i4_actual_module_startup_does_not_install_or_repair(diagnostic_workspace, unsafe):
    import os
    import stat

    repo, issue, store = diagnostic_workspace
    configure_provider(repo, "codex")
    if unsafe == "blackboard":
        store.receipts_path.unlink()
    if unsafe == "catalog":
        journal = repo.parent / "home/.cafe/.catalog-transactions/pending"
        journal.mkdir(parents=True)
    if unsafe == "session":
        session_file = issue / "sessions/broken.json"
        session_file.parent.mkdir()
        session_file.write_text("invalid JSON")
    binary = repo.parent / "bin/codex"
    binary.parent.mkdir()
    binary.write_text(
        f"#!{sys.executable}\n"
        + "import json\n"
        + "for r in [{'type':'item.completed','item':{'type':'agent_message','text':'fixture diagnosis'}}, {'type':'turn.completed'}]: print(json.dumps(r))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    before = inventory(repo.parent)
    env = dict(
        os.environ,
        PATH=str(binary.parent) + os.pathsep + os.environ["PATH"],
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    # No skip-sync flag: the actual option must suppress eligible main startup.
    env.pop("CAFE_SKIP_GLOBAL_SKILL_SYNC", None)
    result = subprocess.run(
        [sys.executable, "-m", "cafe.ui.cli", "chat", "analyst", "--read-only", "-p", "inspect"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert (result.returncode == 0) == (unsafe is None), result.stdout + result.stderr
    if unsafe is None:
        assert "fixture diagnosis" in result.stdout
    else:
        assert "read-only" in result.stdout.lower()
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_i3_linked_worktree_shared_and_absent_context_remains_unchanged(
    diagnostic_workspace, native_io, provider, monkeypatch
):
    repo, issue, _ = diagnostic_workspace
    configure_provider(repo, provider, True)
    # Real linked Git worktree, with the issue/catalog/phase authorities shared
    # by links outside cwd. Inventory includes link identities AND their targets.
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    linked = repo.parent / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-qb", "diagnostic", str(linked)], check=True
    )
    (linked / ".cafe").symlink_to(repo / ".cafe", target_is_directory=True)
    monkeypatch.chdir(linked)
    before = inventory(repo.parent)
    from cafe.ui.chat import launch_chat_session

    result = launch_chat_session(
        "analyst", "issue520", read_only=True, phase_name="inspect", prompt="inspect"
    )
    assert result == 0
    assert_native_options(native_io[0][0][0], provider)
    assert inventory(repo.parent) == before
    assert not (issue / "absent-artifacts").exists()


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("reason", ["No conversation found", "Prompt is too long"])
def test_i7_same_provider_recovery_keeps_options_without_persistence(
    diagnostic_workspace, native_io, provider, reason
):
    repo, _, _ = diagnostic_workspace
    configure_provider(repo, provider, True)
    native_io[1]["attempts"] = [
        {"returncode": 1, "stderr": reason, "invalid_request": reason == "Prompt is too long"},
        {"returncode": 0, "stderr": ""},
    ]
    before = inventory(repo.parent)
    result = CliRunner().invoke(
        cli.app, ["chat", "analyst", "--read-only", "--phase", "inspect", "-p", "diagnose"]
    )
    assert result.exit_code == 0, result.output
    commands = [c for c, _ in native_io[0]]
    assert len(commands) == 2
    assert "stored-session" in commands[0] and "stored-session" not in commands[1]
    for command in commands:
        assert_native_options(command, provider)
    assert "diagnostic response" in result.output
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_i7_partial_native_failure_preserves_cafe_records(
    diagnostic_workspace, native_io, provider
):
    repo, _, _ = diagnostic_workspace
    configure_provider(repo, provider, True)
    native_io[1].update(returncode=1, stderr="native option rejected")
    before = inventory(repo.parent)
    result = CliRunner().invoke(cli.app, ["chat", "analyst", "--read-only", "-p", "diagnose"])
    assert result.exit_code != 0
    assert "diagnostic response" in result.output  # partial native text stays visible
    assert len(native_io[0]) == 1
    assert_native_options(native_io[0][0][0], provider)
    assert inventory(repo.parent) == before


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_i8_writable_chat_persists_session_and_prepares_context(
    diagnostic_workspace, native_io, provider
):
    repo, issue, store = diagnostic_workspace
    configure_provider(repo, provider, True)
    # Global/default skill destinations remain isolated by fixture HOME.
    session_file = SessionManager().get_session_file(
        "Ada", AgentCLI(provider), "issue520", "inspect"
    )
    before_timestamp = session_file.stat().st_mtime_ns
    result = CliRunner().invoke(
        cli.app, ["chat", "analyst", "--phase", "inspect", "-p", "diagnose"]
    )
    assert result.exit_code == 0, result.output
    assert session_file.stat().st_mtime_ns > before_timestamp
    assert len(native_io[0]) == 1
    command = native_io[0][0][0]
    assert "--sandbox" not in command and "--tools" not in command
    assert (repo.parent / "home/.cafe").exists()
    assert (
        SessionManager().load_session("Ada", AgentCLI(provider), "issue520", "inspect").session_id
        == "stored-session"
    )
    assert json.loads(store.next_step_path.read_text())["to_step"] == "inspect"


@pytest.mark.integration
def test_i5_saved_configured_backup_keeps_current_model_authority(diagnostic_workspace, native_io):
    repo, _, _ = diagnostic_workspace
    configure_provider(repo, "claude", True)
    (repo / ".cafe/phases.yaml").write_text(
        yaml.safe_dump(
            {
                "inspect": {
                    "name": "Ada",
                    "role": "analyst",
                    "clis": [
                        {"cli": "codex", "model": "primary-model"},
                        {"cli": "claude", "model": "selected-model"},
                    ],
                }
            }
        )
    )
    before = inventory(repo.parent)
    result = CliRunner().invoke(
        cli.app, ["chat", "analyst", "--read-only", "--phase", "inspect", "-p", "inspect"]
    )
    assert result.exit_code == 0, result.output
    assert_native_options(native_io[0][0][0], "claude")
    assert "stored-session" in native_io[0][0][0]
    assert "primary-model" not in native_io[0][0][0]
    assert inventory(repo.parent) == before
