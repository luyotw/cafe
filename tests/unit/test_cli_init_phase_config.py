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
        
        # prompt_text calls (new behavior):
        # PM: only 1 call for model (goes directly to spec.model)
        # Developer: 4 calls (model + 3 phase models)
        # Reviewer: only 1 call for model (goes directly to review.model)
        # Total: 6 calls
        mock_prompt_text.side_effect = [
            "",  # PM: model (will be set to spec.model)
            "dev-default", "plan-model", "", "",  # Dev: model, plan, develop, pr
            "",  # Reviewer: model (will be set to review.model)
        ]

        # prompt_confirm calls (not used for phase config anymore)
        mock_prompt_confirm.side_effect = []

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0

        # Verify config structure
        mock_config = mock_dependencies["config_manager"].save_config.call_args[0][0]

        # Verify developer config
        dev_config = mock_config["agents"]["developer"]
        assert dev_config["model"] == "dev-default"
        assert dev_config["plan"]["model"] == "plan-model"
        # develop and pr should not be in config when empty string is provided
        assert "develop" not in dev_config
        assert "pr" not in dev_config

        # Verify PM and Reviewer configs
        pm_config = mock_config["agents"]["pm"]
        reviewer_config = mock_config["agents"]["reviewer"]
        # PM and Reviewer should NOT have role-level model when empty string provided
        assert "model" not in pm_config
        assert "model" not in reviewer_config
        # spec and review should not be in config when empty string is provided
        # (no role-level model means no auto-populate to phase-specific)
        assert "spec" not in pm_config
        assert "review" not in reviewer_config

def test_init_with_pm_reviewer_models_creates_fallback_chain(mock_dependencies):
    """Test that PM and Reviewer get both role-level and phase-specific models for fallback."""

    with patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
         patch("cafe.ui.cli.prompt_text") as mock_prompt_text, \
         patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm:

        mock_prompt_list.side_effect = [
            "copilot",
            "AgentName: Description (system default)",
            "copilot",
            "AgentName: Description (system default)",
            "copilot",
            "AgentName: Description (system default)",
        ]

        # PM and Reviewer provide models, Developer provides model + phase models
        mock_prompt_text.side_effect = [
            "pm-model",  # PM: model (will be set to both role-level and spec.model)
            "dev-default", "", "", "",  # Dev: model, plan, develop, pr
            "reviewer-model",  # Reviewer: model (will be set to both role-level and review.model)
        ]

        mock_prompt_confirm.side_effect = []

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0

        # Verify config structure
        mock_config = mock_dependencies["config_manager"].save_config.call_args[0][0]

        # Verify PM config has both role-level and phase-specific model
        pm_config = mock_config["agents"]["pm"]
        assert pm_config["model"] == "pm-model"  # Role-level for fallback
        assert pm_config["spec"]["model"] == "pm-model"  # Phase-specific auto-populated

        # Verify Reviewer config has both role-level and phase-specific model
        reviewer_config = mock_config["agents"]["reviewer"]
        assert reviewer_config["model"] == "reviewer-model"  # Role-level for fallback
        assert reviewer_config["review"]["model"] == "reviewer-model"  # Phase-specific auto-populated

        # Verify Developer config
        dev_config = mock_config["agents"]["developer"]
        assert dev_config["model"] == "dev-default"
        # No phase-specific models provided (empty strings)
        assert "plan" not in dev_config
        assert "develop" not in dev_config
        assert "pr" not in dev_config