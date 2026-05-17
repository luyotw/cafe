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


class TestSetupAgentsClisChain:
    """Test that setup_agents populates clis chain via normalize_role_config."""

    def _write_crew(self, cafe_dir: Path, content: str) -> None:
        cafe_dir.mkdir(exist_ok=True)
        (cafe_dir / "crew.yaml").write_text(content)

    def test_new_clis_format_populates_clis_chain(self, tmp_path: Path) -> None:
        from cafe.core.types import AgentCLI
        from cafe.ui.cli_shared import setup_agents
        from cafe.utils.config import ConfigManager

        cafe_dir = tmp_path / ".cafe"
        self._write_crew(
            cafe_dir,
            "pm:\n  name: Roger\n  cli: copilot\n"
            "developer:\n  name: David\n  clis:\n"
            "    - cli: claude\n      model: opus\n      plan: sonnet\n"
            "    - cli: gemini\n      model: gemini-2.5-pro\n"
            "reviewer:\n  name: Richard\n  cli: copilot\n",
        )
        agent_manager = setup_agents(ConfigManager(str(cafe_dir / "config.yaml")), cafe_dir=cafe_dir)
        david_clis = agent_manager.agents["David"].config.clis
        assert len(david_clis) == 2
        assert david_clis[0].cli == AgentCLI.CLAUDE
        assert david_clis[0].model == "opus"
        assert david_clis[0].phase_models.get("plan") == "sonnet"
        assert david_clis[1].cli == AgentCLI.GEMINI

    def test_old_format_crew_yaml_populates_clis_chain(self, tmp_path: Path) -> None:
        from cafe.core.types import AgentCLI
        from cafe.ui.cli_shared import setup_agents
        from cafe.utils.config import ConfigManager

        cafe_dir = tmp_path / ".cafe"
        self._write_crew(
            cafe_dir,
            "pm:\n  name: Roger\n  cli: copilot\n"
            "developer:\n  name: David\n  cli: claude\n  model: opus\n"
            "  backup:\n    - gemini\n    - copilot\n"
            "  models:\n    gemini:\n      plan: gemini-2.5-pro\n"
            "reviewer:\n  name: Richard\n  cli: copilot\n",
        )
        agent_manager = setup_agents(ConfigManager(str(cafe_dir / "config.yaml")), cafe_dir=cafe_dir)
        david_clis = agent_manager.agents["David"].config.clis
        assert len(david_clis) == 3
        assert david_clis[0].cli == AgentCLI.CLAUDE
        assert david_clis[1].cli == AgentCLI.GEMINI
        assert david_clis[1].phase_models.get("plan") == "gemini-2.5-pro"
        assert david_clis[2].cli == AgentCLI.COPILOT

    def test_mixed_format_clis_wins(self, tmp_path: Path) -> None:
        from cafe.core.types import AgentCLI
        from cafe.ui.cli_shared import setup_agents
        from cafe.utils.config import ConfigManager

        cafe_dir = tmp_path / ".cafe"
        self._write_crew(
            cafe_dir,
            "pm:\n  name: Roger\n  cli: copilot\n"
            "developer:\n  name: David\n  cli: copilot\n  backup: [gemini]\n"
            "  clis:\n    - cli: claude\n"
            "reviewer:\n  name: Richard\n  cli: copilot\n",
        )
        agent_manager = setup_agents(ConfigManager(str(cafe_dir / "config.yaml")), cafe_dir=cafe_dir)
        david_clis = agent_manager.agents["David"].config.clis
        assert len(david_clis) == 1
        assert david_clis[0].cli == AgentCLI.CLAUDE

    def test_phase_name_resolves_from_clis_chain(self, tmp_path: Path) -> None:
        """AgentConfig.model reflects clis[0].resolve_model(phase_name)."""
        from cafe.ui.cli_shared import setup_agents
        from cafe.utils.config import ConfigManager

        cafe_dir = tmp_path / ".cafe"
        self._write_crew(
            cafe_dir,
            "pm:\n  name: Roger\n  cli: copilot\n"
            "developer:\n  name: David\n  cli: claude\n  model: opus\n"
            "  models:\n    claude:\n      develop: sonnet\n"
            "reviewer:\n  name: Richard\n  cli: copilot\n",
        )
        agent_manager = setup_agents(
            ConfigManager(str(cafe_dir / "config.yaml")),
            phase_name="develop",
            cafe_dir=cafe_dir,
        )
        assert agent_manager.agents["David"].config.model == "sonnet"


class TestSetupAgentsCrewYaml:
    """Test that setup_agents prefers crew.yaml over config.yaml agents section."""

    def test_setup_agents_prefers_crew_yaml_over_config_agents(self, tmp_path: Path) -> None:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()

        # config.yaml with legacy agents section
        config_yaml = cafe_dir / "config.yaml"
        config_yaml.write_text(
            "agents:\n  pm:\n    name: ConfigPM\n    cli: copilot\n"
            "  developer:\n    name: ConfigDev\n    cli: copilot\n"
            "  reviewer:\n    name: ConfigReviewer\n    cli: copilot\n"
        )

        # crew.yaml with different names
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text(
            "pm:\n  name: CrewPM\n  cli: claude\n"
            "developer:\n  name: CrewDev\n  cli: claude\n"
            "reviewer:\n  name: CrewReviewer\n  cli: claude\n"
        )

        config_manager = ConfigManager(str(cafe_dir))
        config_manager.load_config()
        from cafe.ui.cli_shared import setup_agents
        agent_manager = setup_agents(config_manager)

        assert "CrewPM" in agent_manager.agents
        assert "CrewDev" in agent_manager.agents
        assert "CrewReviewer" in agent_manager.agents
        assert "ConfigPM" not in agent_manager.agents

    def test_setup_agents_falls_back_to_config_agents_when_no_crew(self, tmp_path: Path) -> None:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()

        config_yaml = cafe_dir / "config.yaml"
        config_yaml.write_text(
            "agents:\n  pm:\n    name: LegacyPM\n    cli: copilot\n"
            "  developer:\n    name: LegacyDev\n    cli: copilot\n"
            "  reviewer:\n    name: LegacyReviewer\n    cli: copilot\n"
        )
        # no crew.yaml

        config_manager = ConfigManager(str(cafe_dir))
        config_manager.load_config()
        from cafe.ui.cli_shared import setup_agents
        agent_manager = setup_agents(config_manager)

        assert "LegacyPM" in agent_manager.agents
        assert "LegacyDev" in agent_manager.agents
        assert "LegacyReviewer" in agent_manager.agents
