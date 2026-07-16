"""Tests for syncing bundled CAFE helper skills into user-level CLI directories."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.skills.global_installer import (
    DEFAULT_GLOBAL_SKILLS,
    GlobalSkillSyncError,
    sync_global_skills,
)

EXPECTED_CLI_ROOTS = {
    "claude": Path(".claude/skills"),
    "codex": Path(".codex/skills"),
    "copilot": Path(".copilot/skills"),
    "cursor": Path(".cursor/skills"),
    "gemini": Path(".gemini/skills"),
}


def _write_skill(source_root: Path, name: str, body: str) -> None:
    skill_dir = source_root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    references_dir = skill_dir / "references"
    references_dir.mkdir()
    (references_dir / "guide.md").write_text(f"{name} guide\n", encoding="utf-8")


def _write_default_sources(source_root: Path) -> None:
    for name in DEFAULT_GLOBAL_SKILLS:
        _write_skill(source_root, name, f"{name} v1")


def test_sync_global_skills_installs_updates_and_detects_unchanged(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    installed = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert installed.installed_count == 15
    assert installed.updated_count == 0
    assert installed.unchanged_count == 0
    assert installed.failed_count == 0
    for cli_root in EXPECTED_CLI_ROOTS.values():
        for name in DEFAULT_GLOBAL_SKILLS:
            destination = home_dir / cli_root / name
            assert (destination / "SKILL.md").is_file()
            assert (destination / "references" / "guide.md").is_file()

    changed_skill = source_root / "write-cafe-phase"
    (changed_skill / "SKILL.md").write_text(
        "---\nname: write-cafe-phase\ndescription: test skill\n---\n\nversion 2\n",
        encoding="utf-8",
    )
    stale_file = home_dir / ".codex/skills/write-cafe-phase/stale.md"
    stale_file.write_text("stale\n", encoding="utf-8")

    updated = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert updated.installed_count == 0
    assert updated.updated_count == 5
    assert updated.unchanged_count == 10
    assert not stale_file.exists()
    assert "version 2" in (
        home_dir / ".cursor/skills/write-cafe-phase/SKILL.md"
    ).read_text(encoding="utf-8")

    unchanged = sync_global_skills(source_root=source_root, home_dir=home_dir)
    assert unchanged.unchanged_count == 15
    assert unchanged.changed_count == 0


def test_sync_global_skills_validates_all_sources_before_writing(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_skill(source_root, "use-cafe-workflow", "available")

    with pytest.raises(GlobalSkillSyncError, match="Missing bundled skill"):
        sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert not home_dir.exists()


def test_sync_global_skills_can_target_selected_clis(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    summary = sync_global_skills(
        source_root=source_root,
        home_dir=home_dir,
        cli_names=["codex", "cursor"],
    )

    assert summary.installed_count == 6
    assert {result.cli for result in summary.results} == {"codex", "cursor"}
    assert (home_dir / ".codex/skills/use-cafe-workflow/SKILL.md").is_file()
    assert (home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md").is_file()
    assert not (home_dir / ".claude").exists()


def test_sync_global_skills_rejects_unknown_cli_before_writing(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    with pytest.raises(GlobalSkillSyncError, match="Unsupported CLI 'unknown'"):
        sync_global_skills(
            source_root=source_root,
            home_dir=home_dir,
            cli_names=["codex", "unknown"],
        )

    assert not home_dir.exists()


def test_sync_global_skills_rejects_skill_path_traversal_before_writing(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    with pytest.raises(GlobalSkillSyncError, match="Invalid bundled skill name"):
        sync_global_skills(
            source_root=source_root,
            home_dir=home_dir,
            skill_names=["../outside"],
        )

    assert not home_dir.exists()


def test_sync_global_skills_keeps_previous_copy_when_staging_fails(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    destination = home_dir / ".codex/skills/use-cafe-workflow"
    destination.mkdir(parents=True)
    skill_file = destination / "SKILL.md"
    skill_file.write_text("previous copy\n", encoding="utf-8")

    with patch(
        "cafe.skills.global_installer.shutil.copytree",
        side_effect=OSError("copy failed"),
    ):
        summary = sync_global_skills(
            source_root=source_root,
            home_dir=home_dir,
            skill_names=["use-cafe-workflow"],
            cli_names=["codex"],
        )

    assert summary.failed_count == 1
    assert summary.results[0].reason == "copy failed"
    assert skill_file.read_text(encoding="utf-8") == "previous copy\n"
