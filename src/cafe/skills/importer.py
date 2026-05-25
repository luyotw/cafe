"""Skill import helpers for project-scoped custom skills."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from cafe.skills.loader import read_skill_frontmatter


@dataclass(frozen=True)
class SkillImportResult:
    """One imported, skipped, or failed skill item."""

    name: str
    source: Path
    status: str
    destination: Optional[Path] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class SkillImportSummary:
    """Aggregated skill import results."""

    source_root: Path
    destination_root: Path
    results: list[SkillImportResult]

    @property
    def imported_count(self) -> int:
        return sum(1 for item in self.results if item.status == "imported")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")


def _iter_skill_candidates(source_root: Path) -> Iterable[Path]:
    if (source_root / "SKILL.md").is_file():
        return [source_root]
    return sorted(item for item in source_root.iterdir() if item.is_dir())


def _remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def _validate_candidate(candidate: Path) -> Optional[str]:
    skill_name = candidate.name
    skill_file = candidate / "SKILL.md"

    if not skill_file.is_file():
        return "missing SKILL.md"

    metadata = read_skill_frontmatter(skill_file)
    frontmatter_name = str(metadata.get("name", skill_name))
    if frontmatter_name != skill_name:
        return "frontmatter name does not match folder name"

    return None


def preview_importable_skills(source_root: Path) -> list[str]:
    """Return discovered skill folder names from a source path for preview."""
    source_root = source_root.expanduser().resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"Skill source path not found: {source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"Skill source path is not a directory: {source_root}")

    candidates = list(_iter_skill_candidates(source_root))
    if not candidates:
        raise ValueError(f"No skill folders found under: {source_root}")

    return [candidate.name for candidate in candidates]


def import_skills(
    source_root: Path,
    project_root: Path,
    *,
    overwrite_decider: Optional[Callable[[str, Path], bool]] = None,
) -> SkillImportSummary:
    """Import one or more skill folders into the project `.cafe/skills` directory."""
    source_root = source_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"Skill source path not found: {source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"Skill source path is not a directory: {source_root}")

    destination_root = project_root / ".cafe" / "skills"
    destination_root.mkdir(parents=True, exist_ok=True)

    results: list[SkillImportResult] = []
    candidates = list(_iter_skill_candidates(source_root))
    if not candidates:
        raise ValueError(f"No skill folders found under: {source_root}")

    for candidate in candidates:
        skill_name = candidate.name
        destination = destination_root / skill_name

        validation_error = _validate_candidate(candidate)
        if validation_error is not None:
            results.append(
                SkillImportResult(
                    name=skill_name,
                    source=candidate,
                    destination=destination,
                    status="skipped",
                    reason=validation_error,
                )
            )
            continue

        if destination.exists() or destination.is_symlink():
            should_overwrite = overwrite_decider(skill_name, destination) if overwrite_decider else False
            if not should_overwrite:
                results.append(
                    SkillImportResult(
                        name=skill_name,
                        source=candidate,
                        destination=destination,
                        status="skipped",
                        reason="already exists",
                    )
                )
                continue
            _remove_existing_path(destination)
            overwrite_reason = "overwritten"
        else:
            overwrite_reason = None

        try:
            shutil.copytree(candidate, destination, symlinks=True)
            results.append(
                SkillImportResult(
                    name=skill_name,
                    source=candidate,
                    destination=destination,
                    status="imported",
                    reason=overwrite_reason,
                )
            )
        except Exception as exc:
            results.append(
                SkillImportResult(
                    name=skill_name,
                    source=candidate,
                    destination=destination,
                    status="failed",
                    reason=str(exc),
                )
            )

    if not results or all(item.status != "imported" for item in results) and all(
        item.status == "skipped" and item.reason == "missing SKILL.md" for item in results
    ):
        raise ValueError(f"No valid skill folders found under: {source_root}")

    return SkillImportSummary(
        source_root=source_root,
        destination_root=destination_root,
        results=results,
    )
