"""Utility functions for prompt generation."""

from pathlib import Path
from typing import Optional


def format_checklist_instruction(checklist_path: str) -> str:
    """Generate standard checklist instruction text for phase prompts.

    Args:
        checklist_path: Path to the checklist file (can be relative or absolute)

    Returns:
        Formatted instruction text that tells agents to read and complete checklist

    Example:
        >>> format_checklist_instruction("./.cafe/issues/issue108/spec/iteration_001/checklist.md")
        '''**Task Checklist:**
        Read ./.cafe/issues/issue108/spec/iteration_001/checklist.md for detailed execution steps and requirements.

        IMPORTANT: You MUST edit the checklist file and mark each completed item with [x] format (e.g., "[x] Read agent file").
        Do NOT return a status code until ALL checklist items are marked as [x].'''
    """
    return f"""**Task Checklist:**
Read {checklist_path} for detailed execution steps and requirements.

IMPORTANT: You MUST edit the checklist file and mark each completed item with [x] format (e.g., "[x] Read agent file").
Do NOT return a status code until ALL checklist items are marked as [x]."""


def convert_to_checklist(content: str, section_title: str) -> str:
    """Convert bullet points to checklist format.

    Args:
        content: Text with bullet points in "- item" format
        section_title: Title for the checklist section

    Returns:
        Formatted checklist string with "[ ]" prefix for each item

    Example:
        Input:
        ```
        - Follow commit style
        - Use same language
        ```

        Output:
        ```
        ## Basic Principles

        [ ] Follow commit style
        [ ] Use same language
        ```
    """
    if not content:
        return ""

    lines = content.split('\n')
    items = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- '):
            # Remove the leading "- " and convert to checklist format
            item_text = stripped[2:]  # Remove "- "
            items.append(f"[ ] {item_text}")

    if not items:
        return ""

    # Build the checklist section
    checklist = f"## {section_title}\n\n"
    checklist += '\n'.join(items)
    checklist += '\n'

    return checklist


def extract_agent_guidelines_checklist(agent_file_path: str) -> str:
    """Extract bullet points from agent md file and convert to checklist format.

    Args:
        agent_file_path: Path to agent markdown file

    Returns:
        Formatted checklist string with "[ ]" prefix for each guideline

    Example:
        Input file contains:
        ```
        - **Focus on the Requirement**: Do not jump into discussions...
        - **User Perspective**: Think about functions...
        ```

        Output:
        ```
        ## Agent Guidelines Checklist

        [ ] **Focus on the Requirement**: Do not jump into discussions...
        [ ] **User Perspective**: Think about functions...
        ```
    """
    agent_path = Path(agent_file_path)

    if not agent_path.exists():
        return ""

    try:
        content = agent_path.read_text(encoding='utf-8')
        return convert_to_checklist(content, "Agent Guidelines Checklist")

    except Exception:
        # If any error occurs, return empty string
        return ""


def get_template_allowed_directories(template_mode: str, template_path: Optional[Path], template_type: str) -> list[str]:
    """Get list of allowed directories for template access.

    Args:
        template_mode: Template selection mode ('auto' or 'manual')
        template_path: Path to template file (for manual mode)
        template_type: Type of template ('spec' or 'plan')

    Returns:
        List of directory paths that should be added to allowed_directories
    """
    from cafe.utils.config import get_global_cafe_dir

    dirs = []

    if template_mode == "auto":
        # Auto mode: add both system and custom templates directories
        # System templates directory (bundled under owning skill's assets/)
        package_data_dir = (
            Path(__file__).parent.parent
            / "data"
            / "skills"
            / template_type
            / "assets"
            / "templates"
        )
        if package_data_dir.exists():
            dirs.append(str(package_data_dir.resolve()))
        # Custom templates directory (global cafe dir)
        custom_templates_dir = get_global_cafe_dir() / "templates" / template_type
        if custom_templates_dir.exists():
            dirs.append(str(custom_templates_dir.resolve()))
    elif template_path:
        # Manual mode: add the specified template's directory
        dirs.append(str(template_path.parent.resolve()))

    return dirs
