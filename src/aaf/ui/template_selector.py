"""Interactive template selector."""

from pathlib import Path
from typing import Dict, List, Optional

import typer


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

    print()
    print("Available templates:")
    for i, name in enumerate(templates, 1):
        print(f"  {i}. {name}")
    print()
    print("Tip: Use 'aaf template cat <name>' to preview template content")
    print()

    while True:
        try:
            choice = typer.prompt("Select template number")
            index = int(choice) - 1
            if 0 <= index < len(templates):
                return templates[index]
            else:
                print(f"Invalid selection. Please choose 1-{len(templates)}")
        except ValueError:
            print("Invalid input. Please enter a number")
        except (KeyboardInterrupt, EOFError):
            # User pressed Ctrl+C or Ctrl+D, exit
            print()
            raise typer.Exit(1)
