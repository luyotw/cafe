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


class TestPresetManagerApply:
    """Test PresetManager.apply()."""

    def test_apply_copies_preset_to_crew_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        manager = PresetManager()
        manager.apply("default", cafe_dir=cafe_dir)
        crew_yaml = cafe_dir / "crew.yaml"
        assert crew_yaml.exists()
        import yaml
        data = yaml.safe_load(crew_yaml.read_text())
        assert "pm" in data
        assert "developer" in data
        assert "reviewer" in data

    def test_apply_overwrites_existing_crew_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text("# old content\n")
        manager = PresetManager()
        manager.apply("default", cafe_dir=cafe_dir)
        data = crew_yaml.read_text()
        assert "old content" not in data

    def test_apply_raises_when_preset_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        manager = PresetManager()
        with pytest.raises(Exception) as exc_info:
            manager.apply("totally-unknown-xyz", cafe_dir=cafe_dir)
        assert "totally-unknown-xyz" in str(exc_info.value)


class TestPresetManagerSave:
    """Test PresetManager.save()."""

    def test_save_default_writes_to_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text("pm:\n  name: Roger\n  cli: claude\n")
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        manager = PresetManager()
        dest = manager.save("my-team", cafe_dir=cafe_dir)
        assert dest == home / ".cafe" / "presets" / "my-team.yaml"
        assert dest.exists()

    def test_save_local_flag_writes_to_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text("pm:\n  name: Roger\n  cli: claude\n")
        manager = PresetManager()
        dest = manager.save("my-local", local=True, cafe_dir=cafe_dir)
        assert dest == cafe_dir / "presets" / "my-local.yaml"
        assert dest.exists()

    def test_save_existing_raises_unless_overwrite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text("pm:\n  name: Roger\n  cli: claude\n")
        manager = PresetManager()
        manager.save("existing", local=True, cafe_dir=cafe_dir)
        with pytest.raises(Exception):
            manager.save("existing", local=True, cafe_dir=cafe_dir)
        # overwrite=True should not raise
        manager.save("existing", local=True, overwrite=True, cafe_dir=cafe_dir)
