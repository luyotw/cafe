"""Tests for cafe crew set-primary command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestCrewSetPrimaryPreset:
    """Tests for `cafe crew set-primary --preset` non-interactive mode."""

    def test_preset_applies_valid_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset with a valid name writes preset content to crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_content = {"pm": {"name": "Roger", "cli": "claude"}}
        preset_file = tmp_path / "my_preset.yaml"
        preset_file.write_text(yaml.dump(preset_content))

        with patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["crew", "set-primary", "--preset", "claude-opus"])

        assert result.exit_code == 0
        mock_pm.apply.assert_called_once_with("claude-opus", cafe_dir=Path(".cafe"))
        assert "claude-opus" in result.stdout

    def test_preset_not_found_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--preset with unknown name prints error and exits 1."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        from cafe.utils.preset import PresetNotFoundError

        with patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls:
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.apply.side_effect = PresetNotFoundError("Preset 'unknown' not found.")

            result = runner.invoke(app, ["crew", "set-primary", "--preset", "unknown"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "error" in result.stdout.lower()


class TestCrewSetPrimaryInteractive:
    """Tests for `cafe crew set-primary` interactive flow."""

    def _make_preset_info(self, name: str, tmp_path: Path, cli: str = "claude") -> object:
        from cafe.utils.preset import PresetInfo
        preset_file = tmp_path / f"{name}.yaml"
        preset_file.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": cli},
            "developer": {"name": "David", "cli": cli},
            "reviewer": {"name": "Richard", "cli": cli},
        }))
        return PresetInfo(name=name, path=preset_file, source="built-in")

    def test_interactive_applies_confirmed_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User selects preset, confirms → crew.yaml gets the preset content."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path, cli="claude")

        with (
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]") as mock_list,
            patch("cafe.ui.commands.crew.prompt_confirm", return_value=True),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
        mock_pm.apply.assert_called_once_with("default", cafe_dir=Path(".cafe"))

    def test_interactive_loops_back_when_not_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User rejects first time, confirms second time → apply called once."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        preset_info = self._make_preset_info("default", tmp_path, cli="claude")

        confirm_calls = [False, True]

        with (
            patch("cafe.ui.commands.crew.PresetManager") as mock_pm_cls,
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", return_value="default  [built-in]"),
            patch("cafe.ui.commands.crew.prompt_confirm", side_effect=confirm_calls),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list.return_value = [preset_info]
            mock_pm.apply.return_value = None

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
        assert mock_pm.apply.call_count == 1

    def test_interactive_keyboard_interrupt_exits_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C during interactive flow causes clean exit (exit code 0)."""
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

            result = runner.invoke(app, ["crew", "set-primary"])

        assert result.exit_code == 0
