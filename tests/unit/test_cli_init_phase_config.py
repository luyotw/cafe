"""Tests for init command with phase-specific configuration."""

import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from cafe.ui.cli import app

runner = CliRunner()

@pytest.fixture
def mock_dependencies():
    with patch("cafe.ui.cli.check_available_clis", return_value=["copilot"]) as mock_check, \
         patch("cafe.ui.cli.list_available_agents") as mock_list_agents, \
         patch("cafe.ui.cli.ConfigManager") as mock_config_manager_class:
        
        mock_list_agents.return_value = [("AgentName", "Description", "path/to/file", "system")]
        
        mock_config_manager = MagicMock()
        mock_config_manager.config_file.exists.return_value = False
        mock_config_manager_class.return_value = mock_config_manager
        
        yield {
            "config_manager": mock_config_manager
        }

def test_init_prompts_for_phase_specific_models(mock_dependencies):
    """Test init command prompts for phase-specific models when requested."""
    
    with patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
         patch("cafe.ui.cli.prompt_text") as mock_prompt_text, \
         patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm:
             
        # Setup return values side effects
        
        # prompt_list calls:
        # 1. Select CLI for PM -> "copilot"
        # 2. Select Agent for PM -> "AgentName: ..."
        # 3. Select CLI for Dev -> "copilot"
        # 4. Select Agent for Dev -> "AgentName: ..."
        # 5. Select CLI for Reviewer -> "copilot"
        # 6. Select Agent for Reviewer -> "AgentName: ..."
        mock_prompt_list.side_effect = [
            "copilot",
            "AgentName: Description (system default)",
            "copilot",
            "AgentName: Description (system default)",
            "copilot",
            "AgentName: Description (system default)",
        ]
        
        # prompt_text calls:
        # 1. PM Model -> ""
        # 2. Dev Model -> "dev-default"
        # 3. Dev Plan Model -> "plan-model"
        # 4. Dev Develop Model -> ""
        # 5. Dev PR Model -> ""
        # 6. Reviewer Model -> ""
        mock_prompt_text.side_effect = [
            "",
            "dev-default",
            "plan-model",
            "",
            "",
            "",
        ]
        
        # prompt_confirm calls:
        # 1. PM Phase Config? -> False
        # 2. Dev Phase Config? -> True
        # 3. Reviewer Phase Config? -> False
        mock_prompt_confirm.side_effect = [
            False,
            True,
            False
        ]
        
        result = runner.invoke(app, ["init"])
        
        assert result.exit_code == 0
        
        # Verify config structure
        mock_config = mock_dependencies["config_manager"].save_config.call_args[0][0]
        
        dev_config = mock_config["agents"]["developer"]
        assert dev_config["model"] == "dev-default"
        assert dev_config["plan"]["model"] == "plan-model"
        assert "develop" not in dev_config
        assert "pr" not in dev_config