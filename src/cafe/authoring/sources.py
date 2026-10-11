"""Selective, read-only source views for authoring contract validation."""

import hashlib
from pathlib import Path

from cafe.core.playbook import (
    PlaybookDefinition,
    _resolve_phase_step_defaults,
    iter_declared_playbook_skills,
    normalize_playbook_yaml,
    resolve_playbook_skills,
)
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.skills.selectors import skill_selector_names


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class SourceView:
    """Materialize only declarations/resources needed by this authoring request."""

    def __init__(self, root, destination, sources, result, *, proposed=False):
        self.root = root
        self.destination = destination
        self.sources = sources
        self.result = result
        self.proposed = proposed
        self.loader = SkillLoader(
            project_root=destination,
            global_root=destination / "global",
            builtin_root=destination / "builtin",
            read_only=True,
            resolve_presentation=False,
        )

    def identity(self, requested):
        requested = requested.strip()
        if requested in self.sources:
            return requested
        canonical = canonical_skill_name(requested)
        if canonical in self.sources:
            return canonical
        raise FileNotFoundError(f"Skill not found: {requested}")

    def _copy(self, name, relative):
        source = self.sources[name] / relative
        destination = self.destination / ".cafe/skills" / name / relative
        if not source.resolve().is_relative_to(self.sources[name].resolve()):
            raise ValueError(f"Resource escapes selected skill: {name}/{relative}")
        original = source.read_bytes() if source.is_file() else None
        self.result.dependencies[str(source)] = (
            hashlib.sha256(original).hexdigest() if original is not None else None
        )
        try:
            target = str(source.relative_to(self.root))
        except ValueError:
            target = None
        content = self.result.files.get(target) if self.proposed else None
        if content is not None:
            data = content.encode()
        elif original is not None:
            data = original
        else:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def stage(self, requested, *, resources=True, corpus=False):
        name = self.identity(requested)
        self._copy(name, Path("SKILL.md"))
        if self.proposed:
            for target in self.result.files:
                path = self.root / target
                if path.is_relative_to(self.sources[name]):
                    self._copy(name, path.relative_to(self.sources[name]))
        if resources:
            entry, raw = self.loader.get_workflow_declaration_data(name)
            self.resources(name, self.loader.parse_workflow_declaration(entry, raw))
        if corpus:
            for path in self.sources[name].rglob("*.md"):
                self._copy(name, path.relative_to(self.sources[name]))
        return self.destination / ".cafe/skills" / name

    def resources(self, requested, declaration):
        name = self.identity(requested)
        references = set(declaration.prompt_references.values())
        for checklist in (declaration.checklist, declaration.checklist_overlay):
            if checklist:
                references.update(checklist.context_references.values())
                references.update(
                    section.reference
                    for variant in checklist.variants
                    for section in variant.sections
                    if section.reference
                )
        for reference in sorted(references):
            self._copy(name, Path("references") / reference)
        if declaration.output_templates:
            templates = self.sources[name] / "assets/templates"
            if templates.is_dir():
                (self.destination / ".cafe/skills" / name / "assets/templates").mkdir(
                    parents=True, exist_ok=True
                )
                for path in templates.rglob("*"):
                    if path.is_file():
                        self._copy(name, path.relative_to(self.sources[name]))

    def book(self, raw):
        """Use runtime selectors/defaults and support-skill validation, without catalog scans."""
        raw = normalize_playbook_yaml(raw)
        for step_name, step in raw.get("steps", {}).items():
            for primary in skill_selector_names(step["skill"]):
                self.stage(primary)
            for channel in ("workflow", "chat"):
                for skill in resolve_playbook_skills(
                    raw, channel=channel, role=step.get("role"), step_name=step_name
                ):
                    self.stage(skill, resources=channel == "workflow")
        model = PlaybookDefinition.model_validate(_resolve_phase_step_defaults(raw, self.loader))
        for _, skill in iter_declared_playbook_skills(model):
            self.stage(skill, resources=False)
        return model
