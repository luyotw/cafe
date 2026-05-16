"""Tests for init command with phase-specific configuration."""

import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from cafe.ui.cli import app, CUSTOM_MODEL_SENTINEL

runner = CliRunner()

@pytest.fixture
def mock_dependencies():
    with patch("cafe.ui.cli.check_available_clis", return_value=["copilot"]) as mock_check, \
         patch("cafe.ui.cli.list_available_agents") as mock_list_agents, \
         patch("cafe.ui.cli.ConfigManager") as mock_config_manager_class, \
         patch("cafe.utils.crew.CrewManager") as mock_crew_manager_class:

        mock_list_agents.return_value = [("AgentName", "Description", "path/to/file", "system")]

        mock_config_manager = MagicMock()
        mock_config_manager.config_file.exists.return_value = False
        mock_config_manager_class.return_value = mock_config_manager

        mock_crew_manager = MagicMock()
        mock_crew_manager_class.return_value = mock_crew_manager

        yield {
            "config_manager": mock_config_manager,
            "crew_manager": mock_crew_manager,
        }

def test_init_prompts_for_phase_specific_models(mock_dependencies):
    """Test init command prompts for phase-specific models when requested."""

    with patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
         patch("cafe.ui.cli.prompt_text") as mock_prompt_text:

        agent = "AgentName: Description (system default)"

        # New flow: prompt_list handles CLI + agent + model steps
        # PM: cli, agent, spec_model(custom)
        # Dev: cli, agent, plan_model(custom), develop_model(default), pr_model(default)
        # Reviewer: cli, agent, review_model(default)
        mock_prompt_list.side_effect = [
            "copilot", agent, "",                                  # PM: all default
            "copilot", agent, CUSTOM_MODEL_SENTINEL, "", "",       # Dev: plan=custom
            "copilot", agent, "",                                  # Reviewer: all default
        ]

        # prompt_text only called for custom model entries
        mock_prompt_text.side_effect = [
            "plan-model",  # Dev: plan phase
        ]

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0

        # Verify crew.yaml (agents config saved via CrewManager)
        crew_config = mock_dependencies["crew_manager"].save.call_args[0][0]

        # Verify developer config
        dev_config = crew_config["developer"]
        assert "model" not in dev_config
        assert dev_config["plan"]["model"] == "plan-model"
        assert "develop" not in dev_config
        assert "pr" not in dev_config

        # Verify PM and Reviewer configs
        pm_config = crew_config["pm"]
        reviewer_config = crew_config["reviewer"]
        assert "model" not in pm_config
        assert "model" not in reviewer_config
        assert "spec" not in pm_config
        assert "review" not in reviewer_config

        # Verify config.yaml has no agents section
        saved_config = mock_dependencies["config_manager"].save_config.call_args[0][0]
        assert "agents" not in saved_config

def test_init_with_pm_reviewer_models_stores_only_phase_specific(mock_dependencies):
    """Test that all roles store only phase-specific models without role-level."""

    with patch("cafe.ui.cli.prompt_list") as mock_prompt_list, \
         patch("cafe.ui.cli.prompt_text") as mock_prompt_text:

        agent = "AgentName: Description (system default)"
        CS = CUSTOM_MODEL_SENTINEL

        # All roles select custom models for all phases
        mock_prompt_list.side_effect = [
            "copilot", agent, CS,                # PM: spec=custom
            "copilot", agent, CS, CS, CS,        # Dev: plan=custom, develop=custom, pr=custom
            "copilot", agent, CS,                # Reviewer: review=custom
        ]

        mock_prompt_text.side_effect = [
            "pm-model",        # PM: spec
            "dev-plan", "dev-develop", "dev-pr",  # Dev: plan, develop, pr
            "reviewer-model",  # Reviewer: review
        ]

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0

        crew_config = mock_dependencies["crew_manager"].save.call_args[0][0]

        # Verify PM config
        pm_config = crew_config["pm"]
        assert "model" not in pm_config
        assert pm_config["spec"]["model"] == "pm-model"

        # Verify Reviewer config
        reviewer_config = crew_config["reviewer"]
        assert "model" not in reviewer_config
        assert reviewer_config["review"]["model"] == "reviewer-model"

        # Verify Developer config
        dev_config = crew_config["developer"]
        assert "model" not in dev_config
        assert dev_config["plan"]["model"] == "dev-plan"
        assert dev_config["develop"]["model"] == "dev-develop"
        assert dev_config["pr"]["model"] == "dev-pr"
