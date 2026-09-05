"""Tests for syncing bundled CAFE helper skills into user-level CLI directories."""

import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from time import monotonic
from unittest.mock import patch

import pytest

from cafe.skills import global_installer
from cafe.skills.global_installer import (
    DEFAULT_GLOBAL_SKILLS,
    GlobalSkillSyncError,
    GlobalSkillSyncSummary,
    auto_sync_global_skills,
    detect_global_skill_clis,
    sync_global_skills,
)

EXPECTED_CLI_ROOTS = {
    "claude": Path(".claude/skills"),
    "codex": Path(".codex/skills"),
    "copilot": Path(".copilot/skills"),
    "cursor": Path(".cursor/skills"),
    "gemini": Path(".gemini/skills"),
}


@pytest.fixture(autouse=True)
def _detect_supported_clis_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    executable_names = {
        executable
        for names in global_installer.GLOBAL_CLI_EXECUTABLES.values()
        for executable in names
    }
    monkeypatch.setattr(
        global_installer.shutil,
        "which",
        lambda executable: f"/test-bin/{executable}" if executable in executable_names else None,
    )


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


def test_detect_global_skill_clis_uses_path_or_existing_non_cafe_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "home"
    managed_only = home_dir / ".claude/skills/use-cafe-workflow"
    managed_only.mkdir(parents=True)
    (managed_only / "SKILL.md").write_text("managed", encoding="utf-8")
    gemini_config = home_dir / ".gemini/settings.json"
    gemini_config.parent.mkdir(parents=True)
    gemini_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        global_installer.shutil,
        "which",
        lambda executable: "/test-bin/codex" if executable == "codex" else None,
    )

    detected = detect_global_skill_clis(home_dir=home_dir)

    assert detected == ["codex", "gemini"]


