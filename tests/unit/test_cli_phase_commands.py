"""Tests for CLI phase commands passing phase_name to setup."""

import pytest
from unittest.mock import patch, MagicMock, ANY
from typer.testing import CliRunner
from cafe.ui.cli import app

runner = CliRunner()

@pytest.fixture
def mock_dependencies():
    with patch("cafe.ui.cli.ConfigManager") as mock_config_manager, \
         patch("cafe.ui.cli._setup_agents") as mock_setup_agents, \
         patch("cafe.ui.cli.GitOperations") as mock_git_ops, \
         patch("cafe.ui.cli._get_latest_versioned_file") as mock_get_latest_file, \
         patch("cafe.ui.cli.is_branch_initialized", return_value=True), \
         patch("cafe.ui.cli.SpecPhase"), \
         patch("cafe.ui.cli.PlanPhase"), \
         patch("cafe.ui.cli.DevelopPhase"), \
         patch("cafe.ui.cli.ReviewPhase"), \
         patch("cafe.ui.cli.PRPhase"):
        
        # Setup common mocks
        mock_git_instance = MagicMock()
        mock_git_instance.is_valid_branch.return_value = True
        mock_git_instance.get_current_branch.return_value = "issue-123"
        mock_git_ops.return_value = mock_git_instance
        
        mock_agent_manager = MagicMock()
        mock_agent_executor = MagicMock()
        mock_agent_executor.config.cli.value = "copilot"
        mock_agent_executor.config.session_id = "session-123"
        mock_agent_manager.get_agent.return_value = mock_agent_executor
        mock_setup_agents.return_value = mock_agent_manager
        
        mock_get_latest_file.return_value = "some/file/path"
        
        yield {
            "setup_agents": mock_setup_agents,
            "git_ops": mock_git_ops
        }

def test_spec_command_passes_phase_name(mock_dependencies):
    """Test spec command passes phase_name='spec'."""
    runner.invoke(app, ["spec", "--no-interactive", "--user-input", "test"])
    
    mock_dependencies["setup_agents"].assert_called_with(
        ANY, 
        issue_name="issue-123", 
        phase_name="spec"
    )

def test_plan_command_passes_phase_name(mock_dependencies):
    """Test plan command passes phase_name='plan'."""
    with patch("cafe.ui.cli.select_template", return_value="default"), \
         patch("cafe.templates.manager.TemplateManager"):
        runner.invoke(app, ["plan", "--no-interactive", "--template", "default"])
    
    mock_dependencies["setup_agents"].assert_called_with(
        ANY, 
        issue_name="issue-123", 
        phase_name="plan"
    )

def test_develop_command_passes_phase_name(mock_dependencies):
    """Test develop command passes phase_name='develop'."""
    runner.invoke(app, ["develop", "--no-interactive", "--user-input", "test"])
    
    mock_dependencies["setup_agents"].assert_called_with(
        ANY, 
        issue_name="issue-123", 
        phase_name="develop"
    )

def test_review_command_passes_phase_name(mock_dependencies):
    """Test review command passes phase_name='review'."""
    runner.invoke(app, ["review", "--no-interactive"])
    
    mock_dependencies["setup_agents"].assert_called_with(
        ANY, 
        issue_name="issue-123", 
        phase_name="review"
    )

def test_pr_command_passes_phase_name(mock_dependencies):
    """Test pr command passes phase_name='pr'."""
    with patch("cafe.ui.cli.GitHubOps"):
        runner.invoke(app, ["pr", "--no-interactive"])
        
    mock_dependencies["setup_agents"].assert_called_with(
        ANY, 
        issue_name="issue-123", 
        phase_name="pr"
    )