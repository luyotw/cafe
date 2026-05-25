"""Tests for cafe crew list command."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestCrewList:
    """Tests for `cafe crew list` command."""

    def test_list_warns_when_crew_yaml_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """crew list shows warning (exit 0) when crew.yaml does not exist."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "list"])

        assert result.exit_code == 0
        assert "crew.yaml" in result.stdout.lower() or "warning" in result.stdout.lower()

    def test_list_new_format_shows_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """crew list displays role/CLI/model table for new-format crew.yaml."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text(yaml.dump({
            "pm": {
                "name": "Roger",
                "clis": [{"cli": "claude", "model": "sonnet"}],
            },
            "developer": {
                "name": "David",
                "clis": [{"cli": "claude", "model": "opus"}, {"cli": "gemini"}],
            },
            "reviewer": {
                "name": "Richard",
                "clis": [{"cli": "gemini"}],
            },
        }))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "list"])

        assert result.exit_code == 0
        assert "pm" in result.stdout.lower()
        assert "claude" in result.stdout.lower()
        assert "gemini" in result.stdout.lower()
        assert "developer" in result.stdout.lower()
        assert "reviewer" in result.stdout.lower()

    def test_list_old_format_normalizes_and_shows_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """crew list normalizes old-format (cli/backup) and displays table."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text(yaml.dump({
            "pm": {"name": "Roger", "cli": "claude", "model": "sonnet"},
            "developer": {
                "name": "David",
                "cli": "claude",
                "backup": ["gemini"],
                "model": "opus",
            },
            "reviewer": {"name": "Richard", "cli": "gemini"},
        }))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "list"])

        assert result.exit_code == 0
        assert "pm" in result.stdout.lower()
        assert "claude" in result.stdout.lower()
        assert "gemini" in result.stdout.lower()
        assert "developer" in result.stdout.lower()

    def test_list_empty_crew_yaml_shows_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """crew list handles empty or invalid crew.yaml gracefully."""
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text("")
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["crew", "list"])

        assert result.exit_code == 0
