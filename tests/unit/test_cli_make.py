"""測試 cafe make 指令功能."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.utils.config import ConfigManager


class TestCheckAgentCLIsAvailable:
    """測試 _check_agent_clis_available 函數."""

    def test_active_step_phase_chain_drives_transition_preflight(self, tmp_path: Path) -> None:
        """active step 有 explicit phase chain 時，只用該 chain 做 transition preflight."""
        phase_config = tmp_path / ".cafe" / "phases.yaml"
        phase_config.parent.mkdir()
        phase_config.write_text(
            """
develop:
  name: David
  clis:
    - cli: claude
      model: sonnet
    - cli: codex
      model: gpt-5.6-sol
""",
            encoding="utf-8",
        )

        config_manager = MagicMock(spec=ConfigManager)
        config_manager.config_dir = str(phase_config.parent)
        config_manager.get.side_effect = lambda key, default: {
            "agents.pm": {"name": "Roger", "cli": "gemini"},
            "agents.developer": {"name": "David", "cli": "copilot"},
            "agents.reviewer": {"name": "Richard", "cli": "cursor-agent"},
        }.get(key, default)

        from cafe.ui.cli import _check_agent_clis_available

        checked_clis: list[str] = []

        def which_side_effect(cli: str) -> str | None:
            checked_clis.append(cli)
            return f"/usr/local/bin/{cli}" if cli == "codex" else None

        with (
            patch("shutil.which", side_effect=which_side_effect),
            patch("cafe.ui.cli_shared.console.print") as mock_print,
        ):
            missing_clis = _check_agent_clis_available(
                config_manager,
                active_step="develop",
                phase_config_local_path=phase_config,
            )

        assert missing_clis == []
        assert checked_clis == ["claude", "codex"]
        warning_text = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert phase_config.as_posix() in warning_text
        assert "step=develop" in warning_text
        assert "field=clis" in warning_text

    def test_without_active_step_does_not_infer_role_configuration(self) -> None:
        from cafe.ui.cli import _check_agent_clis_available

        config_manager = MagicMock(spec=ConfigManager)
        with patch("shutil.which") as mock_which:
            assert _check_agent_clis_available(config_manager) == []
        mock_which.assert_not_called()
        config_manager.get.assert_not_called()


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

    def test_make_command_ignores_legacy_agent_cli_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.yaml agents 不再參與 make 的 execution preflight."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text(
            "agents:\n  developer:\n    name: David\n    cli: nonexistent\n",
            encoding="utf-8",
        )
        runner = CliRunner()

        with (
            patch("shutil.which") as mock_which,
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["make"])

        assert result.exit_code == 0, result.output
        mock_which.assert_not_called()
        workflow_calls = [
            call
            for call in mock_run.call_args_list
            if "cafe.ui.cli" in " ".join(str(part) for part in call.args[0])
        ]
        assert len(workflow_calls) == 1

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

    def test_make_command_propagates_workflow_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workflow 的非零結果應成為 make 的穩定失敗 outcome."""
        from typer.testing import CliRunner

        from cafe.ui.cli import app

        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "config.yaml").write_text("settings: {}\n", encoding="utf-8")
        runner = CliRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=7)
            result = runner.invoke(app, ["make"])

        assert result.exit_code == 7
        workflow_calls = [
            call
            for call in mock_run.call_args_list
            if "cafe.ui.cli" in " ".join(str(part) for part in call.args[0])
        ]
        assert len(workflow_calls) == 1
