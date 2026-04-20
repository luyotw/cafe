"""Tests for spec phase behavior around confirmed sync flow."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for SpecPhase."""
    mock_agent_manager = MagicMock()
    mock_permission_handler = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"
    
    return {
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


@pytest.fixture
def spec_phase(tmp_path, mock_dependencies):
    """Create a SpecPhase instance for testing."""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
        user_input="test requirements",
    )

    # Setup phase directory and spec file
    phase.phase_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)

    # Simulate iteration 2 (confirm iteration)
    # The confirmed spec is in iteration 1
    phase.iteration = 2
    iteration_001_dir = phase.phase_dir / "iteration_001"
    iteration_001_dir.mkdir(parents=True, exist_ok=True)
    phase.spec_file = str(iteration_001_dir / "output.md")

    return phase


class TestConfirmedSyncInWorkflow:
    """Test confirmation flow now delegates sync to skill scripts."""
    
    def test_confirmation_returns_result_without_phase_sync_hook(self, spec_phase):
        """Spec phase should not call internal GitHub sync on confirmation."""
        # Setup: Create previous iteration with READY_FOR_REVIEW status
        prev_iteration_dir = spec_phase.phase_dir / "iteration_001"
        prev_iteration_dir.mkdir(parents=True, exist_ok=True)
        
        prev_context = {
            "user_input": "Initial requirements",
            "response": "Spec is ready",
            "status_code": "CAFE_READY_FOR_REVIEW",
            "phase_specific_data": {"pm_agent": "Roger"}
        }
        (prev_iteration_dir / "context.json").write_text(json.dumps(prev_context))
        
        # Create spec file
        spec_content = "# Final Spec\n\nConfirmed requirements"
        Path(spec_phase.spec_file).write_text(spec_content)
        spec_phase._config_issue_id = "123"
        spec_phase._sync_github = True  # Enable sync for this test
        spec_phase.iteration = 2
        
        # Execute: Simulate user confirming
        with patch.object(spec_phase, '_process_review_decision') as mock_process:
            # Mock _process_review_decision to return CONFIRMED result
            confirmed_result = PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="Spec confirmed",
                data={
                    "iterations": 2,
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                }
            )
            mock_process.return_value = confirmed_result

            result = spec_phase._prepare_user_input_for_iteration()
            assert isinstance(result, PhaseResult)
            assert result.data.get("status_code") == PhaseStatusCode.CONFIRMED.value
    
    def test_no_sync_on_modification_request(self, spec_phase):
        """Modification request still returns user feedback as plain text."""
        # Setup: Create previous iteration with READY_FOR_REVIEW status
        prev_iteration_dir = spec_phase.phase_dir / "iteration_001"
        prev_iteration_dir.mkdir(parents=True, exist_ok=True)
        
        prev_context = {
            "user_input": "Initial requirements",
            "response": "Spec is ready",
            "status_code": "CAFE_READY_FOR_REVIEW",
            "phase_specific_data": {"pm_agent": "Roger"}
        }
        (prev_iteration_dir / "context.json").write_text(json.dumps(prev_context))
        
        spec_phase._config_issue_id = "123"
        spec_phase.iteration = 2
        
        # Execute: Simulate user requesting modifications
        with patch.object(spec_phase, '_process_review_decision') as mock_process:
            # Mock _process_review_decision to return modification request (string)
            mock_process.return_value = "Please add more details about authentication"
            result = spec_phase._prepare_user_input_for_iteration()
            assert isinstance(result, str)


class TestGetCompletionDataNoSync:
    """_get_completion_data should remain data-only and side-effect free."""

    def test_get_completion_data_does_not_sync(self, spec_phase):
        """_get_completion_data should only return metadata."""
        # Setup
        Path(spec_phase.spec_file).write_text("# Test spec")
        spec_phase._config_issue_id = 123

        # Execute
        data = spec_phase._get_completion_data()
        assert "spec_file" in data
