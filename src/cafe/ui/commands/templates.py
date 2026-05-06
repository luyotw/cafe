"""Template management command implementations extracted from cli.py."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console

from cafe.templates.manager import TemplateManager
from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text

TEMPLATE_TYPES = ["plan", "spec"]

template_app = typer.Typer(help="Manage plan and spec templates")

console = Console()


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """Inject runtime symbols from cafe.ui.cli into this module."""
    for key, value in runtime_globals.items():
        if key.startswith("__") or key == "set_runtime":
            continue
        globals()[key] = value


@template_app.command(name="add")
def template_add(
    source_file: Optional[str] = typer.Option(None, "--source-file", help="Path to the template file to add"),
    name: Optional[str] = typer.Option(None, "--name", help="Name for the template"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
) -> None:
    """Add a new template from a file.

    \b
    Examples:
        cafe template add --source-file path/to/template.md --name my-template --type plan
        cafe template add  # Interactive mode
    """
    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        if not name:
            name = prompt_text(
                message="Template name:",
                default="",
            )
            name = name.strip()
            if not name:
                console.print("[red]Error: Template name cannot be empty[/red]")
                raise typer.Exit(1)

        if not source_file:
            source_file = prompt_text(
                message="Source file path:",
                default="",
            )
            source_file = source_file.strip()
            if not source_file:
                console.print("[red]Error: Source file path cannot be empty[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Add template
    manager = TemplateManager(template_type=template_type)
    try:
        template_path = manager.add_template(source_file, name)
        # Show path relative to home directory
        try:
            relative_path = template_path.relative_to(Path.home())
            console.print(f"[green]✅ {template_type.capitalize()} template '{name}' added successfully: ~/{relative_path}[/green]")
        except ValueError:
            console.print(f"[green]✅ {template_type.capitalize()} template '{name}' added successfully: {template_path}[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except FileExistsError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def _print_templates(custom_only: bool = False) -> None:
    """Print templates table. Used by template ls, edit, rm."""
    from rich.table import Table

    table = Table(title="Custom Templates" if custom_only else "Available Templates", show_header=True, header_style="bold cyan")
    table.add_column("Type", style="green")
    table.add_column("Template", style="yellow")
    table.add_column("Source", style="dim")

    has_templates = False
    for template_type in TEMPLATE_TYPES:
        manager = TemplateManager(template_type=template_type)
        for name, source_type in manager.list_templates():
            if custom_only and source_type == "system":
                continue
            has_templates = True
            table.add_row(template_type, name, source_type)

    if not has_templates:
        console.print(f"[yellow]No {'custom ' if custom_only else ''}templates found.[/yellow]")
        return

    console.print(table)


@template_app.command(name="ls")
def template_ls(
    custom_only: bool = typer.Option(False, "--custom-only", help="Show only custom templates"),
) -> None:
    """List available templates.

    \b
    Examples:
        cafe template ls
        cafe template ls --custom-only
    """
    _print_templates(custom_only)


@template_app.command(name="rm")
def template_rm(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to remove"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Remove a template.

    \b
    Examples:
        cafe template rm --name my-template --type plan
        cafe template rm --name my-template --type plan --force
        cafe template rm  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Show custom templates before prompting
    if not name:
        _print_templates(custom_only=True)
        console.print()

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            # Only list custom templates (system templates cannot be deleted)
            custom_templates = [name for name, src in manager.list_templates() if src != "system"]
            if not custom_templates:
                console.print(f"[yellow]No custom {template_type} templates found[/yellow]")
                console.print("[dim]System default templates cannot be deleted[/dim]")
                raise typer.Exit(1)

            name = prompt_list(
                message="Select template to delete:",
                choices=custom_templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Confirm deletion unless --force
    if not force:
        try:
            confirm = prompt_confirm(
                f"Are you sure you want to delete template '{template_type}/{name}.md'?",
                default=False
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Remove template
    try:
        manager.remove_template(name)
        console.print(f"[green]✅ {template_type.capitalize()} template '{name}' removed successfully[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@template_app.command(name="cat")
def template_cat(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to view"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """View template content.

    \b
    Examples:
        cafe template cat --name my-template --type plan
        cafe template cat  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            templates_with_source = manager.list_templates()
            if not templates_with_source:
                console.print(f"[red]No {template_type} templates found[/red]")
                raise typer.Exit(1)

            templates = [name for name, _ in templates_with_source]
            name = prompt_list(
                message="Select template to view:",
                choices=templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Display template
    template_path = manager.get_template_path(name)
    if not template_path:
        console.print(f"[red]Error: {template_type.capitalize()} template '{name}' not found[/red]")
        raise typer.Exit(1)

    # Display template content using pager
    try:
        subprocess.run(["less", "-R", str(template_path)], check=False)
    except FileNotFoundError:
        # Fallback: print to console
        content = template_path.read_text()
        console.print(content)


@template_app.command(name="edit")
def template_edit(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to edit"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Edit a template with $EDITOR.

    \b
    Examples:
        cafe template edit --name my-template --type plan
        cafe template edit  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Show custom templates before prompting
    if not name:
        _print_templates(custom_only=True)
        console.print()

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            # Only list custom templates (system templates cannot be edited)
            custom_templates = [name for name, src in manager.list_templates() if src != "system"]
            if not custom_templates:
                console.print(f"[yellow]No custom {template_type} templates found[/yellow]")
                console.print("[dim]System default templates cannot be edited[/dim]")
                raise typer.Exit(1)

            name = prompt_list(
                message="Select template to edit:",
                choices=custom_templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Edit template
    template_path = manager.get_template_path(name)
    if not template_path:
        console.print(f"[red]Error: {template_type.capitalize()} template '{name}' not found[/red]")
        raise typer.Exit(1)

    # Open template in editor
    editor = os.environ.get("EDITOR", "vim")
    try:
        subprocess.run([editor, str(template_path)], check=True)
        console.print(f"[green]✅ Template '{name}' updated[/green]")

        # Auto-sync templates to local .cafe directory
        from cafe.ui.init_helpers import sync_templates
        cafe_dir = Path(".cafe")
        if cafe_dir.exists():
            template_success, template_failed = sync_templates(cafe_dir)
            if template_success > 0:
                console.print(f"  [green]✓[/green] Updated .cafe directory with {template_success} template(s)")
            if template_failed > 0:
                console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {template_failed} template file(s)")

    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit template[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
        raise typer.Exit(1)


@template_app.command(name="create")
def template_create(
    name: Optional[str] = typer.Option(None, "--name", help="Template name"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
) -> None:
    """Create a new template from scratch.

    \b
    Examples:
        cafe template create --name my-template --type plan
        cafe template create  # Interactive mode
    """
    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        if not name:
            name = prompt_text(
                message="Template name:",
                default="",
            )
            name = name.strip()
            if not name:
                console.print("[red]Error: Template name cannot be empty[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Create TemplateManager
    manager = TemplateManager(template_type=template_type)

    # Create template with editor
    editor = os.environ.get("EDITOR", "vim")

    # Create temp file with placeholder
    placeholder_content = f"""# Please enter your {template_type} template "{name}" content below.
# Note: It is highly recommended to include a todo list for all tasks.

"""

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tf:
        tf.write(placeholder_content)
        temp_path = tf.name

    try:
        # Open editor
        subprocess.run([editor, temp_path], check=True)

        # Read content
        with open(temp_path, "r") as f:
            content = f.read().strip()

        # Remove placeholder comments if user didn't modify
        if f'# Please enter your {template_type} template "{name}" content below.' in content:
            lines = [
                line for line in content.split('\n')
                if not (line.strip().startswith('#') and (
                    'Please enter your' in line or
                    'Note: It is highly recommended' in line
                ))
            ]
            content = '\n'.join(lines).strip()

        if not content:
            console.print("[red]Error: Template content cannot be empty[/red]")
            raise typer.Exit(1)

    finally:
        # Clean up temp file
        os.unlink(temp_path)

    # Save template using TemplateManager
    try:
        # Write content to a temporary file first
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
            tf.write(content)
            source_path = tf.name

        try:
            template_path = manager.add_template(source_path, name)
            # Show path relative to home directory
            try:
                relative_path = template_path.relative_to(Path.home())
                console.print(f"[green]✅ {template_type.capitalize()} template '{name}' created successfully: ~/{relative_path}[/green]")
            except ValueError:
                console.print(f"[green]✅ {template_type.capitalize()} template '{name}' created successfully: {template_path}[/green]")
        finally:
            # Clean up temporary source file
            os.unlink(source_path)
    except FileExistsError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@template_app.command(name="sync")
def template_sync() -> None:
    """Sync template files from global/system sources to local .cafe directory.

    Updates all template files in .cafe/templates to their latest versions from
    ~/.cafe/templates (custom) or src/cafe/data/templates (system default).
    Global custom templates take precedence over system defaults.
    """
    from cafe.ui.init_helpers import sync_templates

    # Check if .cafe directory exists
    cafe_dir = Path(".cafe")
    if not cafe_dir.exists():
        console.print("[red]Error: CAFE not initialized in this directory[/red]")
        console.print("[dim]Run 'cafe init' first[/dim]")
        raise typer.Exit(1)

    # Sync templates
    template_success, template_failed = sync_templates(cafe_dir)

    # Display summary
    if template_success > 0:
        console.print(f"  [green]✓[/green] Updated .cafe directory with {template_success} template(s)")

    if template_failed > 0:
        console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {template_failed} template file(s)")
