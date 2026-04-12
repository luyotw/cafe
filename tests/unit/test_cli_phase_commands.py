"""Tests for CLI phase commands passing phase_name to setup."""

import pytest
from unittest.mock import patch, MagicMock, ANY
from typer.testing import CliRunner
from cafe.ui.cli import app

runner = CliRunner()

@pytest.fixture
def mock_dependencies():
    with patch("cafe.ui.cli.ConfigManager") as mock_config_manager, \
         patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias, \
         patch("cafe.ui.cli._setup_agents") as mock_setup_agents, \
         patch("cafe.ui.cli.GitOperations") as mock_git_ops, \
         patch("cafe.ui.cli._get_latest_versioned_file") as mock_get_latest_file, \
         patch("cafe.ui.cli.is_branch_initialized", return_value=True):
        
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
        mock_execute_alias.return_value = {"status_code": "CAFE_CONFIRMED", "iterations": 1}
        
        yield {
            "setup_agents": mock_setup_agents,
            "execute_alias": mock_execute_alias,
            "git_ops": mock_git_ops
        }

def test_spec_command_passes_phase_name(mock_dependencies):
    """Test spec command passes phase_name='spec'."""
    runner.invoke(app, ["spec", "--no-interactive", "--user-input", "test"])
    
    mock_dependencies["execute_alias"].assert_called_with(
        issue_name="issue-123",
        step_name="spec",
        config_manager=ANY,
        role_agent_map_override=None,
        user_input="test",
        show_prompt=False,
    )

def test_plan_command_passes_phase_name(mock_dependencies):
    """Test plan command passes phase_name='plan'."""
    with patch("cafe.ui.cli.select_template", return_value="default"), \
         patch("cafe.templates.manager.TemplateManager"):
        runner.invoke(app, ["plan", "--no-interactive", "--template", "default"])
    
    mock_dependencies["execute_alias"].assert_called_with(
        issue_name="issue-123",
        step_name="plan",
        config_manager=ANY,
        role_agent_map_override=None,
        user_input=None,
        show_prompt=False,
    )

def test_develop_command_passes_phase_name(mock_dependencies):
    """Test develop command passes phase_name='develop'."""
    runner.invoke(app, ["develop", "--no-interactive", "--user-input", "test"])
    
    mock_dependencies["execute_alias"].assert_called_with(
        issue_name="issue-123",
        step_name="develop",
        config_manager=ANY,
        role_agent_map_override=None,
        user_input="test",
        show_prompt=False,
    )

def test_review_command_passes_phase_name(mock_dependencies):
    """Test review command passes phase_name='review'."""
    runner.invoke(app, ["review", "--no-interactive"])
    
    mock_dependencies["execute_alias"].assert_called_with(
        issue_name="issue-123",
        step_name="review",
        config_manager=ANY,
        role_agent_map_override=None,
        show_prompt=False,
    )

def test_pr_command_passes_phase_name(mock_dependencies):
    """Test pr command passes phase_name='pr'."""
    with patch("cafe.ui.cli.GitHubOps"):
        runner.invoke(app, ["pr", "--no-interactive"])

    mock_dependencies["execute_alias"].assert_called_with(
        issue_name="issue-123",
        step_name="pr",
        config_manager=ANY,
        role_agent_map_override=ANY,
        show_prompt=False,
    )


def test_spec_command_shows_questions_before_next_iteration(tmp_path, monkeypatch):
    """Test spec clarification loop renders questions.xml before collecting more input."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-123" / "spec" / "iteration_001"
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "questions.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="q1">
    <title>What should workflow execute do?</title>
    <options>
      <option>Option A</option>
      <option>Option B</option>
    </options>
  </question>
</questions>
""",
        encoding="utf-8",
    )

    with patch.dict("os.environ", {"CAFE_FORCE_INTERACTIVE": "1"}), \
         patch("cafe.ui.cli.ConfigManager") as mock_config_manager, \
         patch("cafe.ui.cli._execute_single_step_alias") as mock_execute_alias, \
         patch("cafe.ui.cli._setup_agents") as mock_setup_agents, \
         patch("cafe.ui.cli.GitOperations") as mock_git_ops, \
         patch("cafe.ui.cli.is_branch_initialized", return_value=True), \
         patch("cafe.ui.cli.prompt_confirm", return_value=True), \
         patch("cafe.ui.cli.prompt_multiline", side_effect=["initial input", "more detail"]):

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

        mock_execute_alias.side_effect = [
            {"status_code": "CAFE_NEED_CLARIFICATION", "iterations": 1},
            {"status_code": "CAFE_CONFIRMED", "iterations": 2},
        ]

        result = runner.invoke(app, ["spec", "--interactive"])

        assert result.exit_code == 0
        assert "Questions to confirm:" in result.stdout
        assert "What should workflow execute do?" in result.stdout
        assert "Option A" in result.stdout
