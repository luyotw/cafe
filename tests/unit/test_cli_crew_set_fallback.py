"""Tests for cafe crew set-fallback command."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()

_CREW_NEW = {
    "pm": {
        "name": "Roger",
        "clis": [{"cli": "claude", "model": "sonnet"}],
    },
    "developer": {
        "name": "David",
        "clis": [{"cli": "claude", "model": "opus"}],
    },
    "reviewer": {
        "name": "Richard",
        "clis": [{"cli": "claude"}],
    },
}


def _write_crew(cafe_dir: Path, data: dict) -> None:
    (cafe_dir / "crew.yaml").write_text(yaml.dump(data))


def _read_crew(cafe_dir: Path) -> dict:
    return yaml.safe_load((cafe_dir / "crew.yaml").read_text()) or {}


class TestCrewSetFallbackNonInteractive:
    """Tests for --role + --add / --remove flags."""

    def test_add_cli_with_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--add codex,gpt-5.5 appends entry with model to developer chain."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-fallback", "--role", "developer", "--add", "codex,gpt-5.5"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        clis = crew["developer"]["clis"]
        assert any(e["cli"] == "codex" and e.get("model") == "gpt-5.5" for e in clis)

    def test_add_cli_without_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--add gemini appends entry without model to pm chain."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-fallback", "--role", "pm", "--add", "gemini"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        clis = crew["pm"]["clis"]
        assert any(e["cli"] == "gemini" for e in clis)

    def test_remove_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--remove claude removes claude entry from developer chain."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        data = {
            "developer": {
                "name": "David",
                "clis": [{"cli": "claude"}, {"cli": "gemini"}],
            }
        }
        _write_crew(cafe_dir, data)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-fallback", "--role", "developer", "--remove", "gemini"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        clis = crew["developer"]["clis"]
        assert not any(e["cli"] == "gemini" for e in clis)
        assert any(e["cli"] == "claude" for e in clis)

    def test_add_duplicate_cli_exits_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--add with CLI already in chain prints error and does not modify crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        original = (cafe_dir / "crew.yaml").read_text()
        result = runner.invoke(app, ["crew", "set-fallback", "--role", "developer", "--add", "claude"])

        assert result.exit_code == 1
        assert (cafe_dir / "crew.yaml").read_text() == original

    def test_remove_nonexistent_cli_exits_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--remove with CLI not in chain prints error and does not modify crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        original = (cafe_dir / "crew.yaml").read_text()
        result = runner.invoke(app, ["crew", "set-fallback", "--role", "developer", "--remove", "gemini"])

        assert result.exit_code == 1
        assert (cafe_dir / "crew.yaml").read_text() == original

    def test_missing_role_with_flags_exits_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--add without --role exits with error message."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-fallback", "--add", "gemini"])

        assert result.exit_code == 1
        assert "role" in result.stdout.lower() or "required" in result.stdout.lower()

    def test_old_format_normalizes_and_adds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Old-format crew.yaml (cli/backup) is normalized when adding fallback."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        old_crew = {
            "developer": {
                "name": "David",
                "cli": "claude",
                "model": "opus",
            }
        }
        _write_crew(cafe_dir, old_crew)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "set-fallback", "--role", "developer", "--add", "gemini"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        clis = crew["developer"]["clis"]
        assert any(e["cli"] == "claude" for e in clis)
        assert any(e["cli"] == "gemini" for e in clis)


class TestCrewSetFallbackInteractive:
    """Tests for interactive set-fallback flow."""

    def test_interactive_add_appends_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive Add appends new CLI entry to chain."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude", "gemini"]),
            patch("cafe.ui.commands.crew.prompt_list", side_effect=["developer", "Add entry", "gemini", "Done"]),
            patch("cafe.ui.commands.crew.prompt_text", return_value=""),
            patch("cafe.ui.commands.crew.prompt_confirm"),
        ):
            result = runner.invoke(app, ["crew", "set-fallback"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        assert any(e["cli"] == "gemini" for e in crew["developer"]["clis"])

    def test_interactive_remove_pops_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive Remove removes the selected fallback entry."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        data = {
            "developer": {
                "clis": [{"cli": "claude"}, {"cli": "gemini"}],
            }
        }
        _write_crew(cafe_dir, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude", "gemini"]),
            patch("cafe.ui.commands.crew.prompt_list", side_effect=["developer", "Remove entry", "2. gemini", "Done"]),
            patch("cafe.ui.commands.crew.prompt_text", return_value=""),
            patch("cafe.ui.commands.crew.prompt_confirm"),
        ):
            result = runner.invoke(app, ["crew", "set-fallback"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        assert not any(e["cli"] == "gemini" for e in crew["developer"]["clis"])

    def test_interactive_reorder_move_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reorder → Move Up swaps entry with its predecessor."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        data = {
            "developer": {
                "clis": [{"cli": "claude"}, {"cli": "gemini"}],
            }
        }
        _write_crew(cafe_dir, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude", "gemini"]),
            patch("cafe.ui.commands.crew.prompt_list", side_effect=["developer", "Reorder", "2. gemini", "Move Up", "Done"]),
            patch("cafe.ui.commands.crew.prompt_text", return_value=""),
            patch("cafe.ui.commands.crew.prompt_confirm"),
        ):
            result = runner.invoke(app, ["crew", "set-fallback"])

        assert result.exit_code == 0
        crew = _read_crew(cafe_dir)
        clis = crew["developer"]["clis"]
        assert clis[0]["cli"] == "gemini"
        assert clis[1]["cli"] == "claude"

    def test_interactive_reorder_first_entry_no_move_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first entry in Reorder has no Move Up option."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        data = {
            "developer": {
                "clis": [{"cli": "claude"}, {"cli": "gemini"}],
            }
        }
        _write_crew(cafe_dir, data)
        monkeypatch.chdir(tmp_path)

        captured_reorder_choices = []

        def fake_prompt_list(message="", choices=None, **kwargs):
            if "direction" in message.lower() or "move" in message.lower():
                captured_reorder_choices.extend(choices or [])
                return "Move Down"
            side = ["developer", "Reorder", "1. claude", "Done"]
            return side.pop(0) if side else "Done"

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude", "gemini"]),
            patch("cafe.ui.commands.crew.prompt_list", side_effect=["developer", "Reorder", "1. claude", "Move Down", "Done"]),
            patch("cafe.ui.commands.crew.prompt_text", return_value=""),
            patch("cafe.ui.commands.crew.prompt_confirm"),
        ):
            result = runner.invoke(app, ["crew", "set-fallback"])

        assert result.exit_code == 0

    def test_interactive_done_saves_crew_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Choosing Done immediately writes crew.yaml without changes."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        _write_crew(cafe_dir, _CREW_NEW)
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.init_helpers.check_available_clis", return_value=["claude"]),
            patch("cafe.ui.commands.crew.prompt_list", side_effect=["developer", "Done"]),
            patch("cafe.ui.commands.crew.prompt_text", return_value=""),
            patch("cafe.ui.commands.crew.prompt_confirm"),
        ):
            result = runner.invoke(app, ["crew", "set-fallback"])

        assert result.exit_code == 0
        # crew.yaml should still exist after Done
        assert (cafe_dir / "crew.yaml").exists()
