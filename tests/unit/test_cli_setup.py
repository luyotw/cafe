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


def _prompt_messages(mock_prompt_list: MagicMock) -> list[str]:
    """Collect prompt message text from prompt_list mock calls."""
    return [call.kwargs.get("message", "") for call in mock_prompt_list.call_args_list]


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
    def test_setup_shows_role_menu_for_complete_config(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should use role menu first when existing config is complete."""
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
            mock_prompt_list.side_effect = [
                "pm",  # role menu
                "claude",
                "Roger: PM agent (system default)",
                "",
                "save",  # role menu
            ]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert mock_prompt_list.call_count == 5
        assert mock_prompt_text.call_count == 0
        assert _prompt_messages(mock_prompt_list)[0] == "Select role to update:"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_falls_back_to_full_flow_when_config_incomplete(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should use original full flow when role setup data is incomplete."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {"name": "Roger", "cli": "claude"},
                "developer": {"name": "David", "cli": "claude"},
                # reviewer missing -> incomplete
            },
            "settings": {"auto_update": True},
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text"),
        ):
            mock_prompt_list.side_effect = _build_prompt_list_side_effect(
                _default_roles_config()
            )
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert mock_prompt_list.call_count == 11
        assert _prompt_messages(mock_prompt_list)[0] == "Select CLI for PM:"

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

        with patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            mock_prompt_list.side_effect = ["save"]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        # Agents unchanged when user chooses Save directly
        assert saved_config["agents"]["pm"]["cli"] == "claude"
        assert saved_config["agents"]["developer"]["cli"] == "claude"
        assert saved_config["agents"]["reviewer"]["cli"] == "claude"

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
            ("Richard", "Reviewer agent", Path("agents/reviewer/Richard.md"), "system default"),
        ]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = [
                # PM
                "pm",
                "claude",
                "Roger: PM agent (system default)",
                CUSTOM_MODEL_SENTINEL,
                # Developer
                "developer",
                "claude",
                "David: Dev agent (system default)",
                CUSTOM_MODEL_SENTINEL,
                "",
                "",
                # Reviewer
                "reviewer",
                "claude",
                "Richard: Reviewer agent (system default)",
                CUSTOM_MODEL_SENTINEL,
                # Save
                "save",
            ]
            mock_prompt_text.side_effect = ["haiku", "opus", "sonnet"]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        assert saved_config["agents"]["pm"]["spec"]["model"] == "haiku"
        assert saved_config["agents"]["developer"]["plan"]["model"] == "opus"
        assert "develop" not in saved_config["agents"]["developer"] or "model" not in saved_config["agents"]["developer"].get("develop", {})
        assert saved_config["agents"]["reviewer"]["review"]["model"] == "sonnet"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    def test_setup_preserves_role_level_settings_when_editing_single_role(
        self,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup should preserve non-interactive role fields like backup/models."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text(yaml.dump({
            "agents": {
                "pm": {
                    "name": "Roger",
                    "cli": "claude",
                    "backup": {"clis": ["gemini"]},
                    "models": {"claude": "haiku"},
                    "spec": {"model": "haiku"},
                },
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "claude"},
            },
        }))

        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path("agents/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            mock_prompt_list.side_effect = [
                "pm",
                "claude",
                "Roger: PM agent (system default)",
                "",
                "save",
            ]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0

        with open(config_file) as f:
            saved_config = yaml.safe_load(f)

        assert saved_config["agents"]["pm"]["backup"] == {"clis": ["gemini"]}
        assert saved_config["agents"]["pm"]["models"] == {"claude": "haiku"}


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
                "pm",
                # PM: select CLI, then Back at agent, re-select CLI, then agent, then model
                "gemini",           # CLI (wrong choice)
                BACK_SENTINEL,      # Back from agent selection
                "claude",           # CLI (correct choice)
                agent,              # agent
                "",                 # spec model = default
                "save",
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
                "pm",
                # PM: CLI, agent (wrong), back from model, re-select agent, model
                "claude",
                agent_roger,         # agent (wrong)
                BACK_SENTINEL,       # Back from spec model → back to agent
                agent_alt,           # agent (correct)
                "",                  # spec model = default
                "save",
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
            mock_prompt_list.side_effect = ["save"]
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
            mock_prompt_list.side_effect = [
                "pm",
                "claude",
                "Roger: PM agent (system default)",
                "",
                "save",
            ]
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Agent configuration updated successfully" in result.stdout
        assert "PM:" in result.stdout
        assert "Developer:" in result.stdout
        assert "Reviewer:" in result.stdout
        assert "cafe config" in result.stdout
