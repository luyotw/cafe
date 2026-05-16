"""cafe preset subcommand group."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from cafe.utils.preset import PresetAlreadyExistsError, PresetManager

app = typer.Typer(help="Manage crew preset configurations.")
console = Console()


@app.command(name="list")
def list_presets() -> None:
    """List all available crew presets (built-in, global, and project)."""
    manager = PresetManager()
    presets = manager.list()

    if not presets:
        console.print("[yellow]No presets found.[/yellow]")
        return

    table = Table(title="Available Presets", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="green")
    table.add_column("Path", style="dim")

    for info in presets:
        table.add_row(info.name, info.source, str(info.path))

    console.print(table)


@app.command()
def save(
    name: str = typer.Argument(..., help="Preset name to save as"),
    local: bool = typer.Option(
        False,
        "--local",
        help="Save to project-level .cafe/presets/ instead of global ~/.cafe/presets/",
    ),
) -> None:
    """Save the current crew.yaml as a named preset."""
    manager = PresetManager()
    cafe_dir = Path(".cafe")

    crew_yaml = cafe_dir / "crew.yaml"
    if not crew_yaml.exists():
        console.print("[red]Error: No .cafe/crew.yaml found. Run 'cafe prepare' first.[/red]")
        raise typer.Exit(1)

    try:
        dest = manager.save(name, local=local, cafe_dir=cafe_dir)
        scope = "project" if local else "global"
        console.print(f"[green]✓[/green] Preset '[cyan]{name}[/cyan]' saved to {scope}: {dest}")
    except PresetAlreadyExistsError:
        overwrite = typer.confirm(f"Preset '{name}' already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
        dest = manager.save(name, local=local, overwrite=True, cafe_dir=cafe_dir)
        console.print(f"[green]✓[/green] Preset '[cyan]{name}[/cyan]' overwritten: {dest}")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
