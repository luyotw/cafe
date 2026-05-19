"""測試 cafe make 指令功能."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.utils.config import ConfigManager


class TestCheckAgentCLIsAvailable:
    """測試 _check_agent_clis_available 函數."""

    def test_all_clis_available(self) -> None:
        """測試當所有 CLI 工具都存在時, 檢查通過."""
        # Setup
        config_manager = MagicMock(spec=ConfigManager)
        config_manager.get.side_effect = lambda key, default: {
            "agents.pm": {"name": "Roger", "cli": "copilot"},
            "agents.developer": {"name": "David", "cli": "claude"},
            "agents.reviewer": {"name": "Richard", "cli": "gemini"},
        }.get(key, default)

        # Import the function
        from cafe.ui.cli import _check_agent_clis_available

        # Mock shutil.which to return paths for all CLIs
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/cli"

            # Execute
            missing_clis = _check_agent_clis_available(config_manager)

            # Verify
            assert missing_clis == []
            assert mock_which.call_count == 3

    def test_missing_cli_tools(self) -> None:
        """測試當缺少某個 CLI 工具時, 檢查失敗並回傳正確錯誤訊息."""
        # Setup
        config_manager = MagicMock(spec=ConfigManager)
        config_manager.get.side_effect = lambda key, default: {
            "agents.pm": {"name": "Roger", "cli": "copilot"},
            "agents.developer": {"name": "David", "cli": "claude"},
            "agents.reviewer": {"name": "Richard", "cli": "gemini"},
        }.get(key, default)

        # Import the function
        from cafe.ui.cli import _check_agent_clis_available

        # Mock shutil.which to return None for 'claude' and 'gemini'
        with patch("shutil.which") as mock_which:

            def which_side_effect(cli: str) -> str | None:
                return "/usr/local/bin/copilot" if cli == "copilot" else None

            mock_which.side_effect = which_side_effect

            # Execute
            missing_clis = _check_agent_clis_available(config_manager)

            # Verify
            assert len(missing_clis) == 2
            assert "claude" in missing_clis
            assert "gemini" in missing_clis

    def test_reads_correct_config_keys(self) -> None:
        """測試從 `.cafe/config.yaml` 正確讀取所有 agent  CLI 配置."""
        # Setup
        config_manager = MagicMock(spec=ConfigManager)
        config_manager.get.side_effect = lambda key, default: {
            "agents.pm": {"name": "Roger", "cli": "copilot"},
            "agents.developer": {"name": "David", "cli": "cursor-agent"},
            "agents.reviewer": {"name": "Richard", "cli": "gemini"},
        }.get(key, default)

        # Import the function
        from cafe.ui.cli import _check_agent_clis_available

        # Mock shutil.which
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/cli"

            # Execute
            _check_agent_clis_available(config_manager)

            # Verify config_manager.get was called with correct keys
            calls = [call[0][0] for call in config_manager.get.call_args_list]
            assert "agents.pm" in calls
            assert "agents.developer" in calls
            assert "agents.reviewer" in calls


class TestMakeCommand:
    """測試 cafe make 指令."""

    def test_make_accepts_multiple_add_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試 cafe make --add-dir 可重複指定並轉發給 workflow."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "docs").mkdir()
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text(
            "agents:\n"
            "  pm:\n    name: Roger\n    cli: claude\n"
            "  developer:\n    name: David\n    cli: claude\n"
            "  reviewer:\n    name: Richard\n    cli: claude\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["make", "--add-dir", "scripts", "--add-dir", "docs"])

        assert result.exit_code == 0, result.output
        cmd = mock_run.call_args[0][0]
        assert cmd.count("--add-dir") == 2
        assert cmd[cmd.index("--add-dir") + 1] == "scripts"
        second = cmd.index("--add-dir", cmd.index("--add-dir") + 1)
        assert cmd[second + 1] == "docs"

    def test_make_aborts_when_add_dir_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """不存在的 --add-dir 目錄應讓 make 在啟動 workflow 前中止。"""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text(
            "agents:\n"
            "  pm:\n    name: Roger\n    cli: claude\n"
            "  developer:\n    name: David\n    cli: claude\n"
            "  reviewer:\n    name: Richard\n    cli: claude\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            result = runner.invoke(app, ["make", "--add-dir", "nope"])

        assert result.exit_code == 1
        assert "nope" in result.stdout
        workflow_calls = [
            call for call in mock_run.call_args_list
            if "cafe.ui.cli" in " ".join(str(part) for part in call.args[0])
        ]
        assert workflow_calls == []

    def test_make_aborts_when_config_dir_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """config.yaml 的 allowed_directories 不存在時應中止。"""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text(
            "agents:\n"
            "  pm:\n    name: Roger\n    cli: claude\n"
            "  developer:\n    name: David\n    cli: claude\n"
            "  reviewer:\n    name: Richard\n    cli: claude\n"
            "allowed_directories:\n"
            "  - absent\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            result = runner.invoke(app, ["make"])

        assert result.exit_code == 1
        assert "absent" in result.stdout
        workflow_calls = [
            call for call in mock_run.call_args_list
            if "cafe.ui.cli" in " ".join(str(part) for part in call.args[0])
        ]
        assert workflow_calls == []

    def test_make_command_fails_when_clis_missing(self) -> None:
        """測試 cafe make 指令在環境檢查失敗時正確中止."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        runner = CliRunner()

        # Mock ConfigManager and shutil.which
        with (
            patch("cafe.ui.cli.ConfigManager") as mock_config_class,
            patch("shutil.which") as mock_which,
        ):
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default: {
                "agents.pm": {"name": "Roger", "cli": "copilot"},
                "agents.developer": {"name": "David", "cli": "claude"},
                "agents.reviewer": {"name": "Richard", "cli": "gemini"},
            }.get(key, default)
            mock_config_class.return_value = mock_config

            # Mock missing 'claude'
            mock_which.side_effect = lambda cli: (
                "/usr/local/bin/" + cli if cli != "claude" else None
            )

            # Execute
            result = runner.invoke(app, ["make"])

            # Verify
            assert result.exit_code == 1
            assert "claude" in result.stdout.lower()

    def test_make_command_executes_workflow_when_clis_available(self) -> None:
        """測試 cafe make 指令在環境檢查通過後執行 cafe workflow --execute."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        runner = CliRunner()

        # Mock ConfigManager, shutil.which, and subprocess.run
        with (
            patch("cafe.ui.cli.ConfigManager") as mock_config_class,
            patch("shutil.which") as mock_which,
            patch("subprocess.run") as mock_run,
        ):
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default: {
                "agents.pm": {"name": "Roger", "cli": "copilot"},
                "agents.developer": {"name": "David", "cli": "claude"},
                "agents.reviewer": {"name": "Richard", "cli": "gemini"},
            }.get(key, default)
            mock_config_class.return_value = mock_config

            # All CLIs available
            mock_which.return_value = "/usr/local/bin/cli"

            # Mock successful subprocess run
            mock_run.return_value = MagicMock(returncode=0)

            # Execute
            result = runner.invoke(app, ["make"])

            # Verify subprocess.run was called with correct arguments
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "cafe.ui.cli" in " ".join(call_args)
            assert "workflow" in call_args
            assert "--execute" in call_args

    def test_make_command_forwards_initial_user_input(self) -> None:
        """測試 cafe make 會把 --user-input 透傳給 workflow."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        runner = CliRunner()

        with (
            patch("cafe.ui.cli.ConfigManager") as mock_config_class,
            patch("shutil.which") as mock_which,
            patch("subprocess.run") as mock_run,
        ):
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default: {
                "agents.pm": {"name": "Roger", "cli": "copilot"},
                "agents.developer": {"name": "David", "cli": "claude"},
                "agents.reviewer": {"name": "Richard", "cli": "gemini"},
            }.get(key, default)
            mock_config_class.return_value = mock_config
            mock_which.return_value = "/usr/local/bin/cli"
            mock_run.return_value = MagicMock(returncode=0)

            result = runner.invoke(
                app,
                ["make", "--user-input", "As a user, I want to export CSV reports."],
            )

        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "--user-input" in call_args
        assert "As a user, I want to export CSV reports." in call_args

    def test_make_command_displays_correct_error_message(self) -> None:
        """測試 cafe make 指令顯示正確錯誤提示訊息."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        runner = CliRunner()

        # Mock ConfigManager and shutil.which
        with (
            patch("cafe.ui.cli.ConfigManager") as mock_config_class,
            patch("shutil.which") as mock_which,
        ):
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default: {
                "agents.pm": {"name": "Roger", "cli": "copilot"},
                "agents.developer": {"name": "David", "cli": "claude"},
                "agents.reviewer": {"name": "Richard", "cli": "nonexistent"},
            }.get(key, default)
            mock_config_class.return_value = mock_config

            # Mock missing tools
            mock_which.side_effect = lambda cli: (
                None if cli in ["claude", "nonexistent"] else "/usr/local/bin/" + cli
            )

            # Execute
            result = runner.invoke(app, ["make"])

            # Verify error message content
            assert result.exit_code == 1
            assert "Error" in result.stdout or "error" in result.stdout.lower()
            # Should list missing tools
            assert "claude" in result.stdout
            assert "nonexistent" in result.stdout
