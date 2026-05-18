"""Tests for refactored cafe init command (orchestrator: crew + setup)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


def _write_config(cafe_dir: Path, data: dict) -> None:
    (cafe_dir / "config.yaml").write_text(yaml.dump(data))


def _read_config(cafe_dir: Path) -> dict:
    return yaml.safe_load((cafe_dir / "config.yaml").read_text()) or {}


class TestInitOverwritePrompt:
    """When config already exists, init asks before overwriting."""

    def test_init_prompts_for_overwrite_if_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When .cafe/config.yaml exists and user declines, init exits cleanly."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, {"settings": {"auto_update": True}})
        monkeypatch.chdir(tmp_path)

        with patch("cafe.ui.cli.prompt_confirm", return_value=False):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "already exists" in result.stdout or "Configuration" in result.stdout

    def test_init_continues_when_overwrite_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When user confirms overwrite, init proceeds to crew setup."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, {"settings": {"auto_update": True}})
        monkeypatch.chdir(tmp_path)

        from cafe.utils.preset import PresetInfo

        preset_file = tmp_path / "preset_default.yaml"
        preset_file.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": "claude"},
            "developer": {"name": "David", "cli": "claude"},
            "reviewer": {"name": "Richard", "cli": "claude"},
        }))
        preset_info = PresetInfo(name="default", path=preset_file, source="built-in")

        with (
            patch("cafe.ui.cli.prompt_confirm", side_effect=[True, True]),
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_list", side_effect=["default", "medium"]),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0


class TestInitPresetFlag:
    """Non-interactive --preset flag."""

    def test_preset_applies_preset_and_writes_minimal_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset writes crew.yaml via PresetManager and creates minimal config.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("cafe.utils.preset.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["init", "--preset", "claude-opus"])

        assert result.exit_code == 0
        mock_pm.apply.assert_called_once_with("claude-opus", cafe_dir=Path(".cafe"))

        config_file = cafe_dir / "config.yaml"
        assert config_file.exists()
        cfg = yaml.safe_load(config_file.read_text()) or {}
        assert "settings" in cfg
        assert cfg["settings"].get("auto_update") is True

    def test_preset_unknown_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset with unknown name prints error and exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        from cafe.utils.preset import PresetNotFoundError

        with patch("cafe.utils.preset.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.side_effect = PresetNotFoundError("Preset 'unknown' not found.")

            result = runner.invoke(app, ["init", "--preset", "unknown"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "error" in result.stdout.lower()

    def test_preset_does_not_prompt_interactively(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset must not trigger any interactive prompts."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.utils.preset.PresetManager") as mock_pm_cls,
            patch("cafe.ui.cli.prompt_confirm") as mock_cli_confirm,
            patch("cafe.ui.commands.crew.prompt_list") as mock_crew_list,
            patch("cafe.ui.commands.crew.prompt_confirm") as mock_crew_confirm,
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["init", "--preset", "claude-opus"])

        assert result.exit_code == 0
        mock_cli_confirm.assert_not_called()
        mock_crew_list.assert_not_called()
        mock_crew_confirm.assert_not_called()

    def test_preset_skips_overwrite_prompt_on_fresh_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset on fresh dir does not ask overwrite and succeeds."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("cafe.utils.preset.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["init", "--preset", "default"])

        assert result.exit_code == 0


class TestInitInteractiveFlow:
    """Interactive init: crew set-primary flow → setup flow."""

    def _make_preset_info(self, name: str, tmp_path: Path) -> object:
        from cafe.utils.preset import PresetInfo
        preset_file = tmp_path / f"{name}.yaml"
        preset_file.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": "claude"},
            "developer": {"name": "David", "cli": "claude"},
            "reviewer": {"name": "Richard", "cli": "claude"},
        }))
        return PresetInfo(name=name, path=preset_file, source="built-in")

    def test_interactive_applies_preset_and_writes_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive flow: preset selected → crew.yaml written, config.yaml has settings."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_confirm") as mock_cli_confirm,
            patch("cafe.ui.cli.prompt_list", side_effect=["default", "medium"]),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]
            mock_pm.apply.return_value = None
            mock_cli_confirm.return_value = True

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0

        crew_file = cafe_dir / "crew.yaml"
        assert crew_file.exists()
        crew = yaml.safe_load(crew_file.read_text()) or {}
        for role in ["pm", "developer", "reviewer"]:
            assert crew[role]["clis"][0]["cli"] == "claude"

        config_file = cafe_dir / "config.yaml"
        assert config_file.exists()
        cfg = yaml.safe_load(config_file.read_text()) or {}
        assert "settings" in cfg

    def test_interactive_does_not_write_agents_key_to_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive init must not write agents: key to config.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_list", side_effect=["default", "medium"]),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        config_file = cafe_dir / "config.yaml"
        if config_file.exists():
            cfg = yaml.safe_load(config_file.read_text()) or {}
            assert "agents" not in cfg

    def test_interactive_keyboard_interrupt_exits_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C during interactive init exits cleanly."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.side_effect = KeyboardInterrupt

            result = runner.invoke(app, ["init"])

        assert result.exit_code in (0, 1)
        assert result.exception is None or isinstance(result.exception, SystemExit)
