"""Utilities for checklist management."""

from pathlib import Path
from typing import Dict, Union


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
