"""Tests for spec phase GitHub synchronization."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.core.status_codes import PhaseStatusCode
from cafe.utils.github import GitHubError


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


class TestSyncConfirmedSpecToGitHub:
    """Test _sync_confirmed_spec_to_github method."""

    def test_sync_when_fetched_from_github(self, spec_phase):
        """Test sync adds comment to issue when spec was fetched from GitHub."""
        # Setup: Create spec file and set _config_issue_id
        spec_content = "# Confirmed Requirements\n\nTest spec content"
        Path(spec_phase.spec_file).write_text(spec_content)
        spec_phase._config_issue_id = 123

        # Execute
        with patch("cafe.phases.spec_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops.check_gh_installed.return_value = True
            mock_gh_ops.check_gh_auth.return_value = True
            mock_gh_ops_cls.return_value = mock_gh_ops

            spec_phase._sync_confirmed_spec_to_github()

            # Verify: Should add comment with spec content, not update issue body
            mock_gh_ops.add_issue_comment.assert_called_once()
            args, kwargs = mock_gh_ops.add_issue_comment.call_args
            assert args[0] == "123"
            assert "### 📋 Requirements Specification (Confirmed)" in args[1]
            assert spec_content in args[1]
            mock_gh_ops.update_issue.assert_not_called()
    
    def test_no_sync_without_config_issue_id(self, spec_phase):
        """Test no sync when spec was not fetched from GitHub."""
        # Setup: Create spec file but no _config_issue_id
        Path(spec_phase.spec_file).write_text("# Test spec")

        # Execute
        with patch("cafe.phases.spec_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops_cls.return_value = mock_gh_ops

            spec_phase._sync_confirmed_spec_to_github()

            # Verify: No API call made
            mock_gh_ops.update_issue.assert_not_called()
            mock_gh_ops.add_issue_comment.assert_not_called()
    
    def test_no_sync_when_spec_file_missing(self, spec_phase):
        """Test no sync when spec file doesn't exist."""
        # Setup: Set _config_issue_id but no spec file
        spec_phase._config_issue_id = 123

        # Execute
        with patch("cafe.phases.spec_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops_cls.return_value = mock_gh_ops

            spec_phase._sync_confirmed_spec_to_github()

            # Verify: No API call made
            mock_gh_ops.update_issue.assert_not_called()
            mock_gh_ops.add_issue_comment.assert_not_called()
    
    def test_handles_github_error_gracefully(self, spec_phase, capsys):
        """Test graceful error handling when GitHub API fails."""
        # Setup
        spec_content = "# Test spec"
        Path(spec_phase.spec_file).write_text(spec_content)
        spec_phase._config_issue_id = 123

        # Execute
        with patch("cafe.phases.spec_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops.check_gh_installed.return_value = True
            mock_gh_ops.check_gh_auth.return_value = True
            mock_gh_ops.add_issue_comment.side_effect = GitHubError("API rate limit exceeded")
            mock_gh_ops_cls.return_value = mock_gh_ops

            # Should not raise exception
            spec_phase._sync_confirmed_spec_to_github()

            # Verify warning message contains error details
            captured = capsys.readouterr()
            assert "Warning" in captured.out or "warning" in captured.out.lower()
            assert "API rate limit exceeded" in captured.out


class TestConfirmedSyncInWorkflow:
    """Test that sync is triggered correctly in the confirmation workflow."""
    
    def test_sync_triggered_on_user_confirmation(self, spec_phase, tmp_path):
        """Test sync is called when user confirms spec."""
        # Setup: Create previous iteration with READY_FOR_REVIEW status
        prev_iteration_dir = spec_phase.phase_dir / "iteration_001"
        prev_iteration_dir.mkdir(parents=True, exist_ok=True)
        
        prev_context = {
            "user_input": "Initial requirements",
            "response": "Spec is ready",
            "status_code": "CAFE_READY_FOR_REVIEW",
            "phase_specific_data": {"pm_agent": "Roger"}
        }
        import json
        (prev_iteration_dir / "context.json").write_text(json.dumps(prev_context))
        
        # Create spec file
        spec_content = "# Final Spec\n\nConfirmed requirements"
        Path(spec_phase.spec_file).write_text(spec_content)
        spec_phase._config_issue_id = "123"
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
            
            with patch.object(spec_phase, '_sync_confirmed_spec_to_github') as mock_sync:
                # Call the method that handles user confirmation
                result = spec_phase._prepare_user_input_for_iteration()
                
                # Verify sync was called
                assert isinstance(result, PhaseResult)
                assert result.data.get("status_code") == PhaseStatusCode.CONFIRMED.value
                mock_sync.assert_called_once()
    
    def test_no_sync_on_modification_request(self, spec_phase):
        """Test sync is NOT called when user requests modifications."""
        # Setup: Create previous iteration with READY_FOR_REVIEW status
        prev_iteration_dir = spec_phase.phase_dir / "iteration_001"
        prev_iteration_dir.mkdir(parents=True, exist_ok=True)
        
        prev_context = {
            "user_input": "Initial requirements",
            "response": "Spec is ready",
            "status_code": "CAFE_READY_FOR_REVIEW",
            "phase_specific_data": {"pm_agent": "Roger"}
        }
        import json
        (prev_iteration_dir / "context.json").write_text(json.dumps(prev_context))
        
        spec_phase._config_issue_id = "123"
        spec_phase.iteration = 2
        
        # Execute: Simulate user requesting modifications
        with patch.object(spec_phase, '_process_review_decision') as mock_process:
            # Mock _process_review_decision to return modification request (string)
            mock_process.return_value = "Please add more details about authentication"
            
            with patch.object(spec_phase, '_sync_confirmed_spec_to_github') as mock_sync:
                result = spec_phase._prepare_user_input_for_iteration()
                
                # Verify sync was NOT called (returned string, not PhaseResult)
                assert isinstance(result, str)
                mock_sync.assert_not_called()


class TestGetCompletionDataNoSync:
    """Test that _get_completion_data no longer syncs to GitHub."""

    def test_get_completion_data_does_not_sync(self, spec_phase):
        """Test _get_completion_data no longer posts to GitHub."""
        # Setup
        Path(spec_phase.spec_file).write_text("# Test spec")
        spec_phase._config_issue_id = 123

        # Execute
        with patch("cafe.phases.spec_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops_cls.return_value = mock_gh_ops

            data = spec_phase._get_completion_data()

            # Verify: No GitHub API call made
            mock_gh_ops.update_issue.assert_not_called()
            mock_gh_ops.add_issue_comment.assert_not_called()

            # Verify: Still returns spec_file path
            assert "spec_file" in data
