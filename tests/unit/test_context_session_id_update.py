"""Test that context.json captures session_id created during agent execution."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseResult, PhaseStatus, TokenUsage


class TestContextSessionIDUpdate:
    """Verify context.json captures session_id created during agent execution."""

    @pytest.fixture
    def mock_agent_manager(self):
        """Mock agent manager that simulates session creation."""
        manager = MagicMock()
        executor = MagicMock()
        
        # Initially no session_id
        executor.config.session_id = None
        executor.config.cli.value = "copilot"
        
        manager.get_agent.return_value = executor
        manager.preview_cli_command_args = MagicMock(return_value=["--model", "claude-sonnet-4.5"])
        manager.preview_cli_environment = MagicMock(return_value={"CODEX_HOME": "/tmp/.codex"})
        
        # Simulate agent execution creates a session
        def mock_execute(*args, **kwargs):
            # After execution, session_id is created
            executor.config.session_id = "new-session-123"

            return (
                "ready_for_review",
                TokenUsage(),
                [],  # permission_denials
                ["--model", "claude-sonnet-4.5"],  # cli_command_args
                [],  # streaming_log
                "claude-sonnet-4.5",  # model
            )
        
        manager.execute = mock_execute
        
        return manager, executor

    def test_context_json_captures_created_session_id(
        self, tmp_path, mock_agent_manager
    ):
        """context.json should contain the session_id created during execution, not None."""
        manager, executor = mock_agent_manager
        
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        
        # Create initial spec file
        spec_file = spec_dir / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Initial Requirements\n\nTest spec")
        
        # Create phase with mocked agent manager
        git_ops = MagicMock()
        git_ops.get_current_branch.return_value = "test-issue"

        with patch.object(SpecPhase, "_get_issue_dir", return_value=issue_dir):
            phase = SpecPhase(
                issue_name="test-issue",
                agent_manager=manager,
                permission_handler=MagicMock(),
                git_ops=git_ops,
            )
        
        # Execute phase (will call _execute_with_agent internally)
        with patch.object(phase, "_prompt_for_rigor"):
            with patch.object(phase, "_check_if_already_completed", return_value=None):
                result = phase.execute()
        
        # Verify context.json exists
        context_file = spec_dir / "iteration_001" / "context.json"
        assert context_file.exists(), "context.json should be created"
        
        # Read context.json
        with open(context_file) as f:
            context_data = json.load(f)
        
        # Verify session_id is the one created during execution, not None
        assert "session_id" in context_data
        assert context_data["session_id"] == "new-session-123", (
            "context.json should capture the session_id created during execution"
        )
        assert context_data["session_id"] is not None, (
            "session_id should not be None after agent execution"
        )
        
        # Verify other agent fields are also populated
        assert context_data["cli"] == "copilot"
        assert context_data["model"] == "claude-sonnet-4.5"
        assert context_data["cli_command_args"] == ["--model", "claude-sonnet-4.5"]
        assert context_data["cli_environment"] == {"CODEX_HOME": "/tmp/.codex"}
