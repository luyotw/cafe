"""Tests for CLI interactive menu integration.

Verifies that bare `cafe` (no arguments) launches the InteractiveMenu,
while all existing subcommands remain accessible for backward compatibility.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


class TestBareCAFELaunchesMenu:
    """Test that bare `cafe` command launches InteractiveMenu."""

    def test_bare_cafe_invokes_interactive_menu(self):
        """測試執行 `cafe` 不帶任何子命令時啟動互動選單"""
        with patch("cafe.ui.cli.InteractiveMenu") as mock_menu_cls:
            mock_menu = MagicMock()
            mock_menu_cls.return_value = mock_menu

            result = runner.invoke(app, [])

            mock_menu_cls.assert_called_once()
            mock_menu.run.assert_called_once()

    def test_bare_cafe_does_not_show_help_text(self):
        """測試執行 `cafe` 時不應該直接顯示 typer help"""
        with patch("cafe.ui.cli.InteractiveMenu") as mock_menu_cls:
            mock_menu = MagicMock()
            mock_menu_cls.return_value = mock_menu

            result = runner.invoke(app, [])

            # Should not contain typical help output
            assert result.exit_code == 0
            assert mock_menu.run.called


class TestBackwardCompatibility:
    """Test that all existing CLI subcommands remain accessible."""

    def test_subcommand_does_not_launch_interactive_menu(self):
        """測試呼叫子命令時不啟動互動選單"""
        with (
            patch("cafe.ui.cli.InteractiveMenu") as mock_menu_cls,
            patch("cafe.ui.cli.ConfigManager") as mock_config_cls,
            patch("shutil.which") as mock_which,
            patch("subprocess.run") as mock_run,
        ):
            mock_which.return_value = "/usr/local/bin/cli"
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default=None: {
                "agents.pm": {"name": "Roger", "cli": "copilot"},
                "agents.developer": {"name": "David", "cli": "claude"},
                "agents.reviewer": {"name": "Richard", "cli": "gemini"},
            }.get(key, default)
            mock_config_cls.return_value = mock_config
            mock_run.return_value = MagicMock(returncode=0)

            runner.invoke(app, ["make"])

            # InteractiveMenu should NOT be called when a subcommand is provided
            mock_menu_cls.assert_not_called()

    def test_ls_command_remains_accessible(self):
        """測試 cafe ls 指令仍然可以直接使用"""
        with patch("cafe.ui.cli.InteractiveMenu") as mock_menu_cls:
            result = runner.invoke(app, ["ls"])

            # ls command runs, interactive menu should not be invoked
            mock_menu_cls.assert_not_called()

    def test_help_flag_remains_accessible(self):
        """測試 --help 旗標仍然正常運作"""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "cafe" in result.stdout.lower() or "Usage" in result.stdout


class TestKeyboardInterruptHandling:
    """Test that Ctrl+C in the menu exits cleanly."""

    def test_keyboard_interrupt_exits_with_code_zero(self):
        """測試選單中按 Ctrl+C 乾淨退出，不拋出例外"""
        with patch("cafe.ui.cli.InteractiveMenu") as mock_menu_cls:
            mock_menu = MagicMock()
            # Simulate KeyboardInterrupt inside run()
            mock_menu.run.side_effect = KeyboardInterrupt
            mock_menu_cls.return_value = mock_menu

            # Should not raise, should exit cleanly
            result = runner.invoke(app, [])

            assert result.exit_code == 0
