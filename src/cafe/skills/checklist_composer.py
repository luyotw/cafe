"""Checklist composition from built-in skill references."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.skills.bridge import load_skill_reference
from cafe.skills.loader import canonical_skill_name
from cafe.templates.manager import TemplateManager
from cafe.utils.checklist_utils import generate_checklist_file, resolve_checklist_placeholders
from cafe.utils.prompt_utils import convert_to_checklist, extract_agent_guidelines_checklist


def _load_skill_checklist_reference(skill_name: str, ref_name: str) -> str:
    """Load checklist section content from a skill reference file."""
    return load_skill_reference(canonical_skill_name(skill_name), ref_name)


def _resolve_xml_questions_instruction(skill_name: str, ref_name: str, questions_xml_file: str) -> str:
    """Pre-resolve questions_xml_file in the XML instruction reference."""
    template = _load_skill_checklist_reference(skill_name, ref_name)
    return template.replace("{questions_xml_file}", questions_xml_file)


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
    agent_file = AgentManager.get_agent_file_path(agent_name, "pm")

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
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

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
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

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

    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = (
        f"{execution_steps}\n{template_instruction}{basic_principles_checklist}\n{agent_guidelines}"
    )

    placeholders = {
        "agent_file": agent_file,
        "plan_file_path": plan_file_path,
        "spec_file_path": spec_file_path,
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
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

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

    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

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
        "plan_file_path": plan_file_path,
        "xml_questions_instruction": xml_questions_instruction,
    }
    if develop_file:
        placeholders["develop_file"] = develop_file
    if feedback_file_path:
        placeholders["feedback_file_path"] = feedback_file_path
    if output_file:
        placeholders["output_file"] = output_file

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
) -> None:
    """Generate checklist file for review phase."""
    agent_file = AgentManager.get_agent_file_path(agent_name, "reviewer")

    execution_steps = _load_skill_checklist_reference("review", "execution_steps.md")

    pr_todo_list_section = ""
    if pr_todo_list_file_path:
        pr_todo_list_section = f"""
## PR Todo List Check
[ ] Read {pr_todo_list_file_path} - this is the todo list from the PR phase
[ ] Check that ALL todo items are marked as completed [x]. If any unchecked items [ ] remain, return needs_changes
"""

    agent_guidelines = extract_agent_guidelines_checklist(agent_file)
    checklist_content = f"{execution_steps}\n{pr_todo_list_section}{agent_guidelines}"

    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "plan_file_path": plan_file_path or "(not available)",
        "review_file_path": review_file_path,
        "base_branch": base_branch,
        "pr_feedback_file_path": pr_feedback_file_path or "(not available)",
    }

    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)
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
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

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

    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "plan_file_path": plan_file_path,
        "pr_file": pr_file,
    }

    if iteration > 1:
        placeholders["prev_pr_file"] = prev_pr_file if prev_pr_file else pr_file
        placeholders["current_pr_file"] = pr_file

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
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

    execution_steps = _load_skill_checklist_reference(
        "pr",
        "comments_organization_steps.md",
    )

    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

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