def test_default_sync_skips_undetected_clis_but_explicit_target_creates_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    monkeypatch.setattr(
        global_installer.shutil,
        "which",
        lambda executable: "/test-bin/codex" if executable == "codex" else None,
    )

    detected = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert detected.installed_count == 4
    assert (home_dir / ".codex/skills/use-cafe-workflow/SKILL.md").is_file()
    assert not (home_dir / ".claude").exists()

    explicit = sync_global_skills(
        source_root=source_root,
        home_dir=home_dir,
        cli_names=["cursor"],
    )

    assert explicit.installed_count == 4
    assert (home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md").is_file()


def test_default_sync_is_a_noop_when_no_supported_cli_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    monkeypatch.setattr(global_installer.shutil, "which", lambda _: None)

    explicit = sync_global_skills(source_root=source_root, home_dir=home_dir)
    automatic = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert explicit.results == []
    assert automatic is None
    assert not home_dir.exists()


def test_sync_global_skills_installs_updates_and_detects_unchanged(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    installed = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert installed.installed_count == 20
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
    assert updated.unchanged_count == 15
    assert not stale_file.exists()
    assert "version 2" in (home_dir / ".cursor/skills/write-cafe-phase/SKILL.md").read_text(
        encoding="utf-8"
    )

    unchanged = sync_global_skills(source_root=source_root, home_dir=home_dir)
    assert unchanged.unchanged_count == 20
    assert unchanged.changed_count == 0


def test_sync_global_skills_ignores_generated_python_bytecode(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    generated = source_root / "use-cafe-workflow" / "scripts" / "__pycache__"
    generated.mkdir(parents=True)
    bytecode = generated / "helper.cpython-312.pyc"
    bytecode.write_bytes(b"first generated version")

    installed = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert installed.installed_count == 20
    assert not (home_dir / ".codex/skills/use-cafe-workflow/scripts/__pycache__").exists()

    bytecode.write_bytes(b"second generated version")
    unchanged = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert unchanged.unchanged_count == 20
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

    assert summary.installed_count == 8
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


def test_sync_global_skills_rolls_back_the_complete_batch_on_publish_failure(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    sync_global_skills(source_root=source_root, home_dir=home_dir)
    for name in DEFAULT_GLOBAL_SKILLS:
        (source_root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test skill\n---\n\n{name} v2\n",
            encoding="utf-8",
        )

    original_publish = global_installer._publish_staged_replacement
    publish_count = 0

    def fail_second_publish(operation) -> None:
        nonlocal publish_count
        publish_count += 1
        if publish_count == 2:
            raise OSError("publish failed")
        original_publish(operation)

    with patch.object(
        global_installer,
        "_publish_staged_replacement",
        side_effect=fail_second_publish,
    ):
        summary = sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert summary.failed_count == 20
    assert summary.changed_count == 0
    for cli_root in EXPECTED_CLI_ROOTS.values():
        for name in DEFAULT_GLOBAL_SKILLS:
            installed = (home_dir / cli_root / name / "SKILL.md").read_text(encoding="utf-8")
            assert f"{name} v1" in installed
            assert f"{name} v2" not in installed
        assert not list((home_dir / cli_root).glob(".cafe-*"))


def test_publish_failure_after_backup_move_restores_the_previous_copy(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    sync_global_skills(
        source_root=source_root,
        home_dir=home_dir,
        skill_names=["use-cafe-workflow"],
        cli_names=["codex"],
    )
    source = source_root / "use-cafe-workflow"
    destination = home_dir / ".codex/skills/use-cafe-workflow"
    (source / "SKILL.md").write_text(
        "---\nname: use-cafe-workflow\ndescription: test skill\n---\n\nversion 2\n",
        encoding="utf-8",
    )
    operation = global_installer._stage_directory_replacement(
        cli="codex",
        skill="use-cafe-workflow",
        source=source,
        destination=destination,
        status="updated",
    )
    shutil.rmtree(operation.staged)

    with pytest.raises(OSError):
        global_installer._publish_staged_replacement(operation)

    assert "use-cafe-workflow v1" in (destination / "SKILL.md").read_text(encoding="utf-8")
    assert not operation.backup.exists()
    global_installer._cleanup_staged_replacement(operation)


def test_auto_sync_serializes_concurrent_initialization(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    worker_count = 4
    barrier = Barrier(worker_count)

    def run_auto_sync() -> GlobalSkillSyncSummary | None:
        barrier.wait()
        return auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(run_auto_sync) for _ in range(worker_count)]
        results = [future.result() for future in futures]

    summaries = [result for result in results if result is not None]
    assert len(summaries) == 1
    assert summaries[0].installed_count == 20
    assert summaries[0].failed_count == 0


def test_auto_sync_never_replaces_existing_differing_destinations(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    installed = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)
    assert installed is not None
    assert installed.installed_count == 20

    destination = home_dir / ".codex/skills/use-cafe-workflow/SKILL.md"
    original = destination.read_bytes()
    (source_root / "use-cafe-workflow" / "SKILL.md").write_text(
        "---\nname: use-cafe-workflow\ndescription: test skill\n---\n\nversion 2\n",
        encoding="utf-8",
    )
    unchanged = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert unchanged is None
    assert destination.read_bytes() == original


def test_auto_sync_treats_existing_symlink_as_immutable(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    external = tmp_path / "external-skill"
    _write_skill(tmp_path, "external-skill", "external")
    destination = home_dir / ".codex/skills/use-cafe-workflow"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(external, target_is_directory=True)

    summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert summary is not None
    assert summary.installed_count == 19
    assert summary.unchanged_count == 1
    assert destination.is_symlink()
    assert "external" in (destination / "SKILL.md").read_text(encoding="utf-8")


def test_auto_sync_installs_a_missing_destination_without_touching_existing_ones(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    auto_sync_global_skills(source_root=source_root, home_dir=home_dir)
    existing = home_dir / ".cursor/skills/use-cafe-workflow/SKILL.md"
    existing.write_text("local content\n", encoding="utf-8")
    missing = home_dir / ".codex/skills/use-cafe-workflow"
    shutil.rmtree(missing)

    repaired = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert repaired is not None
    assert repaired.installed_count == 1
    assert repaired.updated_count == 0
    assert (missing / "SKILL.md").is_file()
    assert existing.read_text(encoding="utf-8") == "local content\n"


def test_auto_sync_retries_after_failed_install_without_recording_state(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    with patch(
        "cafe.skills.global_installer._stage_directory_replacement",
        side_effect=OSError("copy failed"),
    ):
        failed = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert failed is not None
    assert failed.failed_count == 20
    assert not (home_dir / ".cafe/cache/global-skills-sync.json").exists()

    retried = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)
    assert retried is not None
    assert retried.installed_count == 20


def test_auto_sync_leaves_legacy_state_untouched(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    state_file = home_dir / ".cafe/cache/global-skills-sync.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text('{"legacy": true}\n', encoding="utf-8")

    auto_sync_global_skills(source_root=source_root, home_dir=home_dir)
    sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert state_file.read_text(encoding="utf-8") == '{"legacy": true}\n'


def test_trusted_automatic_source_uses_packaged_bundle_outside_git(tmp_path: Path) -> None:
    bundle = tmp_path / "site-packages/cafe/data/skills"
    _write_default_sources(bundle)

    resolved = global_installer._trusted_automatic_source_root(bundle)

    assert resolved == bundle.resolve()


def test_trusted_automatic_source_fails_closed_for_invalid_checkout(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "src/cafe/data/skills"
    _write_default_sources(bundle)
    (checkout / ".git").mkdir(parents=True)

    with patch.object(
        global_installer,
        "_discover_git_roots",
        side_effect=GlobalSkillSyncError("Git root discovery failed"),
    ):
        with pytest.raises(GlobalSkillSyncError, match="Git root discovery failed"):
            global_installer._trusted_automatic_source_root(bundle)


def test_trusted_automatic_source_bounds_git_discovery_time(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "src/cafe/data/skills"
    _write_default_sources(bundle)
    (checkout / ".git").mkdir(parents=True)

    with patch.object(
        global_installer.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(("git",), timeout=0.01),
    ) as run:
        with pytest.raises(GlobalSkillSyncError):
            global_installer._trusted_automatic_source_root(bundle)

    assert run.call_args.kwargs["timeout"] > 0


def test_auto_sync_preserves_destination_that_appears_before_staging(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    original_stage = global_installer._stage_directory_replacement
    appeared = False

    def create_destination_before_staging(**kwargs):
        nonlocal appeared
        if not appeared:
            appeared = True
            destination = kwargs["destination"]
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_text(
                "concurrent-existing\n", encoding="utf-8"
            )
        return original_stage(**kwargs)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(
            global_installer,
            "_stage_directory_replacement",
            side_effect=create_destination_before_staging,
        ),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    destination = home_dir / ".codex/skills/use-cafe-workflow/SKILL.md"
    assert summary is not None
    assert summary.updated_count == 0
    assert destination.read_text(encoding="utf-8") == "concurrent-existing\n"


def test_auto_sync_preserves_symlink_that_appears_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    external = tmp_path / "external"
    _write_default_sources(source_root)
    _write_skill(tmp_path, "external", "concurrent symlink")
    original_publish = global_installer._publish_staged_replacement
    appeared = False

    def create_symlink_before_publish(operation) -> None:
        nonlocal appeared
        if not appeared:
            appeared = True
            operation.destination.symlink_to(external, target_is_directory=True)
        original_publish(operation)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(
            global_installer,
            "_publish_staged_replacement",
            side_effect=create_symlink_before_publish,
        ),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    destination = home_dir / ".codex/skills/use-cafe-workflow"
    assert summary is not None
    assert summary.updated_count == 0
    assert destination.is_symlink()
    assert "concurrent symlink" in (destination / "SKILL.md").read_text(encoding="utf-8")


def test_auto_sync_atomically_publishes_a_complete_missing_tree(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    original_move = global_installer.move_without_replacement
    intervened = False

    def create_destination_at_publish_boundary(*args, **kwargs) -> None:
        nonlocal intervened
        if not intervened:
            intervened = True
            source_directory = kwargs["source_directory"]
            destination_directory = kwargs["destination_directory"]
            source = source_directory.path / args[0]
            destination = destination_directory.path / args[1]
            assert (source / "SKILL.md").is_file()
            assert not destination.exists()
            destination.mkdir()
            (destination / "SKILL.md").write_text(
                "concurrent-existing\n", encoding="utf-8"
            )
        original_move(*args, **kwargs)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(
            global_installer,
            "move_without_replacement",
            side_effect=create_destination_at_publish_boundary,
        ),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    destination = home_dir / ".codex/skills/use-cafe-workflow/SKILL.md"
    assert summary is not None
    assert summary.installed_count == 0
    assert summary.failed_count == len(DEFAULT_GLOBAL_SKILLS)
    assert destination.read_text(encoding="utf-8") == "concurrent-existing\n"


def test_auto_sync_publishes_complete_missing_trees_on_windows(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(global_installer.sys, "platform", "win32"),
        patch.object(
            global_installer,
            "bound_directory",
            side_effect=AssertionError("POSIX publication used on Windows"),
        ),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    assert summary is not None
    assert summary.installed_count == len(DEFAULT_GLOBAL_SKILLS)
    assert summary.failed_count == 0
    for skill in DEFAULT_GLOBAL_SKILLS:
        destination = home_dir / ".codex/skills" / skill
        assert (destination / "SKILL.md").is_file()
        assert (destination / "references/guide.md").is_file()


def test_auto_sync_windows_preserves_a_destination_that_wins_the_move(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    def concurrent_windows_rename(source, destination) -> None:
        competing_destination = Path(destination)
        competing_destination.mkdir()
        (competing_destination / "SKILL.md").write_text(
            "concurrent-existing\n", encoding="utf-8"
        )
        raise FileExistsError(destination)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(global_installer.sys, "platform", "win32"),
        patch.object(global_installer.os, "rename", side_effect=concurrent_windows_rename),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    destination = home_dir / ".codex/skills/use-cafe-workflow/SKILL.md"
    assert summary is not None
    assert summary.installed_count == 0
    assert summary.failed_count == len(DEFAULT_GLOBAL_SKILLS)
    assert destination.read_text(encoding="utf-8") == "concurrent-existing\n"


def test_auto_sync_does_not_roll_back_a_published_tree_after_external_use(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    original_move = global_installer.move_without_replacement
    publish_count = 0

    def fail_second_publish_after_external_use(*args, **kwargs) -> None:
        nonlocal publish_count
        publish_count += 1
        if publish_count == 2:
            first_destination = (
                home_dir / ".codex/skills" / DEFAULT_GLOBAL_SKILLS[0]
            )
            (first_destination / "concurrent.md").write_text(
                "external use\n", encoding="utf-8"
            )
            destination_directory = kwargs["destination_directory"]
            destination = destination_directory.path / args[1]
            destination.mkdir()
            (destination / "SKILL.md").write_text(
                "concurrent-existing\n", encoding="utf-8"
            )
        original_move(*args, **kwargs)

    with (
        patch.object(global_installer, "detect_global_skill_clis", return_value=["codex"]),
        patch.object(
            global_installer,
            "move_without_replacement",
            side_effect=fail_second_publish_after_external_use,
        ),
    ):
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)

    first_destination = home_dir / ".codex/skills" / DEFAULT_GLOBAL_SKILLS[0]
    second_destination = home_dir / ".codex/skills" / DEFAULT_GLOBAL_SKILLS[1]
    assert summary is not None
    assert summary.installed_count == 1
    assert summary.failed_count == len(DEFAULT_GLOBAL_SKILLS) - 1
    assert (first_destination / "SKILL.md").is_file()
    assert (first_destination / "concurrent.md").read_text(encoding="utf-8") == (
        "external use\n"
    )
    assert (second_destination / "SKILL.md").read_text(encoding="utf-8") == (
        "concurrent-existing\n"
    )


def test_auto_sync_skips_quickly_when_another_process_holds_the_lock(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)

    with global_installer._global_skill_sync_lock(
        home_dir,
        timeout_seconds=1,
    ):
        started = monotonic()
        summary = auto_sync_global_skills(source_root=source_root, home_dir=home_dir)
        elapsed = monotonic() - started

    assert summary is None
    assert elapsed < 0.5


def test_auto_sync_lock_contention_is_safe_across_processes(tmp_path: Path) -> None:
    source_root = tmp_path / "bundled-skills"
    home_dir = tmp_path / "home"
    _write_default_sources(source_root)
    codex_config = home_dir / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text("model = 'test'\n", encoding="utf-8")
    code = (
        "from pathlib import Path; import sys; "
        "from cafe.skills.global_installer import auto_sync_global_skills; "
        "print(auto_sync_global_skills(source_root=Path(sys.argv[1]), "
        "home_dir=Path(sys.argv[2])))"
    )

    with global_installer._global_skill_sync_lock(
        home_dir,
        timeout_seconds=1,
    ):
        result = subprocess.run(
            [sys.executable, "-c", code, str(source_root), str(home_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )

    assert result.stdout.strip() == "None"
