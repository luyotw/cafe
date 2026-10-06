"""Skill catalog discovery and activation."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from cafe.catalogs.resolver import (
    CatalogEntry,
    CatalogKind,
    CatalogResolver,
    ProjectRoots,
    global_catalog_lock,
)
from cafe.core.runtime_locales import owner_catalog_renderer
from cafe.skills.contracts import SkillWorkflowDeclaration
from cafe.skills.exceptions import SkillDiscoveryError
from cafe.utils.yaml_utils import safe_load

_logger = logging.getLogger(__name__)


def workflow_locale_context(
    skill_root: Path, *, resolve_presentation: bool
) -> dict[str, object]:
    """Give one declaration operation its own lazy owner-local renderer."""
    root = skill_root.resolve() / "locales"
    return {
        "locale_catalog_root": root,
        "resolve_presentation": resolve_presentation,
        "render_locale_text": owner_catalog_renderer(root),
    }


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
    """Read YAML frontmatter without materializing the skill body."""
    with skill_file.open(encoding="utf-8") as handle:
        if handle.readline().rstrip("\r\n") != "---":
            return {}
        frontmatter_lines: list[str] = []
        for line in handle:
            if line.rstrip("\r\n") == "---":
                break
            frontmatter_lines.append(line)
        else:
            return {}
    frontmatter = "".join(frontmatter_lines)
    if not frontmatter.strip():
        return {}
    # Read the current header on every call. Cache only parsing by exact content,
    # never by path, so same-path publications and overrides remain fresh.
    if len(frontmatter) <= 32 * 1024:
        return deepcopy(_parse_frontmatter(frontmatter))
    return _parse_frontmatter.__wrapped__(frontmatter)


@lru_cache(maxsize=128)
def _parse_frontmatter(frontmatter: str) -> Dict[str, object]:
    data = safe_load(frontmatter) or {}
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
        resolve_presentation: bool = True,
        read_only: bool = False,
        project_roots: Optional[ProjectRoots] = None,
    ) -> None:
        self.resolver = CatalogResolver(
            project_root=project_root,
            global_root=global_root,
            builtin_root=builtin_root,
            read_only=read_only,
            project_roots=project_roots,
        )
        self.read_only = read_only
        self.project_root = self.resolver.project_root
        self.global_root = self.resolver.global_root
        self.builtin_root = self.resolver.builtin_root
        self.resolve_presentation = resolve_presentation
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
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            return self._discover_unlocked(strict=strict)

    def _discover_unlocked(self, *, strict: bool = False) -> List[SkillCatalogEntry]:
        catalog: Dict[str, SkillCatalogEntry] = {}
        for resolved in self.resolver.entries([CatalogKind.PHASE]):
            entry = self._entry_from_resolved(resolved, strict=strict)
            catalog[entry.name] = entry

        self._catalog = catalog
        return sorted(catalog.values(), key=lambda item: item.name)

    def _entry_from_resolved(
        self, resolved: CatalogEntry, *, strict: bool = False
    ) -> SkillCatalogEntry:
        skill_dir = resolved.path
        metadata = self._read_skill_frontmatter(skill_dir / "SKILL.md")
        name = str(metadata.get("name", skill_dir.name))
        description = str(metadata.get("description", "")).strip()
        warning = None

        if name != skill_dir.name:
            mismatch = f"Skill frontmatter name '{name}' does not match folder '{skill_dir.name}'"
            if resolved.source == "builtin" or strict:
                raise ValueError(mismatch)
            warning = mismatch
        elif resolved.source != "builtin" and skill_dir.name in _SKILL_ALIASES:
            warning = (
                f"Skill '{skill_dir.name}' uses a deprecated builtin name; "
                f"rename it to '{_SKILL_ALIASES[skill_dir.name]}' to override the builtin, "
                "or pick a distinct name"
            )

        return SkillCatalogEntry(
            name=skill_dir.name,
            description=description,
            directory=skill_dir,
            source=resolved.source,
            warning=warning,
        )

    def _resolve_entry(self, name: str) -> SkillCatalogEntry:
        try:
            resolved = self.resolver.resolve(CatalogKind.PHASE, name)
        except FileNotFoundError:
            canonical_name = self._resolve_alias(name)
            if canonical_name is None:
                raise SkillDiscoveryError(name) from None
            try:
                resolved = self.resolver.resolve(CatalogKind.PHASE, canonical_name)
            except FileNotFoundError:
                raise SkillDiscoveryError(name) from None
        return self._entry_from_resolved(resolved)

    def get_skill_dir(self, name: str) -> Path:
        return self.get_skill_entry(name).directory

    def get_skill_entry(self, name: str) -> SkillCatalogEntry:
        """Return the resolved skill and its discovery trust source."""
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            return self._resolve_entry(name)

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
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            skill_dir = self._resolve_entry(name).directory
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

    def get_workflow_declaration(self, name: str) -> SkillWorkflowDeclaration:
        """Load and validate optional workflow metadata from the resolved skill."""
        _entry, declaration = self.get_workflow_declaration_entry(name)
        return declaration

    def get_workflow_declaration_entry(
        self, name: str, *, validate_resources: bool = True
    ) -> tuple[SkillCatalogEntry, SkillWorkflowDeclaration]:
        """Return a declaration with the exact catalog entry that supplied it."""
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            entry, raw_declaration = self.get_workflow_declaration_data(name)
            declaration = self.parse_workflow_declaration(entry, raw_declaration)
            if validate_resources:
                self.validate_workflow_declaration_resources(entry.directory, declaration)
            return entry, declaration

    def get_workflow_declaration_data(
        self, name: str
    ) -> tuple[SkillCatalogEntry, object]:
        """Return resolved provenance and raw workflow metadata without validating it."""
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            entry = self._resolve_entry(name)
            metadata = self._read_skill_frontmatter(entry.directory / "SKILL.md")
            return entry, metadata.get("workflow", {})

    def parse_workflow_declaration(
        self, entry: SkillCatalogEntry, raw_declaration: object
    ) -> SkillWorkflowDeclaration:
        """Preserve the compatibility error used by direct and primary loading."""
        try:
            return SkillWorkflowDeclaration.model_validate(
                raw_declaration,
                context=workflow_locale_context(
                    entry.directory, resolve_presentation=self.resolve_presentation
                ),
            )
        except Exception as exc:
            raise ValueError(
                f"Invalid workflow declaration for skill {entry.directory.name}: {exc}"
            ) from exc

    @staticmethod
    def workflow_declaration_resource_errors(
        skill_dir: Path,
        declaration: SkillWorkflowDeclaration,
        *,
        fields: Optional[set[str]] = None,
    ) -> tuple[str, ...]:
        """Return bounded resource errors for selected declaration fields."""
        selected = fields or {
            "prompt_references",
            "checklist",
            "checklist_overlay",
            "output_templates",
        }
        errors: list[str] = []
        references: list[str] = []
        if "prompt_references" in selected:
            references.extend(declaration.prompt_references.values())
        for field in ("checklist", "checklist_overlay"):
            checklist = getattr(declaration, field)
            if field not in selected or checklist is None:
                continue
            for key, reference in checklist.context_references.items():
                if not (skill_dir / "references" / reference).is_file():
                    errors.append(
                        f"{field}.context_references.{key}: "
                        f"workflow reference not found: {reference}"
                    )
            for index, variant in enumerate(checklist.variants):
                for position, section in enumerate(variant.sections):
                    if (
                        section.reference
                        and not (skill_dir / "references" / section.reference).is_file()
                    ):
                        errors.append(
                            f"{field}.variants[{index}].sections[{position}].reference: "
                            f"workflow reference not found: {section.reference}"
                        )
        errors.extend(
            f"workflow reference not found: {reference}"
            for reference in references
            if not (skill_dir / "references" / reference).is_file()
        )
        if (
            "output_templates" in selected
            and declaration.output_templates is not None
            and not (skill_dir / "assets" / "templates").is_dir()
        ):
            errors.append(
                f"template catalog {declaration.output_templates.catalog!r} is unavailable"
            )
        return tuple(errors)

    @classmethod
    def validate_workflow_declaration_resources(
        cls,
        skill_dir: Path,
        declaration: SkillWorkflowDeclaration,
    ) -> None:
        """Preserve generic declaration validation for primary and supported fields."""
        errors = cls.workflow_declaration_resource_errors(skill_dir, declaration)
        if errors:
            raise ValueError(
                f"Invalid workflow declaration for skill {skill_dir.name}: {errors[0]}"
            )

    # TODO: remove me
    def get_workflow_contract(self, name: str) -> SkillWorkflowDeclaration:
        """Load a workflow declaration through the compatibility API."""
        return self.get_workflow_declaration(name)

    def get_reference(self, name: str, ref: str) -> str:
        """Read one reference file under skill references directory."""
        with global_catalog_lock(self.global_root, read_only=self.read_only):
            skill_dir = self._resolve_entry(name).directory
            ref_file = (skill_dir / "references" / ref).resolve()
            refs_dir = (skill_dir / "references").resolve()
            if not str(ref_file).startswith(str(refs_dir)):
                raise ValueError("Reference path must stay inside references directory")
            if not ref_file.exists():
                raise FileNotFoundError(f"Reference not found: {ref}")
            return ref_file.read_text(encoding="utf-8")
