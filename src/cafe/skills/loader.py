"""Skill catalog discovery and activation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from cafe.skills.exceptions import SkillDiscoveryError
from cafe.utils.config import get_global_cafe_dir


def read_skill_frontmatter(skill_file: Path) -> Dict[str, str]:
    """Read YAML frontmatter from one skill file."""
    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    frontmatter = content[3:end]
    data = yaml.safe_load(frontmatter) or {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class SkillCatalogEntry:
    """Catalog metadata for a skill."""

    name: str
    description: str
    directory: Path
    source: str
    warning: Optional[str] = None


class SkillLoader:
    """Load skills with project/global/builtin precedence."""

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        global_root: Optional[Path] = None,
        builtin_root: Optional[Path] = None,
    ) -> None:
        self.project_root = project_root or self._find_project_root(Path.cwd())
        self.global_root = global_root or get_global_cafe_dir()
        self.builtin_root = builtin_root or (Path(__file__).parent.parent / "data")
        self._catalog: Dict[str, SkillCatalogEntry] = {}

    @staticmethod
    def _find_project_root(start: Path) -> Path:
        current = start.resolve()
        while current != current.parent:
            if (current / ".cafe").exists():
                return current
            current = current.parent
        return start.resolve()

    def _skill_roots(self) -> List[tuple[str, Path]]:
        return [
            ("builtin", self.builtin_root / "skills"),
            ("global", self.global_root / "skills"),
            ("project", self.project_root / ".cafe" / "skills"),
        ]

    @staticmethod
    def _read_skill_frontmatter(skill_file: Path) -> Dict[str, str]:
        return read_skill_frontmatter(skill_file)

    def discover(self, *, strict: bool = False) -> List[SkillCatalogEntry]:
        """Discover catalog entries and cache by lookup key (folder name)."""
        catalog: Dict[str, SkillCatalogEntry] = {}
        for source, root in self._skill_roots():
            if not root.exists():
                continue

            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue

                metadata = self._read_skill_frontmatter(skill_file)
                name = str(metadata.get("name", skill_dir.name))
                description = str(metadata.get("description", "")).strip()
                warning = None

                if name != skill_dir.name:
                    mismatch = (
                        f"Skill frontmatter name '{name}' does not match folder '{skill_dir.name}'"
                    )
                    if source == "builtin" or strict:
                        raise ValueError(mismatch)
                    warning = mismatch

                catalog[skill_dir.name] = SkillCatalogEntry(
                    name=skill_dir.name,
                    description=description,
                    directory=skill_dir,
                    source=source,
                    warning=warning,
                )

        self._catalog = catalog
        return sorted(catalog.values(), key=lambda item: item.name)

    def _ensure_catalog(self) -> None:
        if not self._catalog:
            self.discover()

    def get_skill_dir(self, name: str) -> Path:
        self._ensure_catalog()
        if name not in self._catalog:
            raise SkillDiscoveryError(name)
        return self._catalog[name].directory

    def activate(self, name: str, context: Optional[Dict[str, str]] = None) -> str:
        """Load full skill content and replace placeholders."""
        skill_dir = self.get_skill_dir(name)
        skill_file = skill_dir / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")

        # Remove frontmatter; activation stage only needs body instructions.
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + len("\n---") :].lstrip()

        context = context or {}
        for key, value in context.items():
            text = text.replace(f"{{{key}}}", str(value))
        return text

    def get_reference(self, name: str, ref: str) -> str:
        """Read one reference file under skill references directory."""
        skill_dir = self.get_skill_dir(name)
        ref_file = (skill_dir / "references" / ref).resolve()
        refs_dir = (skill_dir / "references").resolve()
        if not str(ref_file).startswith(str(refs_dir)):
            raise ValueError("Reference path must stay inside references directory")
        if not ref_file.exists():
            raise FileNotFoundError(f"Reference not found: {ref}")
        return ref_file.read_text(encoding="utf-8")
