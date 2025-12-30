"""Tests for CLI config command."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from click.exceptions import Exit
import yaml

from cafe.ui.commands.config_commands import config


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager."""
    with patch("cafe.ui.commands.config_commands.ConfigManager") as mock:
        instance = MagicMock()
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_console():
    """Create a mock console."""
    with patch("cafe.ui.commands.config_commands.console") as mock:
        yield mock


class TestConfigShowAll:
    """Test config command without arguments (show all)."""

    def test_show_all_config(self, mock_config_manager, mock_console):
        """Test showing all configuration."""
        mock_config = {"workflow_mode": "github", "agents": {"pm": {"cli": "gemini"}}}
        mock_config_manager.load_config.return_value = mock_config

        config(action=None, key=None, value=None)

        mock_config_manager.load_config.assert_called_once()
        mock_console.print.assert_any_call("[bold cyan]Current Configuration:[/bold cyan]")


class TestConfigSet:
    """Test config set command."""

    def test_set_config_value(self, mock_config_manager, mock_console):
        """Test setting a config value."""
        config(action="set", key="pm", value="claude")

        mock_config_manager.set.assert_called_once_with("pm", "claude")
        mock_console.print.assert_called_with("[green]✓ Set pm = claude[/green]")

    def test_set_without_key_exits(self, mock_config_manager, mock_console):
        """Test set without key exits with error."""
        with pytest.raises(Exit):
            config(action="set", key=None, value="value")

        mock_console.print.assert_any_call(
            "[red]Error: 'set' requires both key and value[/red]"
        )

    def test_set_without_value_exits(self, mock_config_manager, mock_console):
        """Test set without value exits with error."""
        with pytest.raises(Exit):
            config(action="set", key="pm", value=None)

        mock_console.print.assert_any_call(
            "[red]Error: 'set' requires both key and value[/red]"
        )


class TestConfigGet:
    """Test config get command."""

    def test_get_existing_value(self, mock_config_manager, mock_console):
        """Test getting an existing config value."""
        mock_config_manager.get.return_value = "gemini"

        config(action="get", key="pm", value=None)

        mock_config_manager.get.assert_called_once_with("pm")
        # Should print the value
        assert mock_console.print.called

    def test_get_nonexistent_value(self, mock_config_manager, mock_console):
        """Test getting a nonexistent value."""
        mock_config_manager.get.return_value = None

        config(action="get", key="nonexistent", value=None)

        mock_console.print.assert_any_call("[yellow]Key not found: nonexistent[/yellow]")

    def test_get_without_key_exits(self, mock_config_manager, mock_console):
        """Test get without key exits with error."""
        with pytest.raises(Exit):
            config(action="get", key=None, value=None)

        mock_console.print.assert_any_call("[red]Error: 'get' requires a key[/red]")


class TestConfigEdit:
    """Test config edit command."""

    def test_edit_opens_editor(self, mock_config_manager, mock_console, tmp_path):
        """Test edit command opens editor."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: value")
        mock_config_manager.config_file = config_file

        with patch("cafe.ui.commands.config_commands.subprocess.run") as mock_run:
            with patch("cafe.ui.commands.config_commands.os.environ.get") as mock_env:
                mock_env.return_value = "vim"
                mock_run.return_value = MagicMock()

                config(action="edit", key=None, value=None)

                mock_run.assert_called_once_with(["vim", str(config_file)], check=True)
                mock_console.print.assert_called_with(
                    f"[green]✓ Config file edited: {config_file}[/green]"
                )

    def test_edit_with_custom_editor(self, mock_config_manager, tmp_path):
        """Test edit uses EDITOR environment variable."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: value")
        mock_config_manager.config_file = config_file

        with patch("cafe.ui.commands.config_commands.subprocess.run") as mock_run:
            with patch("cafe.ui.commands.config_commands.os.environ.get") as mock_env:
                mock_env.return_value = "nano"

                config(action="edit", key=None, value=None)

                mock_run.assert_called_once_with(["nano", str(config_file)], check=True)

    def test_edit_nonexistent_file_exits(self, mock_config_manager, mock_console, tmp_path):
        """Test edit with nonexistent config file exits."""
        config_file = tmp_path / "nonexistent.yaml"
        mock_config_manager.config_file = config_file

        with pytest.raises(Exit):
            config(action="edit", key=None, value=None)

        mock_console.print.assert_any_call(
            "[red]Error: Configuration file not found.[/red]"
        )

    def test_edit_editor_not_found(self, mock_config_manager, mock_console, tmp_path):
        """Test edit with nonexistent editor exits."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: value")
        mock_config_manager.config_file = config_file

        with patch("cafe.ui.commands.config_commands.subprocess.run") as mock_run:
            with patch("cafe.ui.commands.config_commands.os.environ.get") as mock_env:
                mock_env.return_value = "nonexistent_editor"
                mock_run.side_effect = FileNotFoundError()

                with pytest.raises(Exit):
                    config(action="edit", key=None, value=None)

                mock_console.print.assert_any_call(
                    "[red]Error: Editor 'nonexistent_editor' not found[/red]"
                )


class TestConfigReset:
    """Test config reset command."""

    def test_reset_confirmed(self, mock_config_manager, mock_console):
        """Test reset with confirmation."""
        with patch("cafe.ui.commands.config_commands.prompt_confirm") as mock_confirm:
            mock_confirm.return_value = True

            config(action="reset", key=None, value=None)

            mock_config_manager.reset.assert_called_once()
            mock_console.print.assert_called_with(
                "[green]✓ Configuration reset to defaults[/green]"
            )

    def test_reset_cancelled(self, mock_config_manager, mock_console):
        """Test reset cancelled."""
        with patch("cafe.ui.commands.config_commands.prompt_confirm") as mock_confirm:
            mock_confirm.return_value = False

            config(action="reset", key=None, value=None)

            mock_config_manager.reset.assert_not_called()
            mock_console.print.assert_called_with("[dim]Cancelled[/dim]")

    def test_reset_keyboard_interrupt(self, mock_config_manager, mock_console):
        """Test reset with KeyboardInterrupt."""
        with patch("cafe.ui.commands.config_commands.prompt_confirm") as mock_confirm:
            mock_confirm.side_effect = KeyboardInterrupt()

            with pytest.raises(Exit):
                config(action="reset", key=None, value=None)

            mock_console.print.assert_called_with("\n[dim]Cancelled[/dim]")


class TestConfigBackwardCompatibility:
    """Test backward compatibility for direct key access."""

    def test_action_as_key(self, mock_config_manager, mock_console):
        """Test using action parameter as key (backward compatibility)."""
        mock_config_manager.get.return_value = "gemini"

        config(action="pm", key=None, value=None)

        mock_config_manager.get.assert_called_once_with("pm")
        assert mock_console.print.called

    def test_action_as_nonexistent_key(self, mock_config_manager, mock_console):
        """Test using action as nonexistent key."""
        mock_config_manager.get.return_value = None

        config(action="unknown_key", key=None, value=None)

        mock_console.print.assert_any_call("[yellow]Key not found: unknown_key[/yellow]")
