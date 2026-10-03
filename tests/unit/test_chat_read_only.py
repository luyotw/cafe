"""Read-only chat invariants (Plan U1–U3; external launch is the only mock)."""

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from cafe.agents.manager import AgentManager
from cafe.catalogs.resolver import CatalogKind, CatalogResolver, global_catalog_lock
from cafe.core.blackboard import BlackboardStore
from cafe.core.session import SessionManager
from cafe.core.types import AgentCLI, AgentConfig, SessionData
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui import chat, cli
from cafe.utils.config import ConfigManager


def inventory(root):
    """Include names, bytes and write metadata; reading may update access time."""

    def record(path):
        info = path.lstat()
        return (
            path.is_dir(),
            (
                path.readlink().as_posix()
                if path.is_symlink()
                else path.read_bytes() if path.is_file() else None
            ),
            info.st_mode,
            info.st_mtime_ns,
        )

    if not root.exists():
        return None
    return {str(path.relative_to(root)): record(path) for path in [root, *root.rglob("*")]}


@pytest.mark.parametrize("message_option", [None, "--prompt", "-p"])
@pytest.mark.parametrize("phase", [None, "inspect"])
def test_u1_actual_options_reach_chat(monkeypatch, message_option, phase):
    monkeypatch.setattr(cli, "_get_and_validate_branch", lambda *args: "issue520")
    monkeypatch.setattr(cli, "_load_issue_playbook_roles", lambda *args, **kwargs: ["analyst"])
    launches = []
    monkeypatch.setattr(
        cli, "launch_chat_session", lambda *args, **kwargs: launches.append(kwargs) or 0
    )
    args = ["chat", "analyst", "--read-only"]
    if phase:
        args += ["--phase", phase]
    if message_option:
        args += [message_option, "diagnose"]
    assert CliRunner().invoke(cli.app, args).exit_code == 0
    assert launches[0]["read_only"] is True
    assert launches[0].get("phase_name") == phase
    assert launches[0].get("prompt") == ("diagnose" if message_option else None)


@pytest.mark.parametrize("message_option", ["--prompt", "-p"])
def test_u1_prompt_text_is_not_a_read_only_option(message_option):
    assert (
        cli._should_auto_install_global_helper_skills(
            ["chat", "analyst", message_option, "--read-only"]
        )
        is True
    )
    assert (
        cli._should_auto_install_global_helper_skills(
            ["chat", "analyst", message_option, "diagnose", "--read-only"]
        )
        is False
    )


@pytest.mark.parametrize(
    "args",
    [
        ["analyst", "--unknown-option", "--read-only"],
        ["analyst", "--read-only", "--unknown-option"],
        ["analyst", "--read-only", "--phase"],
        ["analyst", "--read-only", "-p"],
        ["--read-only"],
    ],
)
def test_u1_invalid_diagnostic_options_never_allow_startup_install(args):
    assert cli._should_auto_install_global_helper_skills(["chat", *args]) is False


def test_u1_valid_writable_chat_still_allows_startup_install():
    assert cli._should_auto_install_global_helper_skills(["chat", "analyst"]) is True


def test_u1_help_exposes_protection_and_provider_support():
    result = CliRunner().invoke(cli.app, ["chat", "--help"])
    assert result.exit_code == 0
    assert "--read-only" in result.output
    assert "provider" in result.output.lower()
    for term in ["Codex", "Claude", "CAFE", "workspaceWrite", "2.1.284", "!touch", "backend"]:
        assert term in result.output


def test_u2_optional_session_and_config_never_initialize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = inventory(tmp_path)
    sessions = SessionManager(read_only=True)
    assert sessions.load_session("Ada", AgentCLI.CODEX, "issue520", "inspect") is None
    ConfigManager(read_only=True)
    assert inventory(tmp_path) == before
    for action in (sessions.save_session, sessions.delete_session):
        with pytest.raises((ValueError, PermissionError)):
            if action == sessions.save_session:
                action("Ada", AgentCLI.CODEX, "native", "issue520", "inspect")
            else:
                action("Ada", AgentCLI.CODEX, "issue520", "inspect")
    assert inventory(tmp_path) == before


