"""Tests for Git-triggered bundled global skill synchronization."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "auto-sync-global-skills.sh"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_auto_sync_delegates_to_the_minimal_hook_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$CAFE_GLOBAL_SYNC_LOG"\n'
        'printf "%s\\n" "$PYTHONPATH" >> "$CAFE_GLOBAL_SYNC_ENV_LOG"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    sync_log = tmp_path / "sync.log"
    env_log = tmp_path / "env.log"
    env = {
        **os.environ,
        "CAFE_GLOBAL_SYNC_PYTHON": str(fake_python),
        "CAFE_GLOBAL_SYNC_LOG": str(sync_log),
        "CAFE_GLOBAL_SYNC_ENV_LOG": str(env_log),
    }

    subprocess.run([str(SYNC_SCRIPT)], cwd=repo, env=env, check=True)

    assert sync_log.read_text(encoding="utf-8").strip() == "-m cafe.skills.global_sync_hook"
    assert str(repo / "src") in env_log.read_text(encoding="utf-8")


def test_auto_sync_hooks_are_valid_and_delegate_to_the_runner() -> None:
    hooks = ("post-commit", "post-merge")

    subprocess.run(["bash", "-n", str(SYNC_SCRIPT)], check=True)
    for name in hooks:
        hook = REPO_ROOT / ".githooks" / name
        subprocess.run(["bash", "-n", str(hook)], check=True)
        content = hook.read_text(encoding="utf-8")
        assert "auto-sync-global-skills.sh" in content
        assert "HEAD^ HEAD" not in content
        assert "ORIG_HEAD HEAD" not in content


def test_post_commit_runs_sync_after_amend(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.hooksPath", ".githooks")

    hook = repo / ".githooks/post-commit"
    script = repo / "scripts/auto-sync-global-skills.sh"
    hook.parent.mkdir()
    script.parent.mkdir()
    shutil.copy2(REPO_ROOT / ".githooks/post-commit", hook)
    shutil.copy2(SYNC_SCRIPT, script)

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CAFE_GLOBAL_SYNC_LOG"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    sync_log = tmp_path / "sync.log"
    env = {
        **os.environ,
        "CAFE_GLOBAL_SYNC_PYTHON": str(fake_python),
        "CAFE_GLOBAL_SYNC_LOG": str(sync_log),
    }

    tracked = repo / "skill.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "skill.txt")
    _git(repo, "commit", "-m", "initial", env=env)
    tracked.write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "skill.txt")
    _git(repo, "commit", "--amend", "--no-edit", env=env)

    assert sync_log.read_text(encoding="utf-8").splitlines() == [
        "-m cafe.skills.global_sync_hook",
        "-m cafe.skills.global_sync_hook",
    ]


def test_linked_worktree_uses_the_main_worktree_virtualenv(tmp_path: Path) -> None:
    repo = tmp_path / "main"
    linked = tmp_path / "linked"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    script = repo / "scripts/auto-sync-global-skills.sh"
    script.parent.mkdir()
    shutil.copy2(SYNC_SCRIPT, script)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "worktree", "add", "-b", "linked-test", str(linked))

    main_python = repo / ".venv/bin/python"
    main_python.parent.mkdir(parents=True)
    main_python.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$0" >> "$CAFE_GLOBAL_SYNC_PYTHON_LOG"\n'
        'printf "%s\\n" "$PYTHONPATH" >> "$CAFE_GLOBAL_SYNC_ENV_LOG"\n',
        encoding="utf-8",
    )
    main_python.chmod(0o755)
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"
    env = {
        **os.environ,
        "CAFE_GLOBAL_SYNC_PYTHON_LOG": str(python_log),
        "CAFE_GLOBAL_SYNC_ENV_LOG": str(env_log),
    }

    subprocess.run(
        [str(linked / "scripts/auto-sync-global-skills.sh")],
        cwd=linked,
        env=env,
        check=True,
    )

    assert not (linked / ".venv").exists()
    assert python_log.read_text(encoding="utf-8").strip() == str(main_python)
    assert str(linked / "src") in env_log.read_text(encoding="utf-8")
