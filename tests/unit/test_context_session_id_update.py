"""Test that iteration.json captures session_id created during agent execution."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import TokenUsage
from cafe.phases.plan_phase import PlanPhase


class TestContextSessionIDUpdate:
    """Verify iteration.json captures session_id created during agent execution."""

    @pytest.fixture
    def mock_agent_manager(self):
        manager = MagicMock()
        executor = MagicMock()
        executor.config.session_id = None
        executor.config.cli.value = "copilot"

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
        return manager, executor

    def test_context_json_captures_created_session_id(
        self, tmp_path: Path, mock_agent_manager
    ) -> None:
        manager, _executor = mock_agent_manager

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Initial Requirements\n\nTest spec", encoding="utf-8")

        plan_dir = issue_dir / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)

        git_ops = MagicMock()
        git_ops.get_current_branch.return_value = "test-issue"

        phase = PlanPhase(
            issue_name="test-issue",
            agent_manager=manager,
            permission_handler=MagicMock(),
            git_ops=git_ops,
            spec_file=str(spec_file),
            interactive=False,
        )
        phase.phase_dir = plan_dir
        phase.issue_dir = issue_dir
        phase.iteration = 1
        phase.plan_file = str(plan_dir / "iteration_001" / "output.md")

        phase._execute_agent_iteration(
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
