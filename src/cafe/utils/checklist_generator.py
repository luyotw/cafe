"""Checklist generation for phase execution."""

from pathlib import Path
from typing import Dict, Optional
from cafe.utils.checklist_utils import (
    resolve_checklist_placeholders,
    generate_checklist_file
)
from cafe.utils import checklist_templates
from cafe.agents.manager import AgentManager
from cafe.utils.prompt_utils import extract_agent_guidelines_checklist, convert_to_checklist
from cafe.utils.git_utils import to_cwd_relative_path


def generate_spec_checklist(
    iteration: int,
    agent_name: str,
    current_spec_file: str,
    prev_spec_file: Optional[str],
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
    template_file: Optional[str] = None,
    template_mode: str = "auto",
) -> None:
    """Generate checklist file for spec phase.

    Args:
        iteration: Current iteration number
        agent_name: PM agent name
        current_spec_file: Path to current spec file
        prev_spec_file: Path to previous spec file (None for iteration 1)
        checklist_file_path: Path where checklist file should be created
        basic_principles: Basic principles text in "- item" format (optional)
        template_file: Path to spec template file (optional)
        template_mode: Template selection mode ('auto' or 'manual', default: 'auto')
    """
    # Get agent file path
    agent_file = AgentManager.get_agent_file_path(agent_name, "pm")

    # Choose template based on iteration
    if iteration == 1:
        execution_steps = checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_1
    else:
        execution_steps = checklist_templates.SPEC_EXECUTION_STEPS_ITERATION_N

    # Add iteration-specific note for iteration 4+
    iteration_note = ""
    if iteration >= 4:
        iteration_note = checklist_templates.SPEC_IMPORTANT_NOTES_ITERATION_4_PLUS

    # Add template instruction for iteration 1
    template_instruction = ""
    if iteration == 1:
        if template_mode == "auto":
            # Auto mode: list available templates for agent to choose
            from cafe.templates.manager import TemplateManager
            template_manager = TemplateManager(template_type="spec")
            available_templates_with_source = template_manager.list_templates()
            if available_templates_with_source:
                available_templates = [name for name, _ in available_templates_with_source]
                template_list_str = ", ".join(f"`{t}`" for t in available_templates)
                template_instruction = f"[ ] Pick a most suitable spec template (available: {template_list_str}) and read it\n[ ] Follow template structure when writing analysis results\n"
        elif template_file:
            # Manual mode: use the specified template
            template_instruction = f"[ ] Read {template_file} as reference for output format and structure\n[ ] Follow template structure when writing analysis results\n"

    # Get agent guidelines checklist
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    # Convert basic principles to checklist format
    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    # Combine all sections
    checklist_content = f"{execution_steps}\n{template_instruction}{basic_principles_checklist}\n{iteration_note}\n{agent_guidelines}"

    # Build placeholders dict
    placeholders = {
        "agent_file": agent_file,
        "current_spec_file": current_spec_file,
        "iteration": str(iteration),
    }
    if prev_spec_file:
        placeholders["prev_spec_file"] = prev_spec_file

    # Resolve placeholders
    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)

    # Generate checklist file
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_plan_checklist(
    agent_name: str,
    plan_file_path: str,
    spec_file_path: str,
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
) -> None:
    """Generate checklist file for plan phase.

    Args:
        agent_name: Developer agent name
        plan_file_path: Path to plan file
        spec_file_path: Path to spec file
        checklist_file_path: Path where checklist file should be created
        basic_principles: Basic principles text in "- item" format (optional)
    """
    # Get agent file path
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

    # Get templates
    execution_steps = checklist_templates.PLAN_EXECUTION_STEPS

    # Get agent guidelines checklist
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    # Convert basic principles to checklist format
    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    # Combine all sections
    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    # Build placeholders dict
    placeholders = {
        "agent_file": agent_file,
        "plan_file_path": plan_file_path,
        "spec_file_path": spec_file_path,
    }

    # Resolve placeholders
    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)

    # Generate checklist file
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_develop_checklist(
    agent_name: str,
    spec_file_path: str,
    plan_file_path: str,
    develop_file: Optional[str],
    checklist_file_path: Path,
    correction_mode: bool = False,
    review_file_path: Optional[str] = None,
    basic_principles: Optional[str] = None,
) -> None:
    """Generate checklist file for develop phase.

    Args:
        agent_name: Developer agent name
        spec_file_path: Path to spec file
        plan_file_path: Path to plan file
        develop_file: Path to develop file (for correction mode)
        checklist_file_path: Path where checklist file should be created
        correction_mode: True if in correction mode, False for normal mode
        review_file_path: Path to review file (for correction mode)
        basic_principles: Basic principles text in "- item" format (optional)
    """
    # Get agent file path
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

    # Choose template based on mode
    if correction_mode:
        execution_steps = checklist_templates.DEVELOP_EXECUTION_STEPS_CORRECTION
    else:
        execution_steps = checklist_templates.DEVELOP_EXECUTION_STEPS_NORMAL

    # Get agent guidelines checklist
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    # Convert basic principles to checklist format
    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    # Combine all sections
    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    # Build placeholders dict
    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "plan_file_path": plan_file_path,
    }
    if develop_file:
        placeholders["develop_file"] = develop_file
    if review_file_path:
        placeholders["review_file_path"] = review_file_path

    # Resolve placeholders
    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)

    # Generate checklist file
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_review_checklist(
    agent_name: str,
    spec_file_path: str,
    review_file_path: str,
    base_branch: str,
    checklist_file_path: Path,
) -> None:
    """Generate checklist file for review phase.

    Args:
        agent_name: Reviewer agent name
        spec_file_path: Path to spec file
        review_file_path: Path to review file
        base_branch: Base branch name
        checklist_file_path: Path where checklist file should be created
    """
    # Get agent file path
    agent_file = AgentManager.get_agent_file_path(agent_name, "reviewer")

    # Get templates
    execution_steps = checklist_templates.REVIEW_EXECUTION_STEPS

    # Get agent guidelines checklist
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    # Combine all sections
    checklist_content = f"{execution_steps}\n{agent_guidelines}"

    # Build placeholders dict
    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "review_file_path": review_file_path,
        "base_branch": base_branch,
    }

    # Resolve placeholders
    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)

    # Generate checklist file
    generate_checklist_file(checklist_file_path, checklist_content)


