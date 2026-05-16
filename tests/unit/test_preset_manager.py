"""Tests for PresetManager."""

import pytest
from pathlib import Path

from cafe.utils.preset import PresetManager, PresetInfo


class TestPresetManagerList:
    """Test PresetManager.list() and find()."""

    def test_list_returns_built_in_presets(self) -> None:
        manager = PresetManager()
        presets = manager.list()
        names = {p.name for p in presets}
        assert "default" in names
        assert "claude-opus" in names
        assert "gemini-team" in names
        built_in = [p for p in presets if p.source == "built-in"]
        built_in_names = {p.name for p in built_in}
        assert {"default", "claude-opus", "gemini-team"}.issubset(built_in_names)

    def test_list_includes_global_presets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        global_presets = tmp_path / ".cafe" / "presets"
        global_presets.mkdir(parents=True)
        (global_presets / "my-global.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        manager = PresetManager()
        presets = manager.list()
        global_ones = [p for p in presets if p.source == "global"]
        assert any(p.name == "my-global" for p in global_ones)

    def test_list_includes_project_presets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project_presets = tmp_path / ".cafe" / "presets"
        project_presets.mkdir(parents=True)
        (project_presets / "my-project.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        monkeypatch.chdir(tmp_path)

        manager = PresetManager()
        presets = manager.list()
        project_ones = [p for p in presets if p.source == "project"]
        assert any(p.name == "my-project" for p in project_ones)

    def test_list_priority_project_over_global_over_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same name in all three layers
        project_presets = tmp_path / ".cafe" / "presets"
        project_presets.mkdir(parents=True)
        (project_presets / "default.yaml").write_text("# project version\npm:\n  name: P\n  cli: claude\n")

        global_home = tmp_path / "home"
        global_home.mkdir()
        global_presets = global_home / ".cafe" / "presets"
        global_presets.mkdir(parents=True)
        (global_presets / "default.yaml").write_text("# global version\npm:\n  name: G\n  cli: claude\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: global_home)

        manager = PresetManager()
        found = manager.find("default")
        assert found is not None
        assert found.source == "project"

    def test_find_returns_none_for_unknown_preset(self) -> None:
        manager = PresetManager()
        assert manager.find("does-not-exist-xyz") is None
