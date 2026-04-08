"""Tests for playbook/skill catalog CLI commands."""

from pathlib import Path

from typer.testing import CliRunner

from cafe.ui.cli import app


runner = CliRunner()


def test_playbook_list_includes_builtin_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["playbook", "list"])

    assert result.exit_code == 0
    assert "default" in result.stdout
    assert "hotfix" in result.stdout
    assert "simple" in result.stdout


def test_playbook_show_displays_custom_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  develop:
    skill: develop
    role: developer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["playbook", "show", "custom"])

    assert result.exit_code == 0
    assert "id: custom" in result.stdout
    assert "source=project" in result.stdout


def test_playbook_validate_reports_warning_and_strict_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "develop"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: develop\ndescription: custom\n---\n\nDevelop\n",
        encoding="utf-8",
    )
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  develop:
    skill: develop
    role: developer
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["playbook", "validate", "custom"])
    strict_result = runner.invoke(app, ["playbook", "validate", "custom", "--strict"])

    assert result.exit_code == 0
    assert "warning:" in result.stdout
    assert strict_result.exit_code == 1
    assert "redundant allowed_tools entry" in strict_result.stdout


def test_skill_list_and_show_prefer_project_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "plan"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: plan\ndescription: custom plan\n---\n\nProject override body\n",
        encoding="utf-8",
    )

    list_result = runner.invoke(app, ["skill", "list"])
    show_result = runner.invoke(app, ["skill", "show", "plan"])

    assert list_result.exit_code == 0
    assert "plan" in list_result.stdout
    assert "project" in list_result.stdout
    assert show_result.exit_code == 0
    assert "Project override body" in show_result.stdout
    assert "source=project" in show_result.stdout


def test_skill_validate_supports_strict_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "plan"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: wrong_name\ndescription: custom plan\n---\n\nProject override body\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["skill", "validate"])
    strict_result = runner.invoke(app, ["skill", "validate", "--strict"])

    assert result.exit_code == 0
    assert "warning:" in result.stdout
    assert strict_result.exit_code == 1
    assert "does not match folder" in strict_result.stdout


def test_help_lists_dynamic_playbook_steps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cafe" / "config.yaml").write_text("playbook: custom\n", encoding="utf-8")
    playbook_dir = tmp_path / ".cafe" / "playbooks"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "custom.yaml").write_text(
        """
playbook:
  id: custom
steps:
  qa:
    skill: review
    role: reviewer
    valid_status_codes: [CAFE_CONFIRMED]
    on:
      CAFE_CONFIRMED: _done
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "qa" in result.stdout
