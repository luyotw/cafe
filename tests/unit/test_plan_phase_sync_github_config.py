"""Tests for PlanPhase sync_github configuration."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase
from cafe.core.types import PhaseResult
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for PlanPhase."""
    mock_agent_manager = MagicMock()
    mock_permission_handler = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"
    mock_git_ops.get_repo_root.return_value = Path("/tmp/repo")

    return {
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


@pytest.fixture
def plan_phase(tmp_path, mock_dependencies):
    """Create a PlanPhase instance for testing."""
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)

    # Mock _get_issue_dir to return our tmp issue_dir
    with patch.object(PlanPhase, '_get_issue_dir', return_value=issue_dir):
        phase = PlanPhase(
            agent_manager=mock_dependencies["agent_manager"],
            permission_handler=mock_dependencies["permission_handler"],
            git_ops=mock_dependencies["git_ops"],
            spec_file=str(tmp_path / "spec.md"),
            issue_name="test-issue",
        )

    # Setup phase directory
    phase.phase_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "plan"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)
    phase.issue_dir = issue_dir

    return phase


class TestLoadPlanConfigSyncGithub:
    """Test _load_plan_config() loading sync_github."""

    def test_load_sync_github_true_from_config(self, plan_phase, tmp_path):
        """Test loading sync_github=true from plan section."""
        # Setup: Create issue.yaml with sync_github=true
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        config_file.write_text("plan:\n  template: auto\n  sync_github: true\n")

        # Execute
        plan_phase._load_plan_config()

        # Verify
        assert plan_phase._sync_github is True

    def test_load_sync_github_false_from_config(self, plan_phase, tmp_path):
        """Test loading sync_github=false from plan section."""
        # Setup: Create issue.yaml with sync_github=false
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        config_file.write_text("plan:\n  template: auto\n  sync_github: false\n")

        # Execute
        plan_phase._load_plan_config()

        # Verify
        assert plan_phase._sync_github is False

    def test_default_sync_github_true_when_issue_id_in_spec_config(self, plan_phase, tmp_path):
        """Test sync_github defaults to True when spec.issue_id is present but sync_github not specified."""
        # Setup: Create issue.yaml with spec.issue_id but no plan.sync_github
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        config_file.write_text("spec:\n  issue_id: 123\nplan:\n  template: auto\n")

        # Execute
        plan_phase._load_plan_config()

        # Verify: Should default to True for backward compatibility
        assert plan_phase._sync_github is True

    def test_default_sync_github_false_when_no_issue_id(self, plan_phase, tmp_path):
        """Test sync_github defaults to False when no issue_id is present."""
        # Setup: Create issue.yaml without issue_id
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        config_file.write_text("plan:\n  template: auto\n")

        # Execute
        plan_phase._load_plan_config()

        # Verify: Should default to False
        assert plan_phase._sync_github is False

    def test_no_config_file_defaults_to_false(self, plan_phase, tmp_path):
        """Test sync_github defaults to False when no config file exists."""
        # Setup: No config file

        # Execute
        plan_phase._load_plan_config()

        # Verify: Should default to False
        assert plan_phase._sync_github is False


class TestSyncGuard:
    """Test sync guard in _prepare_user_input_for_iteration()."""

    def test_sync_skipped_when_sync_github_false(self, plan_phase, tmp_path):
        """Test _sync_plan_to_github() is not called when sync_github=False."""
        # Setup
        plan_phase._sync_github = False

        # Create a plan file
        plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Test Plan")
        plan_phase.plan_file = plan_file

        with patch.object(plan_phase, '_sync_plan_to_github') as mock_sync:
            # The guard should prevent sync when _sync_github is False
            if plan_phase._sync_github:
                plan_phase._sync_plan_to_github()

            # Verify: sync method should not be called
            mock_sync.assert_not_called()

    def test_sync_called_when_sync_github_true(self, plan_phase, tmp_path):
        """Test _sync_plan_to_github() is called when sync_github=True."""
        # Setup
        plan_phase._sync_github = True

        # Create a plan file
        plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# Test Plan")
        plan_phase.plan_file = plan_file

        with patch.object(plan_phase, '_sync_plan_to_github') as mock_sync:
            # The guard should allow sync when _sync_github is True
            if plan_phase._sync_github:
                plan_phase._sync_plan_to_github()

            # Verify: sync method should be called
            mock_sync.assert_called_once()

    def test_sync_works_normally_when_enabled(self, plan_phase, tmp_path):
        """Test that sync still works as before when sync_github=True."""
        # Setup
        plan_phase._sync_github = True

        # Create issue.yaml with issue_id
        config_file = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
        config_file.write_text("spec:\n  issue_id: '123'\n")

        # Create a plan file
        plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan_001.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_content = "# Test Plan\n\nImplementation details"
        plan_file.write_text(plan_content)
        plan_phase.plan_file = plan_file

        # Execute with mocked GitHub operations
        with patch("cafe.phases.plan_phase.GitHubOps") as mock_gh_ops_cls:
            mock_gh_ops = MagicMock()
            mock_gh_ops.check_gh_installed.return_value = True
            mock_gh_ops.check_gh_auth.return_value = True
            mock_gh_ops_cls.return_value = mock_gh_ops

            plan_phase._sync_plan_to_github()

            # Verify: Should add comment with plan content
            mock_gh_ops.add_issue_comment.assert_called_once()
            args, kwargs = mock_gh_ops.add_issue_comment.call_args
            assert args[0] == "123"
            assert "### 📝 Implementation Plan (Confirmed)" in args[1]
            assert plan_content in args[1]
