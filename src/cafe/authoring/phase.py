"""Canonical phase scaffolding and narrowly scoped phase authoring guards."""

import re
from pathlib import PurePosixPath

from cafe.skills.contracts import RUNTIME_OWNED_PROMPT_PLACEHOLDERS, SkillWorkflowDeclaration

from .requests import decode_request
from .source_edits import dump, edit_section, edit_yaml


class PhaseError(ValueError):
    """A phase source rejection with an exact authoring field."""

    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


def frontmatter(text):
    match = re.match(r"\A---\r?\n(.*?)^---\s*\n", text, re.S | re.M)
    if not match:
        raise PhaseError("Phase requires complete YAML frontmatter", "metadata")
    return match, decode_request(match.group(1))


def guard(text, name, resources):
    match, metadata = frontmatter(text)
    if set(metadata) - {"name", "description", "version", "workflow"}:
        key = sorted(set(metadata) - {"name", "description", "version", "workflow"})[0]
        raise PhaseError("Unsupported phase frontmatter metadata", f"metadata.{key}")
    if metadata.get("name") != name or not name.startswith("cafe-"):
        raise PhaseError("Phase name must match its canonical cafe- directory", "metadata.name")
    if not metadata.get("description") or not metadata.get("version"):
        key = "description" if not metadata.get("description") else "version"
        raise PhaseError("Phase requires nonempty description and version", f"metadata.{key}")
    declaration = SkillWorkflowDeclaration.model_validate(metadata.get("workflow", {}))
    if declaration.execution_profile is None:
        raise PhaseError(
            "Authoring requires explicit workflow.execution_profile", "workflow.execution_profile"
        )
    body = text[match.end() :]
    for section in ("Role", "Instructions", "Output", "Handoff"):
        headings = re.findall(rf"^## {section}\s*$", body, re.M)
        if len(headings) != 1:
            raise PhaseError(
                f"Phase requires one canonical {section} section", f"sections.{section}"
            )
        content = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        if content is None or not content.group(1).strip():
            raise PhaseError(
                f"Phase requires author-owned {section} content", f"sections.{section}"
            )
    if "Read your agent file: {agent_file}" not in body or "{output_file}" not in body:
        section = "Role" if "Read your agent file: {agent_file}" not in body else "Output"
        raise PhaseError(
            "Canonical role and output path instructions are required", f"sections.{section}"
        )
    allowed = set(RUNTIME_OWNED_PROMPT_PLACEHOLDERS)
    allowed.update(i.placeholder for i in declaration.prompt_inputs)
    allowed.update(declaration.prompt_references)
    for checklist in (declaration.checklist, declaration.checklist_overlay):
        if checklist:
            allowed.update(checklist.context_references)
    for origin, content in [(None, body), *resources.items()]:
        for token in re.finditer(r"(?<!\{)\{([a-z][a-z0-9_]*)\}(?!\})", content):
            placeholder = token.group(1)
            if placeholder in allowed:
                continue
            if origin is None:
                headings = list(re.finditer(r"^## ([^\n]+)\n", content[: token.start()], re.M))
                origin_field = f"sections.{headings[-1].group(1)}" if headings else "sections.Title"
            else:
                origin_field = f"references.{origin}"
            raise PhaseError(
                f"Undeclared placeholder: {placeholder}", f"{origin_field}.{placeholder}"
            )
    for checklist in (declaration.checklist, declaration.checklist_overlay):
        if checklist:
            for variant in checklist.variants:
                for section in variant.sections:
                    if section.todo_projection and not declaration.prompt_inputs:
                        field = (
                            "checklist"
                            if checklist is declaration.checklist
                            else "checklist_overlay"
                        )
                        raise PhaseError(
                            "Todo projection requires declared artifact prompt inputs",
                            f"workflow.{field}",
                        )
    return declaration


def scaffold(request):
    unknown = set(request.sections) - {"Title", "Context", "Instructions", "Output", "Handoff"}
    if unknown:
        raise PhaseError(
            f"Unsupported phase sections: {sorted(unknown)}", f"sections.{sorted(unknown)[0]}"
        )
    for section in ("Instructions", "Output", "Handoff"):
        if not request.sections.get(section, "").strip():
            raise PhaseError(f"Supply author-owned {section} prose", f"sections.{section}")
    metadata = request.declaration
    body = (
        f"# {request.sections.get('Title', metadata.get('name', ''))}\n\n## Role\n"
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
        raise PhaseError(
            "Resources must be explicitly declared references/*.md paths", f"references.{name}"
        )
    return path
