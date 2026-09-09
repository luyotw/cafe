"""Checklist composition from built-in skill references."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

from cafe.agents.manager import AgentManager
from cafe.skills.bridge import load_skill_reference, try_load_skill_reference
from cafe.skills.contracts import ChecklistVariant, SkillWorkflowContract
from cafe.skills.loader import canonical_skill_name
from cafe.templates.manager import TemplateManager
from cafe.utils.checklist_utils import generate_checklist_file, resolve_checklist_placeholders
from cafe.utils.prompt_utils import convert_to_checklist


def _load_skill_checklist_reference(skill_name: str, ref_name: str) -> str:
    """Load checklist section content from a skill reference file."""
    return load_skill_reference(canonical_skill_name(skill_name), ref_name)


def _load_agent_guidance(agent_name: str, role: str) -> tuple[str, str]:
    """Read role guidance without releasing the catalog lock between path and content."""
    agent_file, content = AgentManager.read_agent_file(agent_name, role)
    guidelines = (
        convert_to_checklist(content, "Agent Guidelines Checklist") if content else ""
    )
    return agent_file, guidelines


def _resolve_xml_questions_instruction(
    skill_name: str,
    ref_name: str,
    questions_xml_file: str,
) -> str:
    """Pre-resolve questions_xml_file in the XML instruction reference."""
    template = _load_skill_checklist_reference(skill_name, ref_name)
    return template.replace("{questions_xml_file}", questions_xml_file)


_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _variant_matches(
    variant: ChecklistVariant,
    *,
    step: str | None,
    iteration: int,
    artifacts: Mapping[str, Any],
    feedback: bool,
) -> bool:
    """Return whether one bounded, declared checklist selector applies."""
    return variant.when.matches(
        step=step, iteration=iteration, artifacts=artifacts, feedback=feedback
    )


def select_checklist_variant(
    contract: SkillWorkflowContract,
    *,
    step: str | None = None,
    iteration: int,
    artifacts: Mapping[str, Any],
    feedback: bool,
) -> ChecklistVariant:
    """Select the first matching, declaration-ordered checklist variant."""
    if contract.checklist is None:
        raise ValueError("Skill does not declare checklist composition")
    for variant in contract.checklist.variants:
        if _variant_matches(
            variant, step=step, iteration=iteration, artifacts=artifacts, feedback=feedback
        ):
            return variant
    raise ValueError(f"No checklist variant matches iteration {iteration}")


def _template_instruction(
    *,
    skill_name: str,
    contract: SkillWorkflowContract,
    template_mode: str,
    template_file: Optional[str],
) -> str:
    """Render the existing template-choice guidance from a declared catalog."""
    if contract.output_templates is None:
        return ""
    catalog = contract.output_templates.catalog
    if template_mode == "auto":
        manager = TemplateManager(template_type=catalog, skill_name=skill_name)
        template_lines = [
            f"  - `{name}`: {path}"
            for name, _source in manager.list_templates()
            if (path := manager.get_template_path(name)) is not None
        ]
        if not template_lines:
            return ""
        template_list = "\n".join(template_lines)
        label = contract.output_templates.label or catalog
        return (
            f"[ ] Pick a most suitable {label} template and read it. Available templates:\n"
            f"{template_list}\n"
            f"[ ] {contract.output_templates.follow_instruction}\n"
        )
    if template_file:
        return (
            f"[ ] Read {template_file} as reference for output format and structure\n"
            f"[ ] {contract.output_templates.follow_instruction}\n"
        )
    return ""


def _reference_context(
    *,
    skill_name: str,
    references: Mapping[str, str],
    context: Mapping[str, str],
) -> dict[str, str]:
    """Render declared context references only when their inputs are available."""
    resolved: dict[str, str] = {}
    for placeholder, reference in references.items():
        content = _load_skill_checklist_reference(skill_name, reference)
        names = _PLACEHOLDER_PATTERN.findall(content)
        if all(context.get(name) for name in names):
            resolved[placeholder] = resolve_checklist_placeholders(content, dict(context))
        else:
            resolved[placeholder] = ""
    return resolved


def compose_declared_checklist(
    *,
    skill_name: str,
    contract: SkillWorkflowContract,
    agent_name: str,
    role: str,
    checklist_file_path: Path,
    step: str | None = None,
    iteration: int,
    context: Mapping[str, str],
    artifacts: Mapping[str, Any],
    feedback: bool = False,
    template_mode: str = "auto",
    template_file: Optional[str] = None,
    preserve_completed_items: bool = False,
) -> bool:
    """Compose a skill-declared checklist without phase-name behavior branches."""
    if contract.checklist is None:
        generate_checklist_file(checklist_file_path, "")
        return False

    variant = select_checklist_variant(
        contract,
        step=step,
        iteration=iteration,
        artifacts=artifacts,
        feedback=feedback,
    )
    parts: list[str] = []
    for section in variant.sections:
        if section.reference:
            parts.append(_load_skill_checklist_reference(skill_name, section.reference))
        elif section.optional_checklist:
            optional = try_load_skill_reference(skill_name, section.optional_checklist)
            if optional:
                parts.append(convert_to_checklist(optional, "Basic Principles"))
        elif section.template_catalog:
            parts.append(
                _template_instruction(
                    skill_name=skill_name,
                    contract=contract,
                    template_mode=template_mode,
                    template_file=template_file,
                )
            )

    role_dirs = {
        "pm": "pm",
        "reviewer": "reviewer",
        "writer": "writer",
        "editor": "editor",
        "researcher": "researcher",
        "ops": "ops",
    }
    agent_file, guidelines = _load_agent_guidance(
        agent_name, role_dirs.get(role, "developer")
    )
    if contract.checklist.include_role_guidance:
        if guidelines:
            if contract.checklist.compact_agent_guidance:
                parts.append(guidelines)
            else:
                # References traditionally own their terminal spacing. Add the
                # missing separator only when the preceding section has not
                # already supplied a blank line.
                separator = "" if parts and parts[-1].endswith("\n\n") else "\n"
                parts.append(f"{separator}{guidelines}")

    placeholders = {key: str(value) for key, value in context.items() if value is not None}
    placeholders["agent_file"] = agent_file
    reference_context = _reference_context(
        skill_name=skill_name,
        references=contract.checklist.context_references,
        context=placeholders,
    )
    overlap = set(placeholders) & set(reference_context)
    if overlap:
        raise ValueError(
            "Checklist context references would overwrite placeholders for "
            f"{skill_name}: {', '.join(sorted(overlap))}"
        )
    placeholders.update(reference_context)
    content = "\n".join(part for part in parts if part)
    content = resolve_checklist_placeholders(content, placeholders)
    unresolved = sorted(set(_PLACEHOLDER_PATTERN.findall(content)))
    if unresolved:
        raise ValueError(
            f"Unresolved checklist placeholders for {skill_name}: {', '.join(unresolved)}"
        )
    generate_checklist_file(
        checklist_file_path,
        content,
        preserve_completed_items=preserve_completed_items,
    )
    return True


def generate_custom_skill_checklist(
    skill_name: str,
    agent_name: str,
    role: str,
    checklist_file_path: Path,
    correction_mode: bool = False,
    placeholders: Optional[dict] = None,
    preserve_completed_items: bool = False,
) -> bool:
    """Compose a checklist for a custom (non-builtin) phase skill from its references.

    Convention mirrors cafe-develop: the skill ships
    ``references/execution_steps_normal.md`` and optionally
    ``references/execution_steps_correction.md`` (used when the step re-enters
    with reviewer feedback). Returns False when the skill provides no checklist
    reference so the caller can keep the empty-checklist fallback.
    """
    execution_steps = ""
    if correction_mode:
        execution_steps = try_load_skill_reference(skill_name, "execution_steps_correction.md")
    if not execution_steps:
        execution_steps = try_load_skill_reference(skill_name, "execution_steps_normal.md")
    if not execution_steps:
        return False

    agent_file, agent_guidelines = _load_agent_guidance(agent_name, role)
    checklist_content = f"{execution_steps}\n{agent_guidelines}"

    resolved = {"agent_file": agent_file}
    if placeholders:
        resolved.update({key: value for key, value in placeholders.items() if value})

    checklist_content = resolve_checklist_placeholders(checklist_content, resolved)
    generate_checklist_file(
        checklist_file_path,
        checklist_content,
        preserve_completed_items=preserve_completed_items,
    )
    return True


def generate_spec_checklist(
    iteration: int,
    agent_name: str,
    current_spec_file: str,
    prev_spec_file: Optional[str],
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
    template_file: Optional[str] = None,
    template_mode: str = "auto",
    questions_xml_file: Optional[str] = None,
) -> None:
    """Generate checklist file for spec phase."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "pm")

    if iteration == 1:
        execution_steps = _load_skill_checklist_reference(
            "spec",
            "execution_steps_iteration_1.md",
        )
    else:
        execution_steps = _load_skill_checklist_reference(
            "spec",
            "execution_steps_iteration_n.md",
        )

    iteration_note = ""
    if iteration >= 4:
        iteration_note = _load_skill_checklist_reference(
            "spec",
            "important_notes_iteration_4_plus.md",
        )

    template_instruction = ""
    if iteration == 1:
        if template_mode == "auto":
            template_manager = TemplateManager(template_type="spec")
            available_templates_with_source = template_manager.list_templates()
            if available_templates_with_source:
                template_lines = []
                for name, _ in available_templates_with_source:
                    path = template_manager.get_template_path(name)
                    if path:
                        template_lines.append(f"  - `{name}`: {path}")
                template_list_str = "\n".join(template_lines)
                template_instruction = (
                    "[ ] Pick a most suitable spec template and read it. Available templates:\n"
                    f"{template_list_str}\n"
                    "[ ] Follow template structure when writing analysis results\n"
                )
        elif template_file:
            template_instruction = (
                f"[ ] Read {template_file} as reference for output format and structure\n"
                "[ ] Follow template structure when writing analysis results\n"
            )

    dod_instruction = _load_skill_checklist_reference("spec", "dod_instruction.md")
    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = (
        f"{execution_steps}\n{template_instruction}{basic_principles_checklist}\n"
        f"{iteration_note}{dod_instruction}\n{agent_guidelines}"
    )

    placeholders = {
        "agent_file": agent_file,
        "current_spec_file": current_spec_file,
        "output_file": current_spec_file,
        "previous_output_file": prev_spec_file or "",
        "iteration": str(iteration),
    }
    if prev_spec_file:
        placeholders["prev_spec_file"] = prev_spec_file

    if questions_xml_file:
        placeholders["xml_questions_instruction"] = _resolve_xml_questions_instruction(
            "spec",
            "xml_questions_instruction.md",
            questions_xml_file,
        )
    else:
        placeholders["xml_questions_instruction"] = ""

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_plan_checklist(
    agent_name: str,
    plan_file_path: str,
    spec_file_path: str,
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
    template_file: Optional[str] = None,
    template_mode: str = "auto",
    iteration: int = 1,
    prev_plan_file: Optional[str] = None,
    questions_xml_file: Optional[str] = None,
) -> None:
    """Generate checklist file for plan phase."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "developer")

    if iteration == 1:
        execution_steps = _load_skill_checklist_reference(
            "plan",
            "execution_steps_iteration_1.md",
        )
    else:
        execution_steps = _load_skill_checklist_reference(
            "plan",
            "execution_steps_iteration_n.md",
        )

    template_instruction = ""
    if template_mode == "auto":
        template_manager = TemplateManager(template_type="plan")
        available_templates_with_source = template_manager.list_templates()
        if available_templates_with_source:
            template_lines = []
            for name, _ in available_templates_with_source:
                path = template_manager.get_template_path(name)
                if path:
                    template_lines.append(f"  - `{name}`: {path}")
            template_list_str = "\n".join(template_lines)
            template_instruction = (
                "[ ] Pick a most suitable plan template and read it. Available templates:\n"
                f"{template_list_str}\n"
                "[ ] Follow template structure when writing plan\n"
            )
    elif template_file:
        template_instruction = (
            f"[ ] Read {template_file} as reference for output format and structure\n"
            "[ ] Follow template structure when writing plan\n"
        )

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = (
        f"{execution_steps}\n{template_instruction}{basic_principles_checklist}\n{agent_guidelines}"
    )

    placeholders = {
        "agent_file": agent_file,
        "plan_file_path": plan_file_path,
        "output_file": plan_file_path,
        "spec_file_path": spec_file_path,
        "spec_file": spec_file_path,
        "previous_output_file": prev_plan_file or "",
    }

    if iteration > 1:
        placeholders["prev_plan_file"] = prev_plan_file if prev_plan_file else plan_file_path
        placeholders["current_plan_file"] = plan_file_path

    if questions_xml_file:
        placeholders["xml_questions_instruction"] = _resolve_xml_questions_instruction(
            "plan",
            "xml_questions_instruction.md",
            questions_xml_file,
        )
    else:
        placeholders["xml_questions_instruction"] = ""

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_develop_checklist(
    agent_name: str,
    spec_file_path: str,
    plan_file_path: str,
    develop_file: Optional[str],
    checklist_file_path: Path,
    correction_mode: bool = False,
    feedback_file_path: Optional[str] = None,
    basic_principles: Optional[str] = None,
    output_file: Optional[str] = None,
    questions_xml_file: Optional[str] = None,
) -> None:
    """Generate checklist file for develop phase."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "developer")

    if correction_mode:
        execution_steps = _load_skill_checklist_reference(
            "develop",
            "execution_steps_correction.md",
        )
    else:
        execution_steps = _load_skill_checklist_reference(
            "develop",
            "execution_steps_normal.md",
        )

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    guidance_separator = "\n\n" if basic_principles_checklist else "\n"
    checklist_content = (
        f"{execution_steps}\n{basic_principles_checklist}"
        f"{guidance_separator}{agent_guidelines}"
    )

    if questions_xml_file:
        xml_questions_instruction = _resolve_xml_questions_instruction(
            "develop",
            "xml_questions_instruction.md",
            str(questions_xml_file),
        )
    else:
        xml_questions_instruction = ""

    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "spec_file": spec_file_path,
        "plan_file_path": plan_file_path,
        "plan_file": plan_file_path,
        "xml_questions_instruction": xml_questions_instruction,
    }
    if develop_file:
        placeholders["develop_file"] = develop_file
    if feedback_file_path:
        placeholders["feedback_file_path"] = feedback_file_path
        placeholders["feedback_file"] = feedback_file_path
    if output_file:
        placeholders["output_file"] = output_file

    for placeholder, reference in {
        "normal_plan_context": "normal_plan_context.md",
        "normal_plan_verification": "normal_plan_verification.md",
        "correction_plan_context": "correction_plan_context.md",
        "correction_plan_test_list": "correction_plan_test_list.md",
    }.items():
        placeholders[placeholder] = resolve_checklist_placeholders(
            _load_skill_checklist_reference("develop", reference), placeholders
        )

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_review_checklist(
    agent_name: str,
    spec_file_path: str,
    review_file_path: str,
    base_branch: str,
    checklist_file_path: Path,
    pr_feedback_file_path: Optional[str] = None,
    plan_file_path: Optional[str] = None,
    pr_todo_list_file_path: Optional[str] = None,
    basic_principles: Optional[str] = None,
) -> None:
    """Generate checklist file for review phase."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "reviewer")

    execution_steps = _load_skill_checklist_reference("review", "execution_steps.md")

    pr_todo_list_section = ""
    if pr_todo_list_file_path:
        pr_todo_list_section = (
            "\n## PR Todo List Check\n"
            f"[ ] Read {pr_todo_list_file_path} - this is the todo list from the PR phase\n"
            "[ ] Check that ALL todo items are marked as completed [x]. "
            "If any unchecked items [ ] remain, return needs_changes\n"
        )

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(
            basic_principles,
            "Basic Principles",
        )

    checklist_content = f"{execution_steps}\n"
    if basic_principles_checklist:
        checklist_content += f"{basic_principles_checklist}\n"
    checklist_content += f"{pr_todo_list_section}{agent_guidelines}"

    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "spec_file": spec_file_path,
        "plan_file_path": plan_file_path or "(not available)",
        "plan_file": plan_file_path or "(not available)",
        "review_file_path": review_file_path,
        "output_file": review_file_path,
        "base_branch": base_branch,
        "pr_feedback_file_path": pr_feedback_file_path or "(not available)",
        "feedback_file": pr_feedback_file_path or "(not available)",
    }
    placeholders["feedback_instruction"] = resolve_checklist_placeholders(
        _load_skill_checklist_reference("review", "feedback_instruction.md"), placeholders
    )
    for placeholder, reference in {
        "spec_read_instruction": "spec_read_instruction.md",
        "plan_read_instruction": "plan_read_instruction.md",
        "spec_comparison_instruction": "spec_comparison_instruction.md",
    }.items():
        placeholders[placeholder] = resolve_checklist_placeholders(
            _load_skill_checklist_reference("review", reference), placeholders
        )

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    checklist_content = checklist_content.rstrip() + "\n"
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_pr_checklist(
    agent_name: str,
    spec_file_path: str,
    plan_file_path: str,
    pr_file: str,
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
    iteration: int = 1,
    prev_pr_file: Optional[str] = None,
) -> None:
    """Generate checklist file for PR phase."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "developer")

    if iteration == 1:
        execution_steps = _load_skill_checklist_reference(
            "pr",
            "execution_steps_iteration_1.md",
        )
    else:
        execution_steps = _load_skill_checklist_reference(
            "pr",
            "execution_steps_iteration_n.md",
        )

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "spec_file": spec_file_path,
        "plan_file_path": plan_file_path,
        "plan_file": plan_file_path,
        "pr_file": pr_file,
        "output_file": pr_file,
        "previous_output_file": prev_pr_file or "",
        "review_feedback_instruction": "",
    }

    if iteration > 1:
        placeholders["prev_pr_file"] = prev_pr_file if prev_pr_file else pr_file
        placeholders["current_pr_file"] = pr_file

    for placeholder, reference in {
        "spec_read_instruction": "spec_read_instruction.md",
        "plan_read_instruction": "plan_read_instruction.md",
    }.items():
        placeholders[placeholder] = resolve_checklist_placeholders(
            _load_skill_checklist_reference("pr", reference), placeholders
        )

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_pr_comments_checklist(
    agent_name: str,
    user_input_file_path: str,
    output_file_path: str,
    prev_output_file_path: Optional[str],
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
) -> None:
    """Generate checklist file for PR comments organization."""
    agent_file, agent_guidelines = _load_agent_guidance(agent_name, "developer")

    execution_steps = _load_skill_checklist_reference(
        "pr",
        "comments_organization_steps.md",
    )

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    placeholders = {
        "agent_file": agent_file,
        "user_input_file": user_input_file_path,
        "output_file": output_file_path,
        "prev_output_file": prev_output_file_path or "N/A (first iteration)",
    }

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
    generate_checklist_file(checklist_file_path, checklist_content)
