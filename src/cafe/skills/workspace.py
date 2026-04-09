"""Workspace helpers for exposing project skills to agent CLIs."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_claude_project_skills(project_root: Path) -> None:
    """Expose project skills to Claude via `.claude/skills` symlink when available."""
    project_root = project_root.expanduser().resolve()
    source_dir = project_root / ".cafe" / "skills"
    if not source_dir.is_dir():
        return

    claude_dir = project_root / ".claude"
    link_path = claude_dir / "skills"
    claude_dir.mkdir(parents=True, exist_ok=True)
    expected_target = Path(os.path.relpath(source_dir, claude_dir))

    if link_path.is_symlink():
        current_target = Path(os.readlink(link_path))
        if current_target == expected_target:
            return
        link_path.unlink()
    elif link_path.exists():
        return

    link_path.symlink_to(expected_target)