def test_u2_required_blackboard_does_not_initialize_or_repair(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    before = inventory(tmp_path)
    with pytest.raises((OSError, ValueError)):
        store.load_read_only()
    assert inventory(tmp_path) == before
    state = store.load_or_create("inspect")
    before = inventory(tmp_path)
    assert store.load_read_only().workflow_id == state.workflow_id
    assert inventory(tmp_path) == before
    store.next_step_path.unlink()
    before = inventory(tmp_path)
    with pytest.raises((OSError, ValueError)):
        store.load_read_only()
    assert inventory(tmp_path) == before


def test_u2_catalog_rejects_pending_recovery_without_writing(tmp_path):
    global_root = tmp_path / "global"
    transaction = global_root / ".catalog-transactions" / "unfinished"
    transaction.mkdir(parents=True)
    before = inventory(tmp_path)
    with pytest.raises(ValueError):
        with global_catalog_lock(global_root, read_only=True):
            pass
    assert inventory(tmp_path) == before


def test_u2_missing_authorities_and_pending_audit_are_not_repaired(tmp_path):
    store = BlackboardStore(tmp_path / "issue")
    state = store.load_or_create("inspect")
    store.receipts_path.unlink()
    before = inventory(tmp_path)
    with pytest.raises(ValueError):
        store.load_read_only()
    assert inventory(tmp_path) == before
    store._write_receipts(state.workflow_id, [])
    sequence = store.audit.reserve(state.workflow_id)
    store.audit.commit(
        state.workflow_id,
        {
            "workflow_id": state.workflow_id,
            "sequence": sequence,
            "event_id": "pending",
            "timestamp": "2026-10-03T00:00:00Z",
            "step": "inspect",
            "event_type": "pending",
            "message": "pending patch",
            "data": {},
            "patch": {"current_step": {"before": "inspect", "after": "user"}},
        },
    )
    before = inventory(tmp_path)
    with pytest.raises(ValueError):
        store.load_read_only()
    assert inventory(tmp_path) == before


def test_u2_nested_writable_catalog_read_cannot_run_recovery(tmp_path):
    before = inventory(tmp_path)
    with global_catalog_lock(tmp_path / "global", read_only=True):
        with pytest.raises(ValueError):
            with global_catalog_lock(tmp_path / "global"):
                pass
    assert inventory(tmp_path) == before


def test_u2_nested_catalog_reads_preserve_absent_global_root(tmp_path, monkeypatch):
    monkeypatch.setattr("cafe.utils.config.Path.home", lambda: tmp_path / "missing-home")
    before = inventory(tmp_path)
    loader = PlaybookLoader(project_root=tmp_path, read_only=True)
    assert "develop" in loader.load("standard")["steps"]
    skills = SkillLoader(project_root=tmp_path, read_only=True)
    assert skills.activate("cafe-develop")
    assert inventory(tmp_path) == before


def test_u2_precedence_and_invalid_highest_authority_are_preserved(tmp_path):
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    for root, marker in [(global_root, "global"), (project / ".cafe", "project")]:
        path = root / "agents" / "analyst" / "Ada.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: Ada\ndescription: {marker}\n---\n")
    resolver = CatalogResolver(project_root=project, global_root=global_root, read_only=True)
    before = inventory(tmp_path)
    entry = resolver.resolve(CatalogKind.AGENT, "analyst/Ada")
    assert entry.source == "project"
    assert inventory(tmp_path) == before
    entry.path.write_text("invalid frontmatter")
    before = inventory(tmp_path)
    with pytest.raises(ValueError):
        resolver.resolve(CatalogKind.AGENT, "analyst/Ada")
    assert inventory(tmp_path) == before


def test_u3_custom_phase_selection_and_injected_session_reads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    playbook = {"steps": {"inspect": {"role": "analyst"}, "publish": {"role": "editor"}}}
    sessions = tmp_path / ".cafe/issues/issue520/sessions"
    sessions.mkdir(parents=True)
    now = datetime.now()
    for phase, last_used in [("inspect", now), ("publish", now + timedelta(days=1))]:
        session = SessionData(
            agent_name="Ada",
            cli=AgentCLI.CODEX,
            session_id=phase,
            phase_name=phase,
            created_at=now,
            last_used_at=last_used,
        )
        (sessions / f"Ada_codex_{phase}.json").write_text(session.model_dump_json())
    before = inventory(tmp_path)
    chosen = chat._load_latest_role_session(
        sessions.parent, role="analyst", playbook=playbook, read_only=True
    )
    assert chosen.session_id == "inspect"
    config = {"name": "Ada", "clis": [{"cli": "codex", "model": "chosen-model"}]}
    assert chat._resolve_configured_chat_session(config, phase_name="inspect", session=chosen) == (
        "codex",
        "chosen-model",
        "inspect",
    )
    manager = AgentManager(session_manager=SessionManager(read_only=True), issue_name="issue520")
    manager.register_agent(AgentConfig(name="Ada", cli=AgentCLI.CODEX))
    assert inventory(tmp_path) == before
    with pytest.raises(ValueError):
        chat._validate_chat_phase(playbook, "analyst", "publish")
    with pytest.raises(ValueError):
        chat._validate_chat_phase(playbook, "analyst", "absent")


def test_u1_end_of_options_does_not_suppress_startup_for_literal_role():
    assert cli._should_auto_install_global_helper_skills(["chat", "--", "--read-only"])
