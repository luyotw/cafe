"""Interactive template selector."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from rich.console import Console

from cafe.ui.inquirer_prompts import prompt_list

console = Console()


def select_template(
    templates: List[str],
    template_paths: Dict[str, Path],
    templates_with_source: Optional[List[Tuple[str, str]]] = None,
) -> Optional[str]:
    """Select a template interactively.

    Args:
        templates: List of template names
        template_paths: Dict mapping template names to their file paths
        templates_with_source: Optional list of (template_name, source_type) tuples

    Returns:
        Selected template name (or 'auto'), or None if skipped
    """
    if not templates:
        return None

    # Build choices list with 'auto' option first
    # If source information is provided, append it to template names
    if templates_with_source:
        template_choices = []
        for name, source_type in templates_with_source:
            if source_type == "system":
                template_choices.append(f"{name} (system default)")
            else:  # custom
                template_choices.append(f"{name} (custom)")
        choices = ["auto (agent decides)"] + template_choices
    else:
        # Fallback to old behavior if source info not provided
        choices = ["auto (agent decides)"] + templates

    console.print()
    console.print("[dim]Tip: Use 'cafe template cat' to preview template content[/dim]")
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
