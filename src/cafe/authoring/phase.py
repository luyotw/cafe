"""Canonical phase scaffolding and narrowly scoped phase authoring guards."""

import re
from pathlib import PurePosixPath

from cafe.skills.contracts import RUNTIME_OWNED_PROMPT_PLACEHOLDERS, SkillWorkflowDeclaration

from .requests import decode_request
from .source_edits import dump, edit_section, edit_yaml


def frontmatter(text):
    match = re.match(r"\A---\r?\n(.*?)^---\s*\n", text, re.S | re.M)
    if not match:
        raise ValueError("Phase requires complete YAML frontmatter")
    return match, decode_request(match.group(1))


def guard(text, name, resources):
    match, metadata = frontmatter(text)
    if set(metadata) - {"name", "description", "version", "workflow"}:
        raise ValueError("Unsupported phase frontmatter metadata")
    if metadata.get("name") != name or not name.startswith("cafe-"):
        raise ValueError("Phase name must match its canonical cafe- directory")
    if not metadata.get("description") or not metadata.get("version"):
        raise ValueError("Phase requires nonempty description and version")
    declaration = SkillWorkflowDeclaration.model_validate(metadata.get("workflow", {}))
    if declaration.execution_profile is None:
        raise ValueError("Authoring requires explicit workflow.execution_profile")
    body = text[match.end() :]
    for section in ("Role", "Instructions", "Output", "Handoff"):
        headings = re.findall(rf"^## {section}\s*$", body, re.M)
        if len(headings) != 1:
            raise ValueError(f"Phase requires one canonical {section} section")
        content = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        if content is None or not content.group(1).strip():
            raise ValueError(f"Phase requires author-owned {section} content")
    if "Read your agent file: {agent_file}" not in body or "{output_file}" not in body:
        raise ValueError("Canonical role and output path instructions are required")
    allowed = set(RUNTIME_OWNED_PROMPT_PLACEHOLDERS)
    allowed.update(i.placeholder for i in declaration.prompt_inputs)
    allowed.update(declaration.prompt_references)
    for checklist in (declaration.checklist, declaration.checklist_overlay):
        if checklist:
            allowed.update(checklist.context_references)
    for content in (body, *resources.values()):
        unknown = set(re.findall(r"(?<!\{)\{([a-z][a-z0-9_]*)\}(?!\})", content)) - allowed
        if unknown:
            raise ValueError(f"Undeclared placeholders: {sorted(unknown)}")
    for checklist in (declaration.checklist, declaration.checklist_overlay):
        if checklist:
            for variant in checklist.variants:
                for section in variant.sections:
                    if section.todo_projection and not declaration.prompt_inputs:
                        raise ValueError("Todo projection requires declared artifact prompt inputs")
    return declaration


def scaffold(request):
    unknown = set(request.sections) - {"Title", "Context", "Instructions", "Output", "Handoff"}
    if unknown:
        raise ValueError(f"Unsupported phase sections: {sorted(unknown)}")
    for section in ("Instructions", "Output", "Handoff"):
        if not request.sections.get(section, "").strip():
            raise ValueError(f"Supply author-owned {section} prose")
    metadata = request.declaration
    body = (
        f"# {request.sections.get('Title', metadata['name'])}\n\n## Role\n"
        "Read your agent file: {agent_file}\n"
    )
    for section in ("Context", "Instructions", "Output", "Handoff"):
        if section in request.sections:
            body += f"\n## {section}\n{request.sections[section].rstrip()}\n"
    return "---\n" + dump(metadata) + "---\n\n" + body


def patch(text, operation):
    if operation.path[0] == "sections":
        if (
            len(operation.path) != 2
            or operation.op != "replace"
            or not isinstance(operation.value, str)
        ):
            raise ValueError("Section edits require exact named replacement")
        return edit_section(
            text, operation.path[1], operation.value, operation.expected, operation.overwrite
        )
    if operation.path[0] != "metadata":
        raise ValueError("Phase operations must target metadata, sections or references")
    match, _ = frontmatter(text)
    changed = edit_yaml(match.group(1), operation.model_copy(update={"path": operation.path[1:]}))
    return text[: match.start(1)] + changed + text[match.end(1) :]


def resource_path(name):
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != "references"
        or path.suffix != ".md"
        or "\\" in name
    ):
        raise ValueError("Resources must be explicitly declared references/*.md paths")
    return path
