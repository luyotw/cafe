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
    assert "editorial" in result.stdout
    assert "research" in result.stdout
    assert "incident" in result.stdout


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
    skill: cafe-develop
    role: developer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["playbook", "show", "custom"])

    assert result.exit_code == 0
    assert "id: custom" in result.stdout
    assert "source=project" in result.stdout


def test_playbook_confirmation_gates_are_derived_from_confirm_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    default_result = runner.invoke(app, ["playbook", "confirmation-gates", "default"])
    research_result = runner.invoke(app, ["playbook", "confirmation-gates", "research"])

    assert default_result.exit_code == 0
    assert "steps declaring on.confirm_output" in default_result.stdout
    assert "  - spec" in default_result.stdout
    assert "  - plan" in default_result.stdout
    assert "  - develop" not in default_result.stdout
    assert research_result.exit_code == 0
    assert "(none)" in research_result.stdout
    assert "Reactive clarification, permission, and alignment pauses" in research_result.stdout


def test_skill_sync_global_installs_bundled_helper_skills(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    home_dir = tmp_path / "home"

    with patch(
        "cafe.skills.global_installer._default_home_dir",
        return_value=home_dir,
    ):
        result = runner.invoke(app, ["skill", "sync-global"])

    assert result.exit_code == 0
    assert "Synced 15 installation(s)" in result.stdout
    assert (home_dir / ".claude/skills/use-cafe-workflow/SKILL.md").is_file()
    assert (home_dir / ".codex/skills/write-cafe-playbook/SKILL.md").is_file()
    assert (home_dir / ".copilot/skills/write-cafe-phase/SKILL.md").is_file()
    assert (home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md").is_file()
    assert (home_dir / ".gemini/skills/write-cafe-playbook/SKILL.md").is_file()


def test_skill_sync_global_can_limit_target_clis(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    home_dir = tmp_path / "home"

    with patch(
        "cafe.skills.global_installer._default_home_dir",
        return_value=home_dir,
    ):
        result = runner.invoke(
            app,
            ["skill", "sync-global", "--cli", "codex", "--cli", "cursor"],
        )

    assert result.exit_code == 0
    assert "Synced 6 installation(s)" in result.stdout
    assert (home_dir / ".codex/skills/use-cafe-workflow/SKILL.md").is_file()
    assert (home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md").is_file()
    assert not (home_dir / ".claude").exists()


def test_playbook_validate_reports_warning_and_strict_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-develop"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cafe-develop\ndescription: custom\n---\n\nDevelop\n",
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
    skill: cafe-develop
    role: developer
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
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
    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-plan"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cafe-plan\ndescription: custom plan\n---\n\nProject override body\n",
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
    skill_dir = tmp_path / ".cafe" / "skills" / "cafe-plan"
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

    with patch("cafe.ui.cli.prompt_confirm", return_value=True) as mock_confirm:
        result = runner.invoke(app, ["skill", "import", str(source_dir)])

    assert result.exit_code == 0
    assert mock_confirm.call_count == 1
    assert "Found 2 skill(s) to import:" in result.stdout
    assert "alpha" in result.stdout
    assert "beta" in result.stdout
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

    with patch("cafe.ui.cli.prompt_confirm", return_value=True):
        result = runner.invoke(app, ["skill", "import", str(source_dir)])

    assert result.exit_code == 0
    assert "Imported 1 skill(s)" in result.stdout
    assert "Skipped 1 item(s)" in result.stdout
    assert "broken" in result.stdout
    assert (tmp_path / ".cafe" / "skills" / "alpha" / "SKILL.md").exists()


def test_skill_import_skips_mismatched_frontmatter_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "incoming-skills" / "alpha"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: beta\ndescription: wrong name\n---\n\n# alpha\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.prompt_confirm", return_value=True):
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert "Skipped 1 item(s)" in result.stdout
    assert "frontmatter name does not match folder name" in result.stdout
    assert not (tmp_path / ".cafe" / "skills" / "alpha").exists()


def test_skill_import_prompts_before_overwriting_existing_skill(
    tmp_path: Path, monkeypatch
) -> None:
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

    with patch("cafe.ui.cli.prompt_confirm", side_effect=[True, False]) as mock_confirm:
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert mock_confirm.call_count == 2
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

    with patch("cafe.ui.cli.prompt_confirm", side_effect=[True, True]):
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert "Imported 1 skill(s)" in result.stdout
    assert "overwritten" in result.stdout
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8").endswith("New body\n")


def test_skill_import_cancelled_when_initial_confirmation_declined(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe").mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "incoming-skills" / "alpha"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n\n# alpha\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.prompt_confirm", return_value=False) as mock_confirm:
        result = runner.invoke(app, ["skill", "import", str(source_dir.parent)])

    assert result.exit_code == 0
    assert mock_confirm.call_count == 1
    assert "Found 1 skill(s) to import:" in result.stdout
    assert "Cancelled" in result.stdout
    assert not (tmp_path / ".cafe" / "skills" / "alpha").exists()


def test_skill_rm_deletes_named_skills_when_confirmed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    alpha_dir = tmp_path / ".cafe" / "skills" / "alpha"
    beta_dir = tmp_path / ".cafe" / "skills" / "beta"
    for skill_dir in (alpha_dir, beta_dir):
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_dir.name}\ndescription: {skill_dir.name}\n---\n",
            encoding="utf-8",
        )

    with patch("cafe.ui.cli.prompt_confirm", return_value=True) as mock_confirm:
        result = runner.invoke(app, ["skill", "rm", "alpha", "beta"])

    assert result.exit_code == 0
    assert mock_confirm.call_count == 1
    assert "Removed 2 skill(s)" in result.stdout
    assert not alpha_dir.exists()
    assert not beta_dir.exists()


def test_skill_rm_interactive_uses_checkbox_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    alpha_dir = tmp_path / ".cafe" / "skills" / "alpha"
    beta_dir = tmp_path / ".cafe" / "skills" / "beta"
    for skill_dir in (alpha_dir, beta_dir):
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_dir.name}\ndescription: {skill_dir.name}\n---\n",
            encoding="utf-8",
        )

    with patch("cafe.ui.cli.prompt_checkbox", return_value=["beta"]) as mock_checkbox, \
         patch("cafe.ui.cli.prompt_confirm", return_value=True):
        result = runner.invoke(app, ["skill", "rm"])

    assert result.exit_code == 0
    mock_checkbox.assert_called_once()
    assert alpha_dir.exists()
    assert not beta_dir.exists()
    assert "Removed 1 skill(s)" in result.stdout


