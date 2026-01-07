"""Interactive template selector."""

from pathlib import Path
from typing import Dict, List, Optional

import typer
from rich.console import Console

from cafe.ui.inquirer_prompts import prompt_list

console = Console()


def select_template(templates: List[str], template_paths: Dict[str, Path]) -> Optional[str]:
    """Select a template interactively.

    Args:
        templates: List of template names
        template_paths: Dict mapping template names to their file paths

    Returns:
        Selected template name (or 'auto'), or None if skipped
    """
    if not templates:
        return None

    # Build choices list with 'auto' option first
    # Use template names as keys for direct mapping
    choices = ["auto (agent decides)"] + templates

    console.print()
    console.print("[dim]Tip: Use 'cafe template cat --type TYPE --name NAME' to preview template content[/dim]")
    if len(templates) == 1:
        console.print("[dim]     Create your own: 'cafe template add --source-file FILE --name NAME --type TYPE' to add custom template[/dim]")
    console.print()

    try:
        selected = prompt_list("Select a template:", choices, default=choices[0])
        # Extract the template name (remove "(agent decides)" if present)
        if " (" in selected:
            template_name = selected.split(" (")[0]
        else:
            template_name = selected
        return template_name
    except (KeyboardInterrupt, EOFError):
        # User pressed Ctrl+C or Ctrl+D, exit
        console.print()
        raise typer.Exit(1)
