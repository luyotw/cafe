"""Tests for cafe preset CLI commands."""

import pytest
from pathlib import Path
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


class TestPresetList:
    """Tests for cafe preset list."""

    def test_preset_list_shows_built_in_sources(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".cafe").mkdir()
        result = runner.invoke(app, ["preset", "list"])
        assert result.exit_code == 0
        assert "claude" in result.output
        assert "codex" in result.output
        assert "gemini" in result.output
        assert "built-in" in result.output

    def test_preset_list_includes_project_presets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        presets_dir = tmp_path / ".cafe" / "presets"
        presets_dir.mkdir(parents=True)
        (presets_dir / "my-project.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        result = runner.invoke(app, ["preset", "list"])
        assert result.exit_code == 0
        assert "my-project" in result.output
        assert "project" in result.output

    def test_preset_list_includes_global_presets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".cafe").mkdir()
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        global_presets = home_dir / ".cafe" / "presets"
        global_presets.mkdir(parents=True)
        (global_presets / "my-global.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        monkeypatch.setattr(Path, "home", lambda: home_dir)
        result = runner.invoke(app, ["preset", "list"])
        assert result.exit_code == 0
        assert "my-global" in result.output
        assert "global" in result.output


class TestPresetSave:
    """Tests for cafe preset save."""

    def test_preset_save_writes_global_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home_dir)
        result = runner.invoke(app, ["preset", "save", "my-team"])
        assert result.exit_code == 0
        assert (home_dir / ".cafe" / "presets" / "my-team.yaml").exists()

    def test_preset_save_local_flag_writes_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        result = runner.invoke(app, ["preset", "save", "my-local", "--local"])
        assert result.exit_code == 0
        assert (cafe_dir / "presets" / "my-local.yaml").exists()

    def test_preset_save_existing_prompts_confirm_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        # Pre-create the target
        project_presets = cafe_dir / "presets"
        project_presets.mkdir()
        (project_presets / "existing.yaml").write_text("# old\n")
        # User confirms overwrite via stdin "y"
        result = runner.invoke(app, ["preset", "save", "existing", "--local"], input="y\n")
        assert result.exit_code == 0
        assert (project_presets / "existing.yaml").read_text() != "# old\n"

    def test_preset_save_existing_prompts_confirm_no(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        project_presets = cafe_dir / "presets"
        project_presets.mkdir()
        (project_presets / "existing.yaml").write_text("# old\n")
        result = runner.invoke(app, ["preset", "save", "existing", "--local"], input="n\n")
        # Should abort, old content preserved
        assert (project_presets / "existing.yaml").read_text() == "# old\n"
