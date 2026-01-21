"""Tests for CLI setup agents with phase awareness."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from cafe.ui.cli import _setup_agents
from cafe.utils.config import ConfigManager


class TestSetupAgentsPhaseAware:
    """Test phase-aware agent setup."""

    def test_setup_agents_resolves_phase_specific_model(self, tmp_path: Path) -> None:
        """Test that _setup_agents resolves phase-specific models."""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # Create config with phase-specific model
        custom_config = {
            "agents": {
                "pm": {
                    "name": "Roger", 
                    "cli": "copilot", 
                    "model": "claude-3-opus-20240229",
                    "spec": {
                        "model": "claude-3-haiku-20240307"
                    }
                },
                "developer": {
                    "name": "David", 
                    "cli": "copilot", 
                    "model": "claude-3-sonnet-20240229",
                    "plan": {
                        "model": "claude-3-haiku-20240307"
                    }
                },
                "reviewer": {
                    "name": "Richard",
                    "cli": "copilot"
                }
            }
        }
        config_manager.save_config(custom_config)

        # Test spec phase
        agent_manager = _setup_agents(config_manager, phase_name="spec")
        assert agent_manager.agents["Roger"].config.model == "claude-3-haiku-20240307"

        # Test plan phase
        agent_manager = _setup_agents(config_manager, phase_name="plan")
        assert agent_manager.agents["David"].config.model == "claude-3-haiku-20240307"

    def test_setup_agents_fallbacks_to_role_default(self, tmp_path: Path) -> None:
        """Test that _setup_agents falls back to role default if phase model not set."""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        # Create config without phase-specific model
        custom_config = {
            "agents": {
                "developer": {
                    "name": "David", 
                    "cli": "copilot", 
                    "model": "claude-3-sonnet-20240229"
                },
                "pm": {
                    "name": "Roger",
                    "cli": "copilot"
                },
                "reviewer": {
                    "name": "Richard",
                    "cli": "copilot"
                }
            }
        }
        config_manager.save_config(custom_config)

        # Test develop phase (no specific model)
        agent_manager = _setup_agents(config_manager, phase_name="develop")
        assert agent_manager.agents["David"].config.model == "claude-3-sonnet-20240229"

    def test_setup_agents_ignores_phase_name_if_none(self, tmp_path: Path) -> None:
        """Test that _setup_agents works correctly without phase_name (backward compatibility)."""
        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        custom_config = {
            "agents": {
                "developer": {
                    "name": "David", 
                    "cli": "copilot", 
                    "model": "claude-3-sonnet-20240229",
                    "plan": {
                        "model": "claude-3-haiku-20240307"
                    }
                },
                "pm": {
                    "name": "Roger",
                    "cli": "copilot"
                },
                "reviewer": {
                    "name": "Richard",
                    "cli": "copilot"
                }
            }
        }
        config_manager.save_config(custom_config)

        # Without phase_name, should use default model
        agent_manager = _setup_agents(config_manager)
        assert agent_manager.agents["David"].config.model == "claude-3-sonnet-20240229"
