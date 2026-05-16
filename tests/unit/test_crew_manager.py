"""Tests for CrewManager."""

import pytest
from pathlib import Path

import yaml

from cafe.utils.crew import CrewManager


class TestCrewManagerLoad:
    """Test CrewManager.load()."""

    def test_load_returns_crew_yaml_when_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        crew_yaml = cafe_dir / "crew.yaml"
        crew_yaml.write_text(
            "pm:\n  name: Roger\n  cli: claude\n"
            "developer:\n  name: David\n  cli: claude\n"
            "reviewer:\n  name: Richard\n  cli: claude\n"
        )
        manager = CrewManager(cafe_dir=cafe_dir)
        data = manager.load()
        assert "pm" in data
        assert "developer" in data
        assert "reviewer" in data
        assert data["pm"]["name"] == "Roger"

    def test_load_falls_back_to_config_yaml_agents_when_no_crew(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_yaml = cafe_dir / "config.yaml"
        config_yaml.write_text(
            "agents:\n"
            "  pm:\n    name: Roger\n    cli: copilot\n"
            "  developer:\n    name: David\n    cli: copilot\n"
            "  reviewer:\n    name: Richard\n    cli: copilot\n"
        )
        manager = CrewManager(cafe_dir=cafe_dir)
        data = manager.load()
        assert "pm" in data
        assert data["pm"]["cli"] == "copilot"

    def test_load_returns_empty_when_neither_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        manager = CrewManager(cafe_dir=cafe_dir)
        data = manager.load()
        assert data == {}

    def test_exists_returns_true_when_crew_yaml_present(self, tmp_path: Path) -> None:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: Roger\n  cli: claude\n")
        manager = CrewManager(cafe_dir=cafe_dir)
        assert manager.exists() is True

    def test_exists_returns_false_when_crew_yaml_absent(self, tmp_path: Path) -> None:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        manager = CrewManager(cafe_dir=cafe_dir)
        assert manager.exists() is False

    def test_load_prefers_crew_yaml_over_config_yaml_agents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        (cafe_dir / "crew.yaml").write_text("pm:\n  name: CrewPM\n  cli: claude\n")
        (cafe_dir / "config.yaml").write_text("agents:\n  pm:\n    name: ConfigPM\n    cli: copilot\n")
        manager = CrewManager(cafe_dir=cafe_dir)
        data = manager.load()
        assert data["pm"]["name"] == "CrewPM"


class TestCrewManagerSave:
    """Test CrewManager.save()."""

    def test_save_writes_crew_yaml(self, tmp_path: Path) -> None:
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        manager = CrewManager(cafe_dir=cafe_dir)
        agents = {"pm": {"name": "Roger", "cli": "claude"}}
        manager.save(agents)
        crew_yaml = cafe_dir / "crew.yaml"
        assert crew_yaml.exists()
        data = yaml.safe_load(crew_yaml.read_text())
        assert data["pm"]["name"] == "Roger"