def generate_pr_checklist(
    agent_name: str,
    spec_file_path: str,
    plan_file_path: str,
    pr_file: str,
    checklist_file_path: Path,
    basic_principles: Optional[str] = None,
) -> None:
    """Generate checklist file for PR phase.

    Args:
        agent_name: Developer agent name
        spec_file_path: Path to spec file
        plan_file_path: Path to plan file
        pr_file: Path to PR output file (output.md)
        checklist_file_path: Path where checklist file should be created
        basic_principles: Basic principles text in "- item" format (optional)
    """
    # Get agent file path
    agent_file = AgentManager.get_agent_file_path(agent_name, "developer")

    # Get templates
    execution_steps = checklist_templates.PR_EXECUTION_STEPS

    # Get agent guidelines checklist
    agent_guidelines = extract_agent_guidelines_checklist(agent_file)

    # Convert basic principles to checklist format
    basic_principles_checklist = ""
    if basic_principles:
        basic_principles_checklist = convert_to_checklist(basic_principles, "Basic Principles")

    # Combine all sections
    checklist_content = f"{execution_steps}\n{basic_principles_checklist}\n{agent_guidelines}"

    # Build placeholders dict
    placeholders = {
        "agent_file": agent_file,
        "spec_file_path": spec_file_path,
        "plan_file_path": plan_file_path,
        "pr_file": pr_file,
    }

    # Resolve placeholders
    checklist_content = resolve_checklist_placeholders(checklist_content, placeholders)

    # Generate checklist file
    generate_checklist_file(checklist_file_path, checklist_content)
