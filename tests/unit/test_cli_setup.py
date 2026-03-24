"""Tests for cafe setup command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app, CUSTOM_MODEL_SENTINEL

runner = CliRunner()


def _build_prompt_list_side_effect(roles_config):
    """Build prompt_list side_effect for the new step-based agent setup flow.

    Args:
        roles_config: List of (cli, agent_display, model_selections) tuples.
            model_selections is a list of values for each phase's prompt_list call.
            Use "" for default, CUSTOM_MODEL_SENTINEL for custom model.

    Returns:
        List of return values for prompt_list.side_effect
    """
    side_effect = []
    for cli, agent_display, model_selections in roles_config:
        side_effect.append(cli)
        side_effect.append(agent_display)
        side_effect.extend(model_selections)
    return side_effect


def _default_roles_config(cli="claude", agent="Roger: PM agent (system default)"):
    """Default config: all 3 roles use same CLI/agent, all models default."""
    return [
        (cli, agent, [""]),              # PM: spec
        (cli, agent, ["", "", ""]),      # Developer: plan, develop, pr
        (cli, agent, [""]),              # Reviewer: review
    ]


class TestSetupRequiresInit:
    """Test that setup requires cafe init to have been run first."""

    def test_setup_exits_if_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """setup should fail when .cafe/config.yaml does not exist."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "cafe init" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    def test_setup_exits_if_no_clis_available(
        self,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should fail when no CLI agents are installed."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({"agents": {"pm": {"name": "Roger", "cli": "claude"}}}))

        mock_which.return_value = None
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "No supported AI agents found" in result.stdout


class TestSetupInteractiveFlow:
    """Test the interactive agent configuration flow in setup."""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_prompts_for_all_three_roles(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should prompt CLI, agent, and model selection for all three roles."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
            "settings": {"auto_update": True},
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _build_prompt_list_side_effect(
                _default_roles_config()
            )
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        # PM: 3 (cli + agent + spec) + Developer: 5 (cli + agent + plan + develop + pr) + Reviewer: 3 = 11
        assert mock_prompt_list.call_count == 11
        # No custom models = no prompt_text calls
        assert mock_prompt_text.call_count == 0

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_handles_keyboard_interrupt(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should handle Ctrl+C gracefully."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            mock_prompt_list.side_effect = KeyboardInterrupt()
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "cancelled" in result.stdout


class TestSetupPreservesExistingConfig:
    """Test that setup only updates agents section, preserving other settings."""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_preserves_non_agent_settings(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should preserve settings, auto, python_bin, etc."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        original_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
            "settings": {"auto_update": True},
            "auto": {"max_review_iterations": 3},
            "python_bin": "python3.11",
        }
        config_file.write_text(yaml.dump(original_config))

        mock_which.return_value = "/usr/bin/gemini"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _build_prompt_list_side_effect(
                _default_roles_config(cli="gemini")
            )
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        # Agents should be updated
        assert saved_config["agents"]["pm"]["cli"] == "gemini"
        assert saved_config["agents"]["developer"]["cli"] == "gemini"
        assert saved_config["agents"]["reviewer"]["cli"] == "gemini"

        # Other settings should be preserved
        assert saved_config["settings"]["auto_update"] is True
        assert saved_config["auto"]["max_review_iterations"] == 3
        assert saved_config["python_bin"] == "python3.11"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_saves_phase_specific_models(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should correctly save phase-specific model overrides."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [
            ("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default"),
            ("David", "Dev agent", Path("agents/developer/David.md"), "system default"),
        ]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            agent = "Roger: PM agent (system default)"
            mock_prompt_list.side_effect = _build_prompt_list_side_effect([
                ("claude", agent, [CUSTOM_MODEL_SENTINEL]),                          # PM: spec=custom
                ("claude", agent, [CUSTOM_MODEL_SENTINEL, "", ""]),                  # Dev: plan=custom, develop=default, pr=default
                ("claude", agent, [CUSTOM_MODEL_SENTINEL]),                          # Reviewer: review=custom
            ])
            mock_prompt_text.side_effect = ["haiku", "opus", "sonnet"]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        assert saved_config["agents"]["pm"]["spec"]["model"] == "haiku"
        assert saved_config["agents"]["developer"]["plan"]["model"] == "opus"
        assert "develop" not in saved_config["agents"]["developer"] or "model" not in saved_config["agents"]["developer"].get("develop", {})
        assert saved_config["agents"]["reviewer"]["review"]["model"] == "sonnet"


class TestSetupBackNavigation:
    """Test back navigation within role configuration."""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_back_from_agent_to_cli(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User can go back from agent selection to CLI selection."""
        from cafe.ui.cli import BACK_SENTINEL

        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        agent = "Roger: PM agent (system default)"
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = [
                # PM: select CLI, then Back at agent, re-select CLI, then agent, then model
                "gemini",           # CLI (wrong choice)
                BACK_SENTINEL,      # Back from agent selection
                "claude",           # CLI (correct choice)
                agent,              # agent
                "",                 # spec model = default
                # Developer: normal flow
                "claude", agent, "", "", "",
                # Reviewer: normal flow
                "claude", agent, "",
            ]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        # PM should have the corrected CLI
        assert saved_config["agents"]["pm"]["cli"] == "claude"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_back_from_model_to_agent(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User can go back from model selection to agent selection."""
        from cafe.ui.cli import BACK_SENTINEL

        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [
            ("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default"),
            ("Alt", "Alt agent", Path("agents/pm/Alt.md"), "custom"),
        ]
        monkeypatch.chdir(tmp_path)

        agent_roger = "Roger: PM agent (system default)"
        agent_alt = "Alt: Alt agent (custom)"
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = [
                # PM: CLI, agent (wrong), back from model, re-select agent, model
                "claude",
                agent_roger,         # agent (wrong)
                BACK_SENTINEL,       # Back from spec model → back to agent
                agent_alt,           # agent (correct)
                "",                  # spec model = default
                # Developer + Reviewer: normal flow
                "claude", agent_roger, "", "", "",
                "claude", agent_roger, "",
            ]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        assert saved_config["agents"]["pm"]["name"] == "Alt"


class TestSetupDisplayOutput:
    """Test setup command display output."""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_displays_current_config_before_prompts(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should display current agent config before interactive prompts."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "gemini"},
                "developer": {"name": "David", "cli": "claude", "plan": {"model": "opus"}},
                "reviewer": {"name": "Richard", "cli": "gemini"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _build_prompt_list_side_effect(
                _default_roles_config()
            )
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Current agent configuration" in result.stdout
        assert "gemini" in result.stdout
        assert "opus" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_displays_success_and_summary(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should display success message and agent summary."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _build_prompt_list_side_effect(
                _default_roles_config()
            )
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Agent configuration updated successfully" in result.stdout
        assert "PM:" in result.stdout
        assert "Developer:" in result.stdout
        assert "Reviewer:" in result.stdout
        assert "cafe config" in result.stdout
