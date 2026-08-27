"""Skill catalog discovery and activation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from cafe.catalogs.resolver import CatalogKind, CatalogResolver
from cafe.skills.contracts import SkillWorkflowContract
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.utils.config import get_global_cafe_dir

_logger = logging.getLogger(__name__)

# Deprecated skill names that resolve to a newer skill. Issued for backward
# compatibility with user playbooks / presets that still reference the old
# names. Builtin workflow skills carry the "cafe-" prefix in their folder
# names since the internal/external skill reorganization; unprefixed names
# remain valid via these aliases. Plan to remove in a future minor release.
_SKILL_ALIASES: Dict[str, str] = {
    "spec_first": "cafe-spec",
    "spec_revise": "cafe-spec",
    "write-skill": "write-cafe-phase",
    "write-cafe-skill": "write-cafe-phase",
    **{
        name: f"cafe-{name}"
        for name in (
            "alignment",
            "brief_first",
            "brief_revise",
            "chat-develop-change",
            "chat-plan-revision",
            "chat-spec-revision",
            "common-chat-handoff",
            "develop",
            "draft",
            "editorial_review",
            "github_sync",
            "incident_detect",
            "incident_mitigate",
            "incident_postmortem",
            "incident_triage",
            "plan",
            "pr",
            "publish",
            "research_collect",
            "research_question",
            "research_report",
            "research_synthesize",
            "review",
            "spec",
            "workflow-common",
        )
    },
}


def canonical_skill_name(name: str) -> str:
    """Map a possibly-deprecated skill name to its canonical name.

    Does not consult the catalog; project/global skills that intentionally
    reuse an old builtin name still win at `get_skill_dir` resolution time.
    """
    return _SKILL_ALIASES.get(name, name)


def read_skill_frontmatter(skill_file: Path) -> Dict[str, object]:
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
        self.resolver = CatalogResolver(
            project_root=project_root,
            global_root=global_root,
            builtin_root=builtin_root,
        )
        self.project_root = self.resolver.project_root
        self.global_root = self.resolver.global_root
        self.builtin_root = self.resolver.builtin_root
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
            (source, root)
            for source, root, _layer in self.resolver.catalog_roots(CatalogKind.PHASE)
        ]

    @staticmethod
    def _read_skill_frontmatter(skill_file: Path) -> Dict[str, object]:
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
                elif source != "builtin" and skill_dir.name in _SKILL_ALIASES:
                    warning = (
                        f"Skill '{skill_dir.name}' uses a deprecated builtin name; "
                        f"rename it to '{_SKILL_ALIASES[skill_dir.name]}' to override the builtin, "
                        "or pick a distinct name"
                    )

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
        return self.get_skill_entry(name).directory

    def get_skill_entry(self, name: str) -> SkillCatalogEntry:
        """Return the resolved skill and its discovery trust source."""
        self._ensure_catalog()
        if name in self._catalog:
            return self._catalog[name]
        resolved = self._resolve_alias(name)
        if resolved is not None and resolved in self._catalog:
            return self._catalog[resolved]
        raise SkillDiscoveryError(name)

    @staticmethod
    def _resolve_alias(name: str) -> Optional[str]:
        target = _SKILL_ALIASES.get(name)
        if target is None:
            return None
        _logger.warning(
            "Skill '%s' is deprecated; resolving to '%s'. Update playbooks/presets to use '%s'.",
            name,
            target,
            target,
        )
        return target

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

    def get_workflow_contract(self, name: str) -> SkillWorkflowContract:
        """Load and validate optional workflow metadata from the resolved skill."""
        skill_dir = self.get_skill_dir(name)
        metadata = self._read_skill_frontmatter(skill_dir / "SKILL.md")
        raw_contract = metadata.get("workflow", {})
        try:
            contract = SkillWorkflowContract.model_validate(raw_contract)
        except Exception as exc:
            raise ValueError(
                f"Invalid workflow contract for skill {skill_dir.name}: {exc}"
            ) from exc
        references = list(contract.prompt_references.values())
        if contract.checklist is not None:
            references.extend(contract.checklist.context_references.values())
            references.extend(
                section.reference
                for variant in contract.checklist.variants
                for section in variant.sections
                if section.reference is not None
            )
        for reference in references:
            reference_path = skill_dir / "references" / reference
            if not reference_path.is_file():
                raise ValueError(
                    f"Invalid workflow contract for skill {skill_dir.name}: "
                    f"workflow reference not found: {reference}"
                )
        if contract.output_templates is not None:
            template_dir = skill_dir / "assets" / "templates"
            if not template_dir.is_dir():
                raise ValueError(
                    f"Invalid workflow contract for skill {skill_dir.name}: "
                    f"template catalog {contract.output_templates.catalog!r} is unavailable"
                )
        return contract

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
