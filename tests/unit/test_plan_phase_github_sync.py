import json
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import pytest

from cafe.phases.plan_phase import PlanPhase
from cafe.core.status_codes import PhaseStatusCode
from cafe.utils.github import GitHubError

@pytest.fixture
def mock_agent_manager():
    return MagicMock()

@pytest.fixture
def mock_permission_handler():
    return MagicMock()

@pytest.fixture
def mock_git_ops():
    mock = MagicMock()
    mock.get_repo_root.return_value = Path("/tmp/repo")
    return mock

@pytest.fixture
def plan_phase(mock_agent_manager, mock_permission_handler, mock_git_ops, tmp_path):
    # Setup issue directory
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True)
    
    # Mock _get_issue_dir to return our tmp issue_dir
    with patch.object(PlanPhase, '_get_issue_dir', return_value=issue_dir):
        phase = PlanPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            spec_file="spec.md",
            issue_name="test-issue"
        )
    return phase

def test_sync_plan_to_github_success(plan_phase, tmp_path):
    # Setup issue.yaml with issue_id in spec section
    issue_config = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
    issue_config.write_text("spec:\n  issue_id: '123'\n")
    
    # Setup plan_001.md
    plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan_001.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("Test Plan Content")
    
    plan_phase.plan_file = plan_file
    
    with patch("cafe.phases.plan_phase.GitHubOps") as MockGitHubOps:
        mock_gh = MockGitHubOps.return_value
        mock_gh.check_gh_installed.return_value = True
        mock_gh.check_gh_auth.return_value = True
        
        plan_phase._sync_plan_to_github()
        
        mock_gh.add_issue_comment.assert_called_once()
        args, kwargs = mock_gh.add_issue_comment.call_args
        assert args[0] == "123"
        assert "### 📝 Implementation Plan (Confirmed)" in args[1]
        assert "Test Plan Content" in args[1]

def test_sync_plan_to_github_no_issue_id(plan_phase, tmp_path):
    # Setup issue.yaml without issue_id
    issue_config = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
    issue_config.write_text("rigor: medium\n")
    
    with patch("cafe.phases.plan_phase.GitHubOps") as MockGitHubOps:
        mock_gh = MockGitHubOps.return_value
        plan_phase._sync_plan_to_github()
        mock_gh.add_issue_comment.assert_not_called()

def test_sync_plan_to_github_gh_not_installed(plan_phase, tmp_path):
    # Setup issue.yaml with issue_id in spec section
    issue_config = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
    issue_config.write_text("spec:\n  issue_id: '123'\n")
    
    with patch("cafe.phases.plan_phase.GitHubOps") as MockGitHubOps:
        mock_gh = MockGitHubOps.return_value
        mock_gh.check_gh_installed.return_value = False
        
        # Should not raise exception
        plan_phase._sync_plan_to_github()
        mock_gh.add_issue_comment.assert_not_called()

def test_sync_plan_to_github_error_silenced(plan_phase, tmp_path):
    # Setup issue.yaml with issue_id in spec section
    issue_config = tmp_path / ".cafe" / "issues" / "test-issue" / "issue.yaml"
    issue_config.write_text("spec:\n  issue_id: '123'\n")
    
    # Setup plan_001.md
    plan_file = tmp_path / ".cafe" / "issues" / "test-issue" / "plan" / "plan_001.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("Test Plan Content")
    plan_phase.plan_file = plan_file
    
    with patch("cafe.phases.plan_phase.GitHubOps") as MockGitHubOps:
        mock_gh = MockGitHubOps.return_value
        mock_gh.check_gh_installed.return_value = True
        mock_gh.check_gh_auth.return_value = True
        mock_gh.add_issue_comment.side_effect = GitHubError("Network error")
        
        # Should not raise exception
        plan_phase._sync_plan_to_github()
        mock_gh.add_issue_comment.assert_called_once()