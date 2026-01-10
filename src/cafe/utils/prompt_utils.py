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
