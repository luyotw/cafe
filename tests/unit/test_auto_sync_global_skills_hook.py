"""Tests for Git-triggered bundled global skill synchronization."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from cafe.skills.global_installer import DEFAULT_GLOBAL_SKILLS

REPO_ROOT = Path(__file__).parents[2]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "auto-sync-global-skills.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def test_auto_sync_runs_only_when_default_global_skill_sources_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    tracked_skill = repo / "src/cafe/data/skills/use-cafe-workflow/SKILL.md"
    tracked_skill.parent.mkdir(parents=True)
    tracked_skill.write_text("version 1\n", encoding="utf-8")
    _commit_all(repo, "initial")

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

    unrelated = repo / "README.md"
    unrelated.write_text("unrelated\n", encoding="utf-8")
    _commit_all(repo, "unrelated")
    subprocess.run([str(SYNC_SCRIPT)], cwd=repo, env=env, check=True)
    assert not sync_log.exists()

    tracked_skill.write_text("version 2\n", encoding="utf-8")
    _commit_all(repo, "skill update")
    subprocess.run([str(SYNC_SCRIPT)], cwd=repo, env=env, check=True)

    assert sync_log.read_text(encoding="utf-8").strip() == "-m cafe.ui.cli skill sync-global"
    assert str(repo / "src") in env_log.read_text(encoding="utf-8")


def test_auto_sync_hooks_are_valid_and_delegate_to_change_detector() -> None:
    hooks = {
        "post-commit": "HEAD^ HEAD",
        "post-merge": "ORIG_HEAD HEAD",
    }

    subprocess.run(["bash", "-n", str(SYNC_SCRIPT)], check=True)
    for name, revisions in hooks.items():
        hook = REPO_ROOT / ".githooks" / name
        subprocess.run(["bash", "-n", str(hook)], check=True)
        content = hook.read_text(encoding="utf-8")
        assert "auto-sync-global-skills.sh" in content
        assert revisions in content


def test_hook_change_detector_tracks_the_installer_default_skill_set() -> None:
    content = SYNC_SCRIPT.read_text(encoding="utf-8")
    tracked_skills = set(re.findall(r"src/cafe/data/skills/([^/]+)/", content))

    assert tracked_skills == set(DEFAULT_GLOBAL_SKILLS)
