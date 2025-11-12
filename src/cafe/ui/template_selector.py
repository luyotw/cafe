"""Interactive template selector."""

from pathlib import Path
from typing import Dict, List, Optional

import typer
from rich.console import Console

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
    console.print("[bold cyan]Available templates:[/bold cyan]")
    for i, name in enumerate(templates, 1):
        console.print(f"  [bold green]{i}[/bold green]. [yellow]{name}[/yellow]")
    console.print()
    console.print("[dim]Tip: Use 'cafe template cat <name>' to preview template content[/dim]")
    if len(templates) == 1:
        console.print("[dim]     Create your own: 'cafe template add <file> <name>' to add custom template[/dim]")
    console.print()

    while True:
        try:
            choice = typer.prompt("Select template number", default="1")
            index = int(choice) - 1
            if 0 <= index < len(templates):
                return templates[index]
            else:
                console.print(f"[red]Invalid selection. Please choose 1-{len(templates)}[/red]")
        except ValueError:
            console.print("[red]Invalid input. Please enter a number[/red]")
        except (KeyboardInterrupt, EOFError):
            # User pressed Ctrl+C or Ctrl+D, exit
            console.print()
            raise typer.Exit(1)