def test_skill_rm_cancelled_when_confirmation_declined(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    alpha_dir = tmp_path / ".cafe" / "skills" / "alpha"
    alpha_dir.mkdir(parents=True, exist_ok=True)
    (alpha_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n",
        encoding="utf-8",
    )

    with patch("cafe.ui.cli.prompt_confirm", return_value=False):
        result = runner.invoke(app, ["skill", "rm", "alpha"])

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert alpha_dir.exists()


def test_skill_rm_reports_missing_skills_when_none_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "skills").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["skill", "rm", "missing"])

    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_skill_rm_rejects_parent_path_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "skills").mkdir(parents=True, exist_ok=True)
    victim_dir = tmp_path / "victim-skill"
    victim_dir.mkdir(parents=True, exist_ok=True)
    (victim_dir / "SKILL.md").write_text(
        "---\nname: victim\ndescription: victim\n---\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["skill", "rm", "../victim-skill", "--force"])

    assert result.exit_code == 1
    assert "invalid skill name" in result.stdout
    assert victim_dir.exists()


def test_skill_rm_rejects_absolute_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cafe" / "skills").mkdir(parents=True, exist_ok=True)
    victim_dir = tmp_path / "absolute-target"
    victim_dir.mkdir(parents=True, exist_ok=True)
    (victim_dir / "SKILL.md").write_text(
        "---\nname: absolute-target\ndescription: victim\n---\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["skill", "rm", str(victim_dir), "--force"])

    assert result.exit_code == 1
    assert "invalid skill name" in result.stdout
    assert victim_dir.exists()


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
    skill: cafe-review
    role: reviewer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "qa" in result.stdout


def test_help_hides_legacy_phase_aliases() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    commands_section = result.stdout.split("╭─ Commands", 1)[1]
    for command_name in ("spec", "plan", "develop", "dev", "review", "pr"):
        assert f"│ {command_name} " not in commands_section
