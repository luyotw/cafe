"""Tests for the reusable chat launcher module."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.cli import ClaudeCLI, CodexCLI, CopilotCLI, CursorCLI, GeminiCLI
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.types import AgentCLI, AgentConfig
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.chat import (
    _load_latest_role_iteration_cli,
    _prepare_chat_environment,
    _prepare_chat_handoff_state,
    get_chat_next_step_path,
    launch_chat_session,
)


@pytest.fixture(autouse=True)
def mock_chat_environment():
    """Avoid writing to real native CLI skill directories in unit tests."""
    with patch("cafe.ui.chat._prepare_chat_environment") as mock_prepare:
        yield mock_prepare


@pytest.fixture(autouse=True)
def mock_phase_config_boundary_for_legacy_chat_fixtures(monkeypatch):
    """Keep launcher tests focused on chat behavior, not phase-file I/O."""
    from cafe.ui import chat

    real_loader = chat._load_chat_role_config

    def load_config(config_manager, role, issue_dir=None):
        configured = config_manager.get(f"agents.{role}", None)
        if configured is not None:
            return configured
        return real_loader(config_manager, role, issue_dir=issue_dir)

    monkeypatch.setattr(chat, "_load_chat_role_config", load_config)
    yield monkeypatch


class TestLaunchChatSession:
    """Tests for launch_chat_session()."""

    @pytest.fixture(autouse=True)
    def isolate_launcher_workspace(self, tmp_path, monkeypatch):
        """Keep launcher-created issue state out of the repository workspace."""
        monkeypatch.chdir(tmp_path)

    def _make_agent_config(self, cli: str, session_id=None, model=None):
        """Build a mock AgentConfig."""
        return AgentConfig(
            name="test-agent",
            cli=AgentCLI(cli),
            session_id=session_id,
            model=model,
        )

    def _make_agent_manager(self, agent_name: str, cli: str, session_id=None, model=None):
        """Build a mock AgentManager with one agent."""
        config = self._make_agent_config(cli, session_id, model)
        executor = MagicMock()
        executor.config = config
        strategy_class = {
            AgentCLI.CLAUDE: ClaudeCLI,
            AgentCLI.CODEX: CodexCLI,
            AgentCLI.COPILOT: CopilotCLI,
            AgentCLI.CURSOR: CursorCLI,
            AgentCLI.GEMINI: GeminiCLI,
        }[config.cli]
        executor._get_cli_strategy.return_value = strategy_class(config)

        agent_manager = MagicMock()
        agent_manager.agents = {agent_name: executor}
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        return agent_manager

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_chat_uses_last_successful_active_cli(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        tmp_path,
        monkeypatch,
    ):
        """Chat reuses the role's last successful fallback CLI."""
        monkeypatch.chdir(tmp_path)
        issue_dir = tmp_path / ".cafe" / "issues" / "issue123"
        issue_dir.mkdir(parents=True)
        # configured_primary still claude → codex was a legit fallback, stay sticky.
        (issue_dir / "active_clis.json").write_text(
            json.dumps(
                {
                    "David": {
                        "cli": "codex",
                        "model": "gpt-5.3-codex",
                        "configured_primary": "claude",
                        "chain": [
                            {"cli": "claude", "model": "sonnet"},
                            {"cli": "codex", "model": "gpt-5.3-codex"},
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        mock_config = MagicMock()
        mock_config.get.return_value = {
            "name": "David",
            "clis": [
                {"cli": "claude", "develop": "sonnet"},
                {"cli": "codex", "develop": "gpt-5.3-codex"},
            ],
        }
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager(
            "David", "codex", session_id=None, model="gpt-5.3-codex"
        )
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_chat_session("developer", "issue123")

        assert result == 0
        assert mock_run.call_args.args[0] == ["codex", "--model", "gpt-5.3-codex"]
        registered_config = agent_manager.register_agent.call_args.args[0]
        assert registered_config.cli == AgentCLI.CODEX
        assert registered_config.model == "gpt-5.3-codex"

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_chat_session_accepts_alignment_context_env(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Roger", "claude", session_id=None)
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_chat_session(
            "pm",
            "issue123",
            chat_mode="alignment",
            extra_env={
                "CAFE_ALIGNMENT_REQUEST_FILE": "/tmp/request.json",
                "CAFE_ALIGNMENT_DECISION_FILE": "/tmp/decision.json",
            },
        )

        assert result == 0
        env = mock_run.call_args.kwargs["env"]
        assert env["CAFE_CHAT_MODE"] == "alignment"
        assert env["CAFE_ISSUE_NAME"] == "issue123"
        assert env["CAFE_ALIGNMENT_REQUEST_FILE"] == "/tmp/request.json"
        assert env["CAFE_ALIGNMENT_DECISION_FILE"] == "/tmp/decision.json"

    @patch("builtins.print")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_missing_cli_tool_prints_warning(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_print
    ):
        """Test that a missing CLI tool prints a warning and does not raise."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude", session_id=None)
        mock_agent_manager_cls.return_value = agent_manager

        with patch("cafe.ui.chat.subprocess.run", side_effect=FileNotFoundError):
            launch_chat_session("developer", "issue123")  # Should not raise

        # Warning should be printed
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "claude" in printed

    @patch("builtins.print")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_no_agent_config_prints_warning(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_print
    ):
        """Test that missing agent config prints a warning and does not raise."""
        mock_config = MagicMock()
        mock_config.get.return_value = None  # No agent config
        mock_config_manager_cls.return_value = mock_config

        launch_chat_session("developer", "issue123")  # Should not raise

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "developer" in printed

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_passes_issue_name_to_agent_manager(
        self, mock_agent_manager_cls, mock_config_manager_cls, mock_run
    ):
        """Test that issue_name is passed to AgentManager for session resolution."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "my-issue")

        mock_agent_manager_cls.assert_called_once_with(issue_name="my-issue")

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_prepares_chat_environment_before_launch(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        mock_chat_environment,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("David", "claude")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        launch_chat_session("developer", "issue123")

        mock_chat_environment.assert_called_once()
        kwargs = mock_chat_environment.call_args.kwargs
        assert kwargs["agent_cli"] == AgentCLI.CLAUDE

    @patch("cafe.ui.chat._extract_latest_codex_session_id", return_value="thread-123")
    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_codex_chat_saves_new_session(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        mock_extract_session,
        monkeypatch,
    ):
        """Test that Codex chat stores a new session after interactive launch."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager("Nick", "codex", session_id=None, model="gpt-5.4")
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)
        codex_home = "/tmp/custom-codex-home"
        monkeypatch.setenv("CODEX_HOME", codex_home)

        result = launch_chat_session("developer", "issue123")

        assert result == 0
        assert mock_run.call_args.args[0] == ["codex", "--model", "gpt-5.4"]
        assert mock_run.call_args.kwargs["env"]["CODEX_HOME"] == codex_home
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "thread-123",
            "issue123",
        )

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_codex_chat_with_existing_session_uses_resume_and_updates_last_used(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
        monkeypatch,
    ):
        """Test Codex interactive resume and session persistence."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager(
            "Nick", "codex", session_id="sess-codex", model="gpt-5.4"
        )
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)
        codex_home = "/tmp/custom-codex-home"
        monkeypatch.setenv("CODEX_HOME", codex_home)

        result = launch_chat_session("developer", "issue123")

        assert result == 0
        assert mock_run.call_args.args[0] == ["codex", "--model", "gpt-5.4", "resume", "sess-codex"]
        assert mock_run.call_args.kwargs["env"]["CODEX_HOME"] == codex_home
        agent_manager.session_manager.save_session.assert_called_once_with(
            "Nick",
            AgentCLI.CODEX,
            "sess-codex",
            "issue123",
        )

    @patch("cafe.ui.chat.subprocess.run")
    @patch("cafe.ui.chat.ConfigManager")
    @patch("cafe.ui.chat.AgentManager")
    def test_codex_chat_accepts_initial_prompt(
        self,
        mock_agent_manager_cls,
        mock_config_manager_cls,
        mock_run,
    ):
        """Test Codex interactive launch receives an initial prompt."""
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Nick", "cli": "codex", "model": "gpt-5.4"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = self._make_agent_manager(
            "Nick", "codex", session_id="sess-codex", model="gpt-5.4"
        )
        mock_agent_manager_cls.return_value = agent_manager
        mock_run.return_value = MagicMock(returncode=0)

        result = launch_chat_session(
            "developer",
            "issue123",
            initial_prompt="Please guide this alignment decision.",
        )

        assert result == 0
        assert mock_run.call_args.args[0] == [
            "codex",
            "--model",
            "gpt-5.4",
            "resume",
            "sess-codex",
            "Please guide this alignment decision.",
        ]
        assert (
            mock_run.call_args.kwargs["env"]["CAFE_CHAT_INITIAL_PROMPT"]
            == "Please guide this alignment decision."
        )


def test_prepare_chat_environment_installs_chat_skills_only() -> None:
    with (
        patch("cafe.ui.chat.SkillLoader.discover"),
        patch("cafe.ui.chat.NativeSkillBridge.synchronize_skills") as mock_synchronize,
    ):
        _prepare_chat_environment(
            agent_cli=AgentCLI.CODEX,
            playbook={
                "skills": {
                    "chat": {
                        "shared": ["chat-base"],
                        "roles": {
                            "developer": {"mode": "extend", "skills": ["chat-role"]}
                        },
                        "steps": {
                            "develop": {"mode": "replace", "skills": ["chat-step"]}
                        },
                    }
                }
            },
            role="developer",
            step_name="develop",
        )

    assert mock_synchronize.call_args.args == (["chat-step"], AgentCLI.CODEX)


def test_prepare_chat_environment_removes_previously_installed_chat_skills(
    tmp_path: Path, monkeypatch
) -> None:
    """I3 — a chat replace environment is isolated in the native skill directory."""
    monkeypatch.chdir(tmp_path)
    builtin_root = tmp_path / "builtin"
    for name in ("chat-stale", "chat-current"):
        skill_dir = builtin_root / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n", encoding="utf-8"
        )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    loader.discover()
    NativeSkillBridge(loader, project_root=tmp_path).install_skill("chat-stale", AgentCLI.CODEX)
    monkeypatch.setattr("cafe.ui.chat.SkillLoader", lambda: loader)

    _prepare_chat_environment(
        agent_cli=AgentCLI.CODEX,
        playbook={
            "skills": {
                "chat": {
                    "shared": ["chat-stale"],
                    "steps": {"question": {"mode": "replace", "skills": ["chat-current"]}},
                }
            }
        },
        role="researcher",
        step_name="question",
    )

    native_skills = tmp_path / ".codex" / "skills"
    assert not (native_skills / "chat-stale").exists()
    assert (native_skills / "chat-current" / "SKILL.md").is_file()

    _prepare_chat_environment(
        agent_cli=AgentCLI.CODEX,
        playbook={"skills": {"chat": {"shared": []}}},
        role="researcher",
        step_name="question",
    )

    assert not (native_skills / "chat-current").exists()


def test_launch_chat_session_stops_before_cli_when_playbook_validation_fails(
    tmp_path: Path, monkeypatch, mock_chat_environment
) -> None:
    """I4 — invalid declared chat skills fail before the interactive CLI starts."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid-chat"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        '{"schema_version":1,"playbook_id":"invalid","current_step":"develop",'
        '"artifacts":{},"events":[],"decisions":[]}',
        encoding="utf-8",
    )

    with (
        patch("builtins.print") as mock_print,
        patch("cafe.ui.chat.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager"),
        patch("cafe.ui.chat.PlaybookLoader") as mock_loader_cls,
    ):
        mock_config = MagicMock()
        mock_config.config_dir = str(tmp_path / ".cafe")
        mock_config.get.return_value = {"name": "David", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config
        mock_loader_cls.return_value.load.side_effect = ValueError(
            "skills.chat.shared references missing skill 'not-installed'"
        )

        result = launch_chat_session("developer", "invalid-chat")

    assert result == 1
    mock_run.assert_not_called()
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "not-installed" in printed


def test_latest_role_iteration_cli_infers_codex_for_phase_chain_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue123"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (iteration_dir / "iteration.json").write_text(
        json.dumps(
            {
                "cli": "claude",
                "response": "confirmed",
                "end_time": "2026-05-19T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (iteration_dir / "streaming.jsonl").write_text(
        json.dumps({"type": "thread.started", "thread_id": "thread-123"}) + "\n",
        encoding="utf-8",
    )

    result = _load_latest_role_iteration_cli(
        issue_dir,
        role="developer",
        role_config={
            "name": "David",
            "clis": [
                    {"cli": "claude", "model": "sonnet"},
                    {"cli": "codex", "model": "gpt-5.3-codex"},
            ],
        },
    )

    assert result == ("codex", "gpt-5.3-codex")


def test_launch_chat_session_prepares_chat_handoff_directory(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch("cafe.ui.chat.subprocess.run", return_value=MagicMock(returncode=0)),
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = MagicMock(session_id=None, model=None)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_chat_session("pm", "issue123")

    assert result == 0
    assert get_chat_next_step_path(tmp_path / ".cafe" / "issues" / "issue123").parent.exists()


def test_launch_chat_session_uses_active_phase_chain(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "research-1"
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        '{"schema_version":1,"playbook_id":"research","current_step":"question","artifacts":{},"events":[],"decisions":[]}',
        encoding="utf-8",
    )
    (tmp_path / ".cafe" / "phases.yaml").write_text(
        "question:\n"
        "  name: Morgan\n"
        "  role: researcher\n"
        "  clis:\n"
        "    - cli: claude\n"
        "      model: sonnet\n",
        encoding="utf-8",
    )

    with (
        patch("cafe.ui.chat.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
        patch("cafe.ui.chat.PlaybookLoader") as mock_loader_cls,
        patch("cafe.ui.chat.get_git_toplevel", return_value=tmp_path),
        patch("cafe.ui.chat.get_repo_root", return_value=tmp_path),
    ):
        mock_config = MagicMock()
        mock_config.config_dir = str(tmp_path / ".cafe")
        mock_config.get.return_value = None
        mock_config_manager_cls.return_value = mock_config
        mock_loader_cls.return_value.load.return_value = {
            "playbook": {"id": "research"},
            "roles": {"researcher": {"default_agent": "Morgan"}},
            "steps": {"question": {"role": "researcher"}},
        }

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = AgentConfig(name="Morgan", cli=AgentCLI.CLAUDE, model="sonnet")
        executor._get_cli_strategy.return_value = ClaudeCLI(executor.config)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_chat_session("researcher", "research-1")

    assert result == 0
    assert mock_run.call_args.args[0] == ["claude", "--model", "sonnet"]
    registered_config = agent_manager.register_agent.call_args.args[0]
    assert registered_config.name == "Morgan"
    assert registered_config.cli == AgentCLI.CLAUDE


@pytest.mark.parametrize(
    "phase_config",
    [
        None,
        "develop:\n  name: David\n  role: developer\n  clis: invalid\n",
    ],
    ids=["missing", "invalid"],
)
def test_paused_human_task_chat_fails_closed_through_phase_loader(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
    mock_phase_config_boundary_for_legacy_chat_fixtures,
    phase_config,
) -> None:
    """Paused chat resolves the originating agent step through production config."""
    mock_phase_config_boundary_for_legacy_chat_fixtures.undo()
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue407"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="develop",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        source="workflow.pause",
    )
    if phase_config is not None:
        (tmp_path / ".cafe" / "phases.yaml").write_text(
            phase_config,
            encoding="utf-8",
        )

    with (
        patch("builtins.print") as mock_print,
        patch("cafe.ui.chat.subprocess.run") as mock_run,
        patch("cafe.ui.chat.PlaybookLoader") as mock_loader_cls,
        patch("cafe.ui.chat.get_git_toplevel", return_value=tmp_path),
        patch("cafe.ui.chat.get_repo_root", return_value=tmp_path),
    ):
        mock_loader_cls.return_value.load.return_value = {
            "playbook": {"id": "default"},
            "roles": {"developer": {"default_agent": "David"}},
            "steps": {"develop": {"role": "developer"}},
        }

        result = launch_chat_session("developer", "issue407")

    assert result == 1
    mock_run.assert_not_called()
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "Skipping chat" not in printed
    assert "invalid phase config" in printed
    assert "develop" in printed
    assert "field='develop" in printed


def test_prepare_chat_handoff_state_creates_blackboard_and_clears_stale_baton(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue123"
    next_step_path = get_chat_next_step_path(issue_dir)
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "blackboard.json").write_text(
        '{"schema_version":1,"playbook_id":"default","current_step":"review","artifacts":{},"events":[],"decisions":[]}',
        encoding="utf-8",
    )
    next_step_path.write_text("review\n", encoding="utf-8")

    current_step, valid_steps, playbook_id = _prepare_chat_handoff_state(issue_dir)

    assert current_step == "review"
    assert "spec" in valid_steps
    assert playbook_id == "default"
    assert (issue_dir / "blackboard.json").exists()
    assert next_step_path.exists()


def test_prepare_chat_handoff_state_preserves_user_clarification_baton(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue123"
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("user", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.NEED_CLARIFICATION,
        status_code="need_clarification",
        source="workflow.pause",
    )

    current_step, valid_steps, playbook_id = _prepare_chat_handoff_state(issue_dir)

    assert current_step == "user"
    assert "spec" in valid_steps
    assert playbook_id == "standard"
    reloaded = store.load_or_create("user", playbook_id="standard")
    assert reloaded.handoff_contract is not None
    assert reloaded.handoff_contract.from_step == "spec"
    assert reloaded.handoff_contract.intent == HandoffIntent.NEED_CLARIFICATION
    assert reloaded.handoff_contract.source == "workflow.pause"


def test_launch_chat_session_warns_when_baton_missing(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch("builtins.print") as mock_print,
        patch("cafe.ui.chat.subprocess.run", return_value=MagicMock(returncode=0)),
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = MagicMock(session_id=None, model=None)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_chat_session("pm", "issue123")

    assert result == 0
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "did not complete workflow handoff" in printed


def test_launch_chat_session_reports_broken_cursor_cli_on_launch_failure(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch("builtins.print") as mock_print,
        patch("cafe.ui.chat.subprocess.run") as mock_run,
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "David", "cli": "cursor-agent"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = MagicMock(session_id="sess-cursor", model=None)
        executor._get_cli_strategy.return_value.build_environment.return_value = dict(os.environ)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Cannot find module '@anysphere/file-service-darwin-x64'",
        )

        result = launch_chat_session("developer", "issue123")

    assert result == 1
    mock_run.assert_called_once()
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "missing native module" in printed
    assert "did not complete workflow handoff" not in printed


def test_launch_chat_session_nonzero_exit_skips_baton_warning(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch("builtins.print") as mock_print,
        patch("cafe.ui.chat.subprocess.run", return_value=MagicMock(returncode=1)),
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = MagicMock(session_id=None, model=None)
        executor._get_cli_strategy.return_value.build_environment.return_value = dict(os.environ)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_chat_session("pm", "issue123")

    assert result == 1
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "did not complete workflow handoff" not in printed


def test_launch_chat_session_nonzero_exit_reports_generic_cli_error(
    tmp_path,
    monkeypatch,
    mock_chat_environment,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch("builtins.print") as mock_print,
        patch(
            "cafe.ui.chat.subprocess.run",
            return_value=MagicMock(returncode=2, stderr="authentication failed\nextra detail"),
        ),
        patch("cafe.ui.chat.ConfigManager") as mock_config_manager_cls,
        patch("cafe.ui.chat.AgentManager") as mock_agent_manager_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.return_value = {"name": "Roger", "cli": "claude"}
        mock_config_manager_cls.return_value = mock_config

        agent_manager = MagicMock()
        executor = MagicMock()
        executor.config = MagicMock(session_id=None, model=None)
        executor._get_cli_strategy.return_value.build_environment.return_value = dict(os.environ)
        agent_manager.get_agent.return_value = executor
        agent_manager.session_manager = MagicMock()
        mock_agent_manager_cls.return_value = agent_manager

        result = launch_chat_session("pm", "issue123")

    assert result == 2
    printed = " ".join(str(call) for call in mock_print.call_args_list)
    assert "Chat CLI exited with code 2: authentication failed" in printed
