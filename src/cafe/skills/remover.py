"""Skill removal helpers for project-scoped custom skills."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SkillRemoveResult:
    """One removed, skipped, or failed skill item."""

    name: str
    destination: Path
    status: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class SkillRemoveSummary:
    """Aggregated skill removal results."""

    destination_root: Path
    results: list[SkillRemoveResult]

    @property
    def removed_count(self) -> int:
        return sum(1 for item in self.results if item.status == "removed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")


def _remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def remove_skills(
    names: list[str],
    project_root: Path,
) -> SkillRemoveSummary:
    """Remove one or more project skill folders from `.cafe/skills`."""
    project_root = project_root.expanduser().resolve()
    destination_root = project_root / ".cafe" / "skills"

    results: list[SkillRemoveResult] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)

        destination = destination_root / name
        if not destination.exists() and not destination.is_symlink():
            results.append(
                SkillRemoveResult(
                    name=name,
                    destination=destination,
                    status="skipped",
                    reason="not found",
                )
            )
            continue

        try:
            _remove_existing_path(destination)
            results.append(
                SkillRemoveResult(
                    name=name,
                    destination=destination,
                    status="removed",
                )
            )
        except Exception as exc:
            results.append(
                SkillRemoveResult(
                    name=name,
                    destination=destination,
                    status="failed",
                    reason=str(exc),
                )
            )

    return SkillRemoveSummary(
        destination_root=destination_root,
        results=results,
    )
