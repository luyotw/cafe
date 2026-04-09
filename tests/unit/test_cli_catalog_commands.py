"""Tests for playbook/skill catalog CLI commands."""

from pathlib import Path
from unittest.mock import patch

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


def test_skill_import_copies_multiple_valid_skill_folders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "incoming-skills"
    for name in ("alpha", "beta"):
        skill_dir = source_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n",
            encoding="utf-8",
        )

    result = runner.invoke(app, ["skill", "import", str(source_dir)])

    assert result.exit_code == 0
    assert "Imported 2 skill(s)" in result.stdout
    assert (tmp_path / ".cafe" / "skills" / "alpha" / "SKILL.md").exists()
    assert (tmp_path / ".cafe" / "skills" / "beta" / "SKILL.md").exists()


def test_skill_import_fails_for_missing_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["skill", "import", str(tmp_path / "missing-skills")])

    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_skill_import_reports_imported_and_skipped_items(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "incoming-skills"
    valid_dir = source_dir / "alpha"
    valid_dir.mkdir(parents=True, exist_ok=True)
    (valid_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n\n# alpha\n",
        encoding="utf-8",
    )
    (source_dir / "broken").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["skill", "import", str(source_dir)])

    assert result.exit_code == 0
    assert "Imported 1 skill(s)" in result.stdout
    assert "Skipped 1 item(s)" in result.stdout
    assert "broken" in result.stdout
    assert (tmp_path / ".cafe" / "skills" / "alpha" / "SKILL.md").exists()


def test_skill_import_prompts_before_overwriting_existing_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    existing_dir = tmp_path / ".cafe" / "skills" / "alpha"
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: old\n---\n\nOld body\n",
        encoding="utf-8",
    )
    source_dir = tmp_path / "incoming-skills" / "alpha"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nNew body\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.prompt_confirm", return_value=False) as mock_confirm:
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert mock_confirm.call_count == 1
    assert "Skipped 1 item(s)" in result.stdout
    assert "already exists" in result.stdout
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8").endswith("Old body\n")


def test_skill_import_overwrites_existing_skill_when_confirmed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    existing_dir = tmp_path / ".cafe" / "skills" / "alpha"
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: old\n---\n\nOld body\n",
        encoding="utf-8",
    )
    source_dir = tmp_path / "incoming-skills" / "alpha"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nNew body\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.prompt_confirm", return_value=True):
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert "Imported 1 skill(s)" in result.stdout
    assert "overwritten" in result.stdout
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8").endswith("New body\n")


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
