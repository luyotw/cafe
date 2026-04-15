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
    """Legacy sync moved to skill scripts; phase no longer owns hook."""

    def test_phase_no_longer_has_internal_confirmed_sync_method(self, plan_phase):
        assert not hasattr(plan_phase, "_sync_plan_to_github")
