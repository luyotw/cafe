"""Workspace helpers for exposing project skills to agent CLIs."""

from __future__ import annotations

import os
from pathlib import Path


def _ensure_project_skill_link(project_root: Path, cli_dir_name: str) -> None:
    """Expose project skills to one CLI workspace via `<cli_dir>/skills` symlink."""
    project_root = project_root.expanduser().resolve()
    source_dir = project_root / ".cafe" / "skills"
    if not source_dir.is_dir():
        return

    cli_dir = project_root / cli_dir_name
    link_path = cli_dir / "skills"
    cli_dir.mkdir(parents=True, exist_ok=True)
    expected_target = Path(os.path.relpath(source_dir, cli_dir))

    if link_path.is_symlink():
        current_target = Path(os.readlink(link_path))
        if current_target == expected_target:
            return
        link_path.unlink()
    elif link_path.exists():
        return

    link_path.symlink_to(expected_target)


def ensure_claude_project_skills(project_root: Path) -> None:
    """Expose project skills to Claude via `.claude/skills` symlink when available."""
    _ensure_project_skill_link(project_root, ".claude")


def ensure_gemini_project_skills(project_root: Path) -> None:
    """Expose project skills to Gemini via `.gemini/skills` symlink when available."""
    _ensure_project_skill_link(project_root, ".gemini")
