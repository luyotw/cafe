"""Tests for refactored cafe setup command (settings-only, no agents)."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()

_MINIMAL_CONFIG = {"settings": {"auto_update": True}}


def _write_config(cafe_dir: Path, data: dict) -> None:
    (cafe_dir / "config.yaml").write_text(yaml.dump(data))


def _read_config(cafe_dir: Path) -> dict:
    return yaml.safe_load((cafe_dir / "config.yaml").read_text()) or {}


class TestSetupRequiresInit:
    """setup requires cafe init to have been run first."""

    def test_setup_exits_if_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """setup fails when .cafe/config.yaml does not exist."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".cafe").mkdir()

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "cafe init" in result.stdout


class TestSetupNonInteractiveFlags:
    """Non-interactive flags write settings without prompts."""

    def test_playbook_flag_updates_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--playbook writes playbook to settings in config.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--playbook", "standard"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["settings"]["playbook"] == "standard"

    def test_auto_update_false_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--no-auto-update writes auto_update=False."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--no-auto-update"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["settings"]["auto_update"] is False

    def test_rigor_flag_updates_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--rigor writes rigor level to settings."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--rigor", "high"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["settings"]["rigor"] == "high"

    def test_multiple_flags_combined(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple flags can be combined in one invocation."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--playbook", "standard", "--rigor", "medium"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["settings"]["playbook"] == "standard"
        assert cfg["settings"]["rigor"] == "medium"

    def test_flags_do_not_touch_crew_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-interactive flags must not create or modify crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--playbook", "standard"])

        assert result.exit_code == 0
        assert not (cafe_dir / "crew.yaml").exists()

    def test_flags_preserve_other_config_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-interactive flags preserve unrelated top-level config keys."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        original = {
            "settings": {"auto_update": True},
            "integration": {"custom_timeout": 7},
            "python_bin": "python3.11",
        }
        _write_config(cafe_dir, original)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--playbook", "standard"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["integration"]["custom_timeout"] == 7
        assert cfg["python_bin"] == "python3.11"

    def test_invalid_rigor_exits_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--rigor with an invalid value exits 1 with an error message."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["setup", "--rigor", "extreme"])

        assert result.exit_code == 1


class TestSetupInteractiveFlow:
    """Interactive mode prompts for settings and writes to config.yaml."""

    def test_interactive_updates_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive flow updates settings based on prompts."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_confirm", return_value=False),  # auto_update -> False
            patch("cafe.ui.cli.prompt_list", side_effect=["standard", "medium"]),  # playbook, rigor
        ):
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert cfg["settings"]["auto_update"] is False

    def test_interactive_does_not_touch_crew_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive flow must not create or modify crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_list", side_effect=["default", "medium"]),
        ):
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert not (cafe_dir / "crew.yaml").exists()

    def test_interactive_keyboard_interrupt_exits_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C during interactive setup exits cleanly (exit code 0 or 1)."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_config(cafe_dir, _MINIMAL_CONFIG)
        monkeypatch.chdir(tmp_path)

        with patch("cafe.ui.cli.prompt_confirm", side_effect=KeyboardInterrupt):
            result = runner.invoke(app, ["setup"])

        # Should not crash with uncaught exception
        assert result.exit_code in (0, 1)
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_interactive_preserves_existing_agents_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive setup removes retired agent execution settings."""

        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        original = {
            "settings": {"auto_update": True},
            "agents": {"pm": {"name": "Roger", "cli": "claude"}},
        }
        _write_config(cafe_dir, original)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_confirm", return_value=True),
            patch("cafe.ui.cli.prompt_list", side_effect=["default", "medium"]),
        ):
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        cfg = _read_config(cafe_dir)
        assert "agents" not in cfg
        assert not (cafe_dir / "crew.yaml").exists()
