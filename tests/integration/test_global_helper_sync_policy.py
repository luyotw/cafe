"""I1-I5 journeys for the global helper publication boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cafe.skills import global_installer
from cafe.skills.global_installer import DEFAULT_GLOBAL_SKILLS, auto_sync_global_skills
from cafe.ui import cli
from cafe.ui.cli import app

REPO_ROOT = Path(__file__).parents[2]
runner = CliRunner()


def _write_skill(source_root: Path, name: str, body: str) -> None:
    skill_dir = source_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _write_default_sources(source_root: Path, version: str) -> None:
    for name in DEFAULT_GLOBAL_SKILLS:
        _write_skill(source_root, name, f"{name} {version}")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_observational_commands_leave_global_state_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    marker = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    marker.parent.mkdir(parents=True)
    marker.write_text("existing\n", encoding="utf-8")
    before = marker.read_bytes()

    with patch("cafe.skills.global_installer.auto_sync_global_skills") as install:
        for argv in (
            ["cafe", "status"],
            ["cafe", "show", "plan"],
            ["cafe", "catalog", "check"],
            ["cafe", "--help"],
            ["cafe", "workflow", "--issue", "issue466"],
        ):
            monkeypatch.setattr(cli.sys, "argv", argv)
            cli._auto_sync_global_helper_skills()

    install.assert_not_called()
    assert marker.read_bytes() == before
    assert not (home / ".cafe").exists()


def test_eligible_startup_installs_only_missing_helpers_from_trusted_source(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "released/cafe/data/skills"
    home = tmp_path / "home"
    _write_default_sources(source, "released")
    existing = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("stale local copy\n", encoding="utf-8")
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])

    with (
        patch.object(global_installer, "_default_source_root", return_value=source),
        patch.object(global_installer, "_default_home_dir", return_value=home),
    ):
        cli._auto_sync_global_helper_skills()

    assert existing.read_text(encoding="utf-8") == "stale local copy\n"
    assert "released" in (
        home / ".codex/skills/write-cafe-agent/SKILL.md"
    ).read_text(encoding="utf-8")


def test_linked_worktrees_resolve_one_canonical_automatic_source(tmp_path: Path) -> None:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    home = tmp_path / "home"
    main.mkdir()
    _git(main, "init")
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test")
    main_source = main / "src/cafe/data/skills"
    _write_default_sources(main_source, "canonical")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "initial")
    _git(main, "worktree", "add", "-b", "feature", str(linked))
    linked_source = linked / "src/cafe/data/skills"
    (linked_source / "use-cafe-workflow/SKILL.md").write_text(
        "---\nname: use-cafe-workflow\ndescription: test\n---\n\nfeature\n",
        encoding="utf-8",
    )

    resolved_main = global_installer._trusted_automatic_source_root(main_source)
    resolved_linked = global_installer._trusted_automatic_source_root(linked_source)
    assert resolved_linked == resolved_main == main_source.resolve()

    (home / ".codex/config.toml").parent.mkdir(parents=True)
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    auto_sync_global_skills(source_root=resolved_linked, home_dir=home)
    installed = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    assert "canonical" in installed.read_text(encoding="utf-8")

    for source in (linked_source, main_source, linked_source):
        auto_sync_global_skills(source_root=source, home_dir=home)
    assert "canonical" in installed.read_text(encoding="utf-8")


def test_explicit_sync_publishes_reported_source_and_clear_outcomes(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "feature/src/cafe/data/skills"
    home = tmp_path / "home"
    _write_default_sources(source, "feature-v1")
    monkeypatch.chdir(tmp_path)

    with (
        patch.object(global_installer, "_default_source_root", return_value=source),
        patch.object(global_installer, "_default_home_dir", return_value=home),
    ):
        installed = runner.invoke(app, ["skill", "sync-global", "--cli", "codex"])
        _write_skill(source, "use-cafe-workflow", "feature-v2")
        updated = runner.invoke(app, ["skill", "sync-global", "--cli", "codex"])
        unchanged = runner.invoke(app, ["skill", "sync-global", "--cli", "codex"])

    assert installed.exit_code == updated.exit_code == unchanged.exit_code == 0
    for result in (installed, updated, unchanged):
        assert f"Source: {source.resolve()}" in result.stdout.replace("\n", "")
    assert "4 installed" in installed.stdout
    assert "1 updated" in updated.stdout
    assert "4 unchanged" in unchanged.stdout


def test_git_lifecycle_has_no_global_helper_publisher(tmp_path: Path) -> None:
    assert not (REPO_ROOT / ".githooks/post-commit").exists()
    assert not (REPO_ROOT / ".githooks/post-merge").exists()
    assert not (REPO_ROOT / "scripts/auto-sync-global-skills.sh").exists()
    assert not (REPO_ROOT / "src/cafe/skills/global_sync_hook.py").exists()
    assert "global helper" not in (REPO_ROOT / "setup-hooks.sh").read_text(
        encoding="utf-8"
    ).lower()

    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.hooksPath", ".githooks")
    (repo / ".githooks").mkdir()
    marker = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    marker.parent.mkdir(parents=True)
    marker.write_text("stable\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("v1\n", encoding="utf-8")

    env = {**os.environ, "HOME": str(home)}
    subprocess.run(
        ["git", "add", "tracked.txt"], cwd=repo, env=env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )

    assert marker.read_text(encoding="utf-8") == "stable\n"
