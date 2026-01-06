"""Utility functions for prompt generation."""

from pathlib import Path
from typing import Optional


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
        lines = content.split('\n')

        # Extract lines starting with "- "
        guidelines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- '):
                # Remove the leading "- " and convert to checklist format
                guideline_text = stripped[2:]  # Remove "- "
                guidelines.append(f"[ ] {guideline_text}")

        if not guidelines:
            return ""

        # Build the checklist section
        checklist = "## Agent Guidelines Checklist\n\n"
        checklist += '\n'.join(guidelines)
        checklist += '\n'

        return checklist

    except Exception:
        # If any error occurs, return empty string
        return ""
