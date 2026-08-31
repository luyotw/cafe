"""Tests for playbook/skill catalog CLI commands."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_global_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )


def test_playbook_list_includes_builtin_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["playbook", "list"])

    assert result.exit_code == 0
    assert "standard" in result.stdout
    assert "default" not in result.stdout
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
    assert "conversation_locale: auto" in result.stdout
    assert "source=project" in result.stdout


def test_playbook_confirmation_gates_are_derived_from_confirm_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    default_result = runner.invoke(app, ["playbook", "confirmation-gates", "standard"])
    research_result = runner.invoke(app, ["playbook", "confirmation-gates", "research"])

    assert default_result.exit_code == 0
    assert "Conversation locale: en-US" in default_result.stdout
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

    with (
        patch("cafe.skills.global_installer._default_home_dir", return_value=home_dir),
        patch(
            "cafe.skills.global_installer.shutil.which",
            side_effect=lambda executable: (
                "/test-bin/codex" if executable == "codex" else None
            ),
        ),
    ):
        result = runner.invoke(app, ["skill", "sync-global"])

    assert result.exit_code == 0
    assert "Synced 4 installation(s)" in result.stdout
    assert (home_dir / ".codex/skills/write-cafe-agent/SKILL.md").is_file()
    assert (home_dir / ".codex/skills/write-cafe-playbook/SKILL.md").is_file()
    assert not (home_dir / ".claude").exists()


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
    assert "Synced 8 installation(s)" in result.stdout
    assert (home_dir / ".codex/skills/use-cafe-workflow/SKILL.md").is_file()
    assert (home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md").is_file()
    assert not (home_dir / ".claude").exists()


def test_skill_sync_global_reports_when_no_cli_is_detected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    home_dir = tmp_path / "home"

    with (
        patch("cafe.skills.global_installer._default_home_dir", return_value=home_dir),
        patch("cafe.skills.global_installer.shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["skill", "sync-global"])

    assert result.exit_code == 0
    assert "No supported CLI agents detected" in result.stdout
    assert not home_dir.exists()


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
    allowed_tools: ["Bash(cafe verification check:*)"]
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


def _write_catalog_entries(project: Path) -> None:
    playbook = project / ".cafe" / "playbooks" / "standard.yaml"
    playbook.parent.mkdir(parents=True, exist_ok=True)
    playbook.write_text("playbook: {id: standard}\nsteps: {}\n", encoding="utf-8")
    skill = project / ".cafe" / "skills" / "develop" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: develop\ndescription: project\n---\n\nDevelop\n",
        encoding="utf-8",
    )
    agent = project / ".cafe" / "agents" / "developer" / "David.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(
        "---\nname: David\ndescription: project\n---\n\nDevelop\n",
        encoding="utf-8",
    )


def _preserve_catalog_agents() -> None:
    preview_result = runner.invoke(app, ["catalog", "migrate-agents", "--json"])
    assert preview_result.exit_code == 0, preview_result.stdout
    preview = json.loads(preview_result.stdout)
    decisions = [
        value
        for item in preview["items"]
        for value in ("--decision", f"{item['entry_id']}=preserve")
    ]
    result = runner.invoke(
        app,
        [
            "catalog",
            "migrate-agents",
            "--token",
            preview["token"],
            *decisions,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_catalog_check_defaults_to_all_three_kinds_with_complete_json(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_catalog_entries(tmp_path)
    _preserve_catalog_agents()

    result = runner.invoke(app, ["catalog", "check", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert {item["entry_id"] for item in payload["entries"]} == {
        "playbook:standard",
        "phase:develop",
        "agent:developer/David",
    }
    assert payload["difference_count"] == 3
    assert set(payload["effective_digests"]) == {"playbook", "phase", "agent"}


def test_catalog_check_json_reports_a_bounded_over_budget_state(
    tmp_path: Path, monkeypatch
) -> None:
    from cafe.catalogs.resolver import (
        MAX_CATALOG_DISCOVERY_ENTRIES,
        MAX_CATALOG_OPERATION_ENTRIES,
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    entry_count = (MAX_CATALOG_OPERATION_ENTRIES * 2) + 1
    for index in range(entry_count):
        skill = tmp_path / ".cafe" / "skills" / f"phase-{index:03d}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: phase-{index:03d}\ndescription: project\n---\n",
            encoding="utf-8",
        )

    result = runner.invoke(app, ["catalog", "check", "--kind", "phase", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["entry_limit"] == MAX_CATALOG_OPERATION_ENTRIES
    assert payload["discovery_entry_limit"] == MAX_CATALOG_DISCOVERY_ENTRIES
    assert payload["discovery_complete"] is True
    assert payload["schema_version"] == 1
    assert payload["status"] == "over_budget"
    assert payload["affected_entry_ids"][0] == "phase:phase-000"
    assert set(payload["affected_entry_ids"]) == {
        f"phase:phase-{index:03d}" for index in range(entry_count)
    }
    assert "next_cursor" not in payload

    narrowed = runner.invoke(
        app,
        [
            "catalog",
            "check",
            "--kind",
            "phase",
            "--entry",
            "phase:phase-000",
            "--json",
        ],
    )
    assert narrowed.exit_code == 0, narrowed.stdout
    assert [item["entry_id"] for item in json.loads(narrowed.stdout)["entries"]] == [
        "phase:phase-000"
    ]


def test_catalog_check_scoped_over_budget_does_not_fall_back_to_unscoped_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    from cafe.catalogs.resolver import MAX_CATALOG_OPERATION_ENTRIES

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    entry_ids: list[str] = []
    for index in range(MAX_CATALOG_OPERATION_ENTRIES + 1):
        name = f"phase-{index:03d}"
        entry_ids.append(f"phase:{name}")
        skill = tmp_path / ".cafe" / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: {name}\ndescription: project\n---\n",
            encoding="utf-8",
        )
    unrelated = tmp_path / ".cafe" / "skills" / "aaa" / "SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(
        "---\nname: aaa\ndescription: unrelated\n---\n",
        encoding="utf-8",
    )

    arguments = ["catalog", "check", "--kind", "phase", "--json"]
    arguments.extend(value for entry_id in entry_ids for value in ("--entry", entry_id))
    result = runner.invoke(app, arguments)

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "over_budget"
    assert payload["scope"] == "explicit"
    assert payload["requested_entry_count"] == MAX_CATALOG_OPERATION_ENTRIES + 1
    assert "affected_entry_ids" not in payload


def test_catalog_check_json_reports_fallback_only_effective_digests(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)

    before = runner.invoke(app, ["catalog", "check", "--json"])
    global_playbook = global_root / "playbooks" / "global-only.yaml"
    global_playbook.parent.mkdir(parents=True)
    global_playbook.write_text(
        "playbook: {id: global-only}\nsteps: {}\n", encoding="utf-8"
    )
    after = runner.invoke(app, ["catalog", "check", "--json"])

    assert before.exit_code == 0, before.stdout
    assert after.exit_code == 0, after.stdout
    before_payload = json.loads(before.stdout)
    after_payload = json.loads(after.stdout)
    assert set(before_payload["effective_digests"]) == {
        "playbook",
        "phase",
        "agent",
    }
    assert before_payload["status"] == "no_project_entries"
    assert after_payload["effective_digests"]["playbook"] != before_payload[
        "effective_digests"
    ]["playbook"]
    assert after_payload["comparison_token"] != before_payload["comparison_token"]


def test_catalog_check_supports_kind_and_entry_filters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    _write_catalog_entries(tmp_path)

    result = runner.invoke(
        app,
        [
            "catalog",
            "check",
            "--kind",
            "playbook",
            "--entry",
            "playbook:standard",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert [item["entry_id"] for item in payload["entries"]] == ["playbook:standard"]


def test_catalog_sync_global_requires_and_honors_exact_noninteractive_approval(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_catalog_entries(tmp_path)
    check = runner.invoke(
        app,
        [
            "catalog",
            "check",
            "--entry",
            "playbook:standard",
            "--json",
        ],
    )
    token = json.loads(check.stdout)["comparison_token"]

    missing_approval = runner.invoke(
        app, ["catalog", "sync-global", "--token", token, "--json"]
    )
    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--entry",
            "playbook:standard",
            "--token",
            token,
            "--approve",
            "playbook:standard",
            "--json",
        ],
    )

    assert missing_approval.exit_code == 1
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["updated"] == ["playbook:standard"]
    assert (global_root / "playbooks" / "standard.yaml").is_file()


def test_catalog_sync_global_rejects_stale_cli_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_catalog_entries(tmp_path)
    check = runner.invoke(app, ["catalog", "check", "--json"])
    token = json.loads(check.stdout)["comparison_token"]
    agent = tmp_path / ".cafe" / "agents" / "developer" / "David.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-global",
            "--token",
            token,
            "--approve",
            "agent:developer/David",
        ],
    )

    assert result.exit_code == 1
    assert not (global_root / "agents" / "developer" / "David.md").exists()


def test_catalog_sync_global_interactive_preview_updates_only_selected_entry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_catalog_entries(tmp_path)

    with (
        patch("cafe.ui.cli.prompt_checkbox", return_value=["phase:develop"]),
        patch("cafe.ui.cli.prompt_confirm", return_value=True),
    ):
        result = runner.invoke(app, ["catalog", "sync-global"])

    assert result.exit_code == 0, result.stdout
    assert (global_root / "skills" / "develop" / "SKILL.md").is_file()
    assert not (global_root / "playbooks" / "standard.yaml").exists()


def test_catalog_sync_global_bounds_human_summary_and_keeps_json_complete(
    tmp_path: Path, monkeypatch
) -> None:
    current_global = {"path": tmp_path / "human-global"}
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: current_global["path"])

    def run_sync(project: Path, *, json_output: bool):
        project.mkdir()
        monkeypatch.chdir(project)
        entry_ids = []
        for index in range(52):
            name = f"item-{index}"
            entry_ids.append(f"playbook:{name}")
            path = project / ".cafe" / "playbooks" / f"{name}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"playbook: {{id: {name}}}\nsteps: {{}}\n",
                encoding="utf-8",
            )
        check = runner.invoke(app, ["catalog", "check", "--kind", "playbook", "--json"])
        assert check.exit_code == 0, check.stdout
        token = json.loads(check.stdout)["comparison_token"]
        approvals = [value for entry_id in entry_ids for value in ("--approve", entry_id)]
        arguments = [
            "catalog",
            "sync-global",
            "--kind",
            "playbook",
            "--token",
            token,
            *approvals,
        ]
        if json_output:
            arguments.append("--json")
        return runner.invoke(app, arguments), entry_ids

    human_result, entry_ids = run_sync(tmp_path / "human-project", json_output=False)
    current_global["path"] = tmp_path / "json-global"
    json_result, json_entry_ids = run_sync(tmp_path / "json-project", json_output=True)

    assert human_result.exit_code == 0, human_result.stdout
    assert sum("playbook:item-" in line for line in human_result.stdout.splitlines()) == 50
    assert entry_ids[50] not in human_result.stdout
    assert "--json" in human_result.stdout
    assert json_result.exit_code == 0, json_result.stdout
    assert set(json.loads(json_result.stdout)["updated"]) == set(json_entry_ids)


def test_catalog_legacy_migration_requires_digest_bound_decisions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_root = tmp_path / "global"
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: global_root)
    _write_catalog_entries(tmp_path)

    preview = runner.invoke(app, ["catalog", "migrate-agents", "--json"])
    assert preview.exit_code == 0, preview.stdout
    token = json.loads(preview.stdout)["token"]
    result = runner.invoke(
        app,
        [
            "catalog",
            "migrate-agents",
            "--token",
            token,
            "--decision",
            "agent:developer/David=preserve",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["status"] == "completed"
    assert (tmp_path / ".cafe" / "agents" / "developer" / "David.md").is_file()


def test_catalog_migration_bounds_human_preview_and_keeps_json_complete(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global")
    entry_ids = []
    for index in range(52):
        name = f"Agent{index}"
        entry_ids.append(f"agent:developer/{name}")
        path = tmp_path / ".cafe" / "agents" / "developer" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: project\n---\n\nDevelop\n",
            encoding="utf-8",
        )

    human_result = runner.invoke(app, ["catalog", "migrate-agents"])
    json_result = runner.invoke(app, ["catalog", "migrate-agents", "--json"])

    assert human_result.exit_code == 0, human_result.stdout
    human_entry_ids = {
        line.strip().split()[0]
        for line in human_result.stdout.splitlines()
        if line.strip().startswith("agent:developer/Agent")
    }
    assert len(human_entry_ids) == 50
    assert len(set(entry_ids) - human_entry_ids) == 2
    assert "--json" in human_result.stdout
    assert json_result.exit_code == 0, json_result.stdout
    assert {item["entry_id"] for item in json.loads(json_result.stdout)["items"]} == set(entry_ids)
