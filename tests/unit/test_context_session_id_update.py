"""Test that iteration.json captures session_id created during agent execution."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentCLI, TokenUsage
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


def _build_loader(tmp_path: Path) -> GenericPhase:
    skill_root = tmp_path / "builtin" / "skills"
    for name, body in {
        "plan": "Write plan to: {output_file}\n",
        "workflow-common": "Read blackboard first.\n",
    }.items():
        skill_dir = skill_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\n\n{body}",
            encoding="utf-8",
        )
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(
            loader,
            project_root=tmp_path,
            home_dir=tmp_path / "home",
        ),
    )


class TestContextSessionIDUpdate:
    """Verify iteration.json captures session_id created during agent execution."""

    @pytest.fixture
    def mock_agent_manager(self):
        manager = MagicMock()
        executor = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.COPILOT, session_id=None, model=None)
        )
        manager.get_agent.return_value = executor
        manager.preview_cli_command_args = MagicMock(return_value=["--model", "claude-sonnet-4.5"])
        manager.preview_cli_environment = MagicMock(return_value={"CODEX_HOME": "/tmp/.codex"})

        def mock_execute(*args, **kwargs):
            executor.config.session_id = "new-session-123"
            return (
                "ready_for_review",
                TokenUsage(),
                [],
                ["--model", "claude-sonnet-4.5"],
                [],
                "claude-sonnet-4.5",
            )

        manager.execute = mock_execute
        return manager

    def test_context_json_captures_created_session_id(
        self, tmp_path: Path, mock_agent_manager
    ) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Initial Requirements\n\nTest spec", encoding="utf-8")

        plan_dir = issue_dir / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)

        git_ops = MagicMock()
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_default_base_branch.return_value = "main"
        git_ops.get_commits_between.return_value = ""

        playbook = {
            "playbook": {"id": "default"},
            "roles": {"developer": {"default_agent": "David"}},
            "steps": {
                "plan": {
                    "skill": "plan",
                    "role": "developer",
                    "output_artifact": "plan",
                    "on": {"await_agent": "develop"},
                }
            },
        }

        executor = GenericWorkflowStepExecutor(
            issue_dir=issue_dir,
            issue_name="test-issue",
            playbook=playbook,
            generic_phase=_build_loader(tmp_path),
            agent_manager=mock_agent_manager,
            git_ops=git_ops,
            role_agent_map={"developer": "David"},
            interactive=False,
        )
        executor.phase_dir = plan_dir
        executor.issue_dir = issue_dir
        executor.iteration = 1

        executor._execute_agent_iteration(
            agent_name="David",
            prompt="Draft a plan",
            user_input="",
            valid_intents=[PhaseStatusCode.READY_FOR_REVIEW],
            require_status_code=False,
            allowed_tools=[],
        )

        context_file = plan_dir / "iteration_001" / "iteration.json"
        assert context_file.exists(), "iteration.json should be created"

        context_data = json.loads(context_file.read_text(encoding="utf-8"))
        assert context_data.get("session_id") == "new-session-123"
        assert context_data["cli"] == "copilot"
        assert context_data["model"] == "claude-sonnet-4.5"
        assert context_data["cli_command_args"] == ["--model", "claude-sonnet-4.5"]
        assert context_data["cli_environment"] == {"CODEX_HOME": "/tmp/.codex"}

    def test_context_json_uses_execution_config_for_fallback_session(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Initial Requirements\n\nTest spec", encoding="utf-8")

        plan_dir = issue_dir / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)

        class MockAgentManager:
            def __init__(self) -> None:
                self._base_agent = SimpleNamespace(
                    config=SimpleNamespace(cli=AgentCLI.CLAUDE, session_id=None, model=None)
                )

            def get_agent(self, name):
                return self._base_agent

            def get_execution_config(self, agent_name: str, phase_name: str = "plan"):
                return SimpleNamespace(
                    name=agent_name,
                    cli=AgentCLI.GEMINI,
                    model="gemini-model",
                    session_id="plan-gemini-session",
                    clis=[],
                    backup_clis=[],
                    models_config={},
                )

            def preview_cli_command_args(self, *args, **kwargs):
                return ["--model", "gemini-model"]

            def preview_cli_environment(self, *args, **kwargs):
                return {}

            def execute(self, *args, **kwargs):
                return (
                    "ready_for_review",
                    TokenUsage(),
                    [],
                    ["--model", "gemini-model"],
                    [],
                    "gemini-model",
                )

            def get_last_cli(self):
                return AgentCLI.GEMINI

            def get_last_session_id(self):
                return None

        mock_agent_manager = MockAgentManager()

        git_ops = MagicMock()
        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_main_branch.return_value = "main"
        git_ops.get_default_base_branch.return_value = "main"
        git_ops.get_commits_between.return_value = ""

        playbook = {
            "playbook": {"id": "default"},
            "roles": {"developer": {"default_agent": "David"}},
            "steps": {
                "plan": {
                    "skill": "plan",
                    "role": "developer",
                    "output_artifact": "plan",
                    "on": {"await_agent": "develop"},
                }
            },
        }

        executor = GenericWorkflowStepExecutor(
            issue_dir=issue_dir,
            issue_name="test-issue",
            playbook=playbook,
            generic_phase=_build_loader(tmp_path),
            agent_manager=mock_agent_manager,
            git_ops=git_ops,
            role_agent_map={"developer": "David"},
            interactive=False,
        )
        executor.phase_dir = plan_dir
        executor.issue_dir = issue_dir
        executor.iteration = 1

        executor._execute_agent_iteration(
            agent_name="David",
            prompt="Draft a plan",
            user_input="",
            valid_intents=[PhaseStatusCode.READY_FOR_REVIEW],
            require_status_code=False,
            allowed_tools=[],
        )

        context_file = plan_dir / "iteration_001" / "iteration.json"
        context_data = json.loads(context_file.read_text(encoding="utf-8"))
        assert context_data.get("cli") == "gemini"
        assert context_data.get("session_id") == "plan-gemini-session"
        assert context_data["model"] == "gemini-model"
