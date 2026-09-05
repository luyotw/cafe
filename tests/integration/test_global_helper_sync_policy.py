"""I1-I5 journeys for the global helper publication boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from typer.testing import CliRunner

from cafe.skills import global_installer
from cafe.skills.global_installer import DEFAULT_GLOBAL_SKILLS
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


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_observational_commands_leave_global_state_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "released/cafe/data/skills"
    home = tmp_path / "home"
    _write_default_sources(source, "released")
    marker = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    marker.parent.mkdir(parents=True)
    marker.write_text("existing\n", encoding="utf-8")
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    before = marker.read_bytes()
    dispatched: list[list[str]] = []
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)

    with (
        patch.object(global_installer, "_default_source_root", return_value=source),
        patch.object(global_installer, "_default_home_dir", return_value=home),
        patch.object(cli, "_check_dependencies"),
        patch.object(cli, "_check_repo_entrypoint_alignment", return_value=True),
        patch.object(cli, "app", side_effect=lambda: dispatched.append(cli.sys.argv[1:])),
    ):
        for argv in (
            ["cafe", "status"],
            ["cafe", "show", "plan"],
            ["cafe", "catalog", "check"],
            ["cafe", "--help"],
            ["cafe", "workflow", "--issue", "issue466"],
        ):
            monkeypatch.setattr(cli.sys, "argv", argv)
            assert cli.main() is None

    assert len(dispatched) == 5
    assert marker.read_bytes() == before
    assert not (home / ".codex/skills/write-cafe-agent").exists()
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
    dispatched: list[bool] = []

    with (
        patch.object(global_installer, "_default_source_root", return_value=source),
        patch.object(global_installer, "_default_home_dir", return_value=home),
        patch.object(cli, "_check_dependencies"),
        patch.object(cli, "_check_repo_entrypoint_alignment", return_value=True),
        patch.object(cli, "app", side_effect=lambda: dispatched.append(True)),
    ):
        assert cli.main() is None

    assert dispatched == [True]
    assert existing.read_text(encoding="utf-8") == "stale local copy\n"
    assert "released" in (
        home / ".codex/skills/write-cafe-agent/SKILL.md"
    ).read_text(encoding="utf-8")


def test_unavailable_automatic_source_is_bounded_and_does_not_block_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    checkout = tmp_path / "checkout"
    source = checkout / "src/cafe/data/skills"
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    _write_default_sources(source, "feature")
    (checkout / ".git").mkdir(parents=True)
    (home / ".codex/config.toml").parent.mkdir(parents=True)
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexec sleep 5\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])
    monkeypatch.setattr(
        global_installer, "AUTOMATIC_GIT_DISCOVERY_TIMEOUT_SECONDS", 0.05
    )
    dispatched: list[bool] = []

    with (
        patch.object(global_installer, "_default_source_root", return_value=source),
        patch.object(global_installer, "_default_home_dir", return_value=home),
        patch.object(cli, "_check_dependencies"),
        patch.object(cli, "_check_repo_entrypoint_alignment", return_value=True),
        patch.object(cli, "app", side_effect=lambda: dispatched.append(True)),
    ):
        started = monotonic()
        assert cli.main() is None
        elapsed = monotonic() - started

    assert elapsed < 0.5
    assert dispatched == [True]
    assert not (home / ".codex/skills/use-cafe-workflow").exists()


def test_linked_worktrees_use_canonical_source_through_real_startup(
    tmp_path: Path, monkeypatch
) -> None:
    main = tmp_path / "main"
    linked_a = tmp_path / "linked-a"
    linked_b = tmp_path / "linked-b"
    home = tmp_path / "home"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test")
    main_source = main / "src/cafe/data/skills"
    _write_default_sources(main_source, "canonical")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "initial")
    _git(main, "worktree", "add", "-b", "feature-a", str(linked_a))
    _git(main, "worktree", "add", "-b", "feature-b", str(linked_b))
    linked_a_source = linked_a / "src/cafe/data/skills"
    linked_b_source = linked_b / "src/cafe/data/skills"
    _write_skill(linked_a_source, "use-cafe-workflow", "feature-a committed")
    _git(linked_a, "add", ".")
    _git(linked_a, "commit", "-m", "feature-a")
    _write_skill(linked_b_source, "use-cafe-workflow", "feature-b uncommitted")

    (home / ".codex/config.toml").parent.mkdir(parents=True)
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])

    def run_startup(source: Path) -> None:
        with (
            patch.object(global_installer, "_default_source_root", return_value=source),
            patch.object(global_installer, "_default_home_dir", return_value=home),
            patch.object(
                global_installer, "detect_global_skill_clis", return_value=["codex"]
            ),
        ):
            cli._auto_sync_global_helper_skills()

    run_startup(linked_a_source)
    installed = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    assert "canonical" in installed.read_text(encoding="utf-8")

    shutil.rmtree(home / ".codex/skills/write-cafe-agent")
    run_startup(linked_b_source)
    assert "canonical" in (
        home / ".codex/skills/write-cafe-agent/SKILL.md"
    ).read_text(encoding="utf-8")

    for source in (linked_a_source, main_source, linked_b_source):
        run_startup(source)
    assert "canonical" in installed.read_text(encoding="utf-8")


def test_separate_git_dir_linked_worktree_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    common_git = tmp_path / "repository.git"
    home = tmp_path / "home"
    subprocess.run(
        [
            "git",
            "init",
            "-b",
            "main",
            f"--separate-git-dir={common_git}",
            str(main),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test")
    main_source = main / "src/cafe/data/skills"
    _write_default_sources(main_source, "canonical")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "initial")
    _git(main, "worktree", "add", "-b", "feature", str(linked))
    linked_source = linked / "src/cafe/data/skills"
    _write_skill(linked_source, "use-cafe-workflow", "feature-uncommitted")
    (home / ".codex/config.toml").parent.mkdir(parents=True)
    (home / ".codex/config.toml").write_text("model = 'test'\n", encoding="utf-8")
    monkeypatch.delenv("CAFE_SKIP_GLOBAL_SKILL_SYNC", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["cafe", "prepare"])
    dispatched: list[bool] = []

    with (
        patch.object(
            global_installer, "_default_source_root", return_value=linked_source
        ),
        patch.object(global_installer, "_default_home_dir", return_value=home),
        patch.object(
            global_installer, "detect_global_skill_clis", return_value=["codex"]
        ),
        patch.object(cli, "_check_dependencies"),
        patch.object(cli, "_check_repo_entrypoint_alignment", return_value=True),
        patch.object(cli, "app", side_effect=lambda: dispatched.append(True)),
    ):
        assert cli.main() is None

    assert dispatched == [True]
    assert not (home / ".codex/skills/use-cafe-workflow").exists()


def test_explicit_sync_publishes_reported_source_and_clear_outcomes(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "[red]feature[/red]/src/cafe/data/skills"
    home = tmp_path / "[blue]home[/blue]"
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
        assert str(home.resolve()) in result.stdout.replace("\n", "")
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
    assert os.access(REPO_ROOT / ".githooks/pre-commit", os.X_OK)
    assert os.access(REPO_ROOT / ".githooks/pre-push", os.X_OK)

    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.hooksPath", ".githooks")
    shutil.copytree(REPO_ROOT / ".githooks", repo / ".githooks")
    hook_python = repo / ".venv/bin/python"
    hook_python.parent.mkdir(parents=True)
    hook_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOOK_PYTHON_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    hook_python.chmod(0o755)
    hook_log = tmp_path / "hook-python.log"
    marker = home / ".codex/skills/use-cafe-workflow/SKILL.md"
    marker.parent.mkdir(parents=True)
    marker.write_text("stable\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("v1\n", encoding="utf-8")

    env = {**os.environ, "HOME": str(home), "HOOK_PYTHON_LOG": str(hook_log)}
    _git(repo, "add", "tracked.txt", env=env)
    _git(repo, "commit", "-m", "initial", env=env)
    (repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", env=env)
    _git(repo, "commit", "--amend", "--no-edit", env=env)
    _git(repo, "checkout", "-b", "feature", env=env)
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt", env=env)
    _git(repo, "commit", "-m", "feature", env=env)
    _git(repo, "checkout", "main", env=env)
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature", env=env)

    assert marker.read_text(encoding="utf-8") == "stable\n"
    assert len(hook_log.read_text(encoding="utf-8").splitlines()) >= 6
