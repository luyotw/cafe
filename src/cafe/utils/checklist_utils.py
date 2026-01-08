"""Utilities for checklist management."""

import re
from pathlib import Path
from typing import Dict, Union


def extract_checklist_sections(prompt: str) -> str:
    """Extract checklist sections from prompt string.

    Args:
        prompt: The prompt string containing checklist sections

    Returns:
        String containing only the checklist sections, or empty string if none found
    """
    # Pattern to match checklist section headers
    checklist_pattern = r"^## .*Checklist$"

    lines = prompt.split("\n")
    result_lines = []
    in_checklist_section = False

    for line in lines:
        # Check if this line is a checklist section header
        if re.match(checklist_pattern, line.strip()):
            in_checklist_section = True
            result_lines.append(line)
        # Check if this line is a different section header (ends checklist section)
        elif line.strip().startswith("## ") and not re.match(checklist_pattern, line.strip()):
            in_checklist_section = False
        # If we're in a checklist section, include the line
        elif in_checklist_section:
            result_lines.append(line)

    # Return joined lines, or empty string if no checklists found
    result = "\n".join(result_lines).strip()
    return result


def resolve_checklist_placeholders(checklist: str, placeholders: Dict[str, str]) -> str:
    """Resolve placeholder variables in checklist content.

    Args:
        checklist: Checklist content with placeholders like {agent_file}
        placeholders: Dictionary mapping placeholder names to actual values

    Returns:
        Checklist content with placeholders resolved
    """
    result = checklist

    for key, value in placeholders.items():
        placeholder = f"{{{key}}}"
        result = result.replace(placeholder, value)

    return result


def generate_checklist_file(output_path: Union[str, Path], checklist_content: str) -> None:
    """Generate checklist file at specified path.

    Args:
        output_path: Path where checklist file should be created
        checklist_content: Content to write to the checklist file
    """
    output_path = Path(output_path)

    # Create parent directories if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write checklist content to file
    output_path.write_text(checklist_content)
