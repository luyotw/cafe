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
        Selected template name, or None if skipped
    """
    if not templates:
        return None

    console.print()
    console.print("Please select a plan template:")
    console.print()
    console.print("[bold cyan]Available templates:[/bold cyan]")
    for i, name in enumerate(templates, 1):
        console.print(f"  [green]{i}[/green]. {name}")
    console.print()
    console.print("[dim]Tip: Use 'cafe template cat <name>' to preview template content[/dim]")
    if len(templates) == 1:
        console.print("[dim]     Create your own: 'cafe template add <file> <name>' to add custom template[/dim]")
    console.print()

    # Create choices for inquirer
    choices = [f"{i}. {name}" for i, name in enumerate(templates, 1)]

    try:
        selected = prompt_list("Select template number:", choices, default=choices[0])
        # Parse the selection to get template name
        # Format: "1. default" -> "default"
        template_name = selected.split(". ", 1)[1]
        return template_name
    except (KeyboardInterrupt, EOFError):
        # User pressed Ctrl+C or Ctrl+D, exit
        console.print()
        raise typer.Exit(1)
