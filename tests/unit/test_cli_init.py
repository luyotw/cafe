"""Project initialization boundary tests (UT-004 / IT-001)."""

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def test_init_creates_only_project_owned_configuration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
        patch("cafe.ui.cli.prompt_list", side_effect=["standard", "medium"]),
    ):
        result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.stdout
    config = yaml.safe_load((tmp_path / ".cafe" / "config.yaml").read_text())
    assert config["settings"]["playbook"] == "standard"
    assert not (tmp_path / ".cafe" / "phases.yaml").exists()
    assert not (tmp_path / ".cafe" / "crew.yaml").exists()


def test_init_non_interactive_omits_repository_playbook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("cafe.ui.cli.prompt_confirm") as prompt_confirm,
        patch("cafe.ui.cli.prompt_list") as prompt_list,
    ):
        result = runner.invoke(app, ["init", "--no-interactive"])

    assert result.exit_code == 0, result.stdout
    config = yaml.safe_load((tmp_path / ".cafe" / "config.yaml").read_text())
    assert config == {"settings": {"auto_update": True}}
    prompt_confirm.assert_not_called()
    prompt_list.assert_not_called()


def test_init_has_no_preset_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--preset", "default"])
    assert result.exit_code != 0
    assert not (tmp_path / ".cafe" / "crew.yaml").exists()


def test_init_declining_overwrite_preserves_existing_config(tmp_path: Path, monkeypatch) -> None:
    cafe_dir = tmp_path / ".cafe"
    cafe_dir.mkdir()
    config_file = cafe_dir / "config.yaml"
    config_file.write_text("settings:\n  playbook: custom\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with patch("cafe.ui.cli.prompt_confirm", return_value=False):
        result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "custom" in config_file.read_text(encoding="utf-8")
