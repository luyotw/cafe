"""Resource management commands (templates and agents)."""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from cafe.templates.manager import TemplateManager
from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text

console = Console()

# Create agent sub-application
agent_app = typer.Typer(help="Manage agents")


def template(
    action: str = typer.Argument(..., help="Action: add, list, or remove"),
    source: Optional[str] = typer.Argument(None, help="Source file path (for 'add' action)"),
    name: Optional[str] = typer.Argument(None, help="Template name (for 'add' or 'remove' action)"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Manage plan templates.

    Actions:
        add  - Add a new template from a file
        ls   - List all available templates
        rm   - Remove a template
        cat  - View template content
        edit - Edit a template with $EDITOR

    Examples:
        # Add a new template
        cafe template add path/to/template.md my-template

        # List all templates
        cafe template ls

        # View template content
        cafe template cat my-template

        # Edit a template
        cafe template edit my-template

        # Remove a template
        cafe template rm my-template
    """
    try:
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        manager = TemplateManager(config_dir)

        if action == "add":
            if not source or not name:
                console.print(
                    "[red]Error: 'add' action requires both source file path and template name[/red]"
                )
                console.print("[dim]Usage: cafe template add <source-file> <template-name>[/dim]")
                raise typer.Exit(1)

            try:
                manager.add_template(source, name)
                console.print(f"[green]✅ Template '{name}' added successfully[/green]")
            except FileNotFoundError as e:
                console.print(f"[red]Error: {e}[/red]")
                raise typer.Exit(1)
            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                raise typer.Exit(1)

        elif action == "ls":
            templates = manager.list_templates()
            if not templates:
                console.print("[dim]No templates found[/dim]")
            else:
                console.print("[bold]Available templates:[/bold]")
                for tmpl in templates:
                    console.print(f"  • {tmpl}")

        elif action == "rm":
            if not name:
                console.print("[red]Error: 'rm' action requires template name[/red]")
                console.print("[dim]Usage: cafe template rm <template-name>[/dim]")
                raise typer.Exit(1)

            try:
                manager.remove_template(name)
                console.print(f"[green]✅ Template '{name}' removed successfully[/green]")
            except FileNotFoundError as e:
                console.print(f"[red]Error: {e}[/red]")
                raise typer.Exit(1)

        elif action == "cat":
            if not source:
                console.print("[red]Error: 'cat' action requires template name[/red]")
                console.print("[dim]Usage: cafe template cat <template-name>[/dim]")
                raise typer.Exit(1)

            template_path = manager.get_template_path(source)
            if not template_path:
                console.print(f"[red]Error: Template '{source}' not found[/red]")
                raise typer.Exit(1)

            # Display template content using pager
            try:
                subprocess.run(["less", "-R", str(template_path)], check=False)
            except FileNotFoundError:
                # Fallback: print to console
                content = template_path.read_text()
                console.print(content)

        elif action == "edit":
            if not source:
                console.print("[red]Error: 'edit' action requires template name[/red]")
                console.print("[dim]Usage: cafe template edit <template-name>[/dim]")
                raise typer.Exit(1)

            template_path = manager.get_template_path(source)
            if not template_path:
                console.print(f"[red]Error: Template '{source}' not found[/red]")
                raise typer.Exit(1)

            # Open template in editor
            editor = os.environ.get("EDITOR", "vim")
            try:
                subprocess.run([editor, str(template_path)], check=True)
                console.print(f"[green]✅ Template '{source}' updated[/green]")
            except subprocess.CalledProcessError:
                console.print("[red]Error: Failed to edit template[/red]")
                raise typer.Exit(1)
            except FileNotFoundError:
                console.print(f"[red]Error: Editor '{editor}' not found[/red]")
                console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
                raise typer.Exit(1)

        else:
            console.print(f"[red]Error: Unknown action '{action}'[/red]")
            console.print("[dim]Valid actions: add, ls, rm, cat, edit[/dim]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@agent_app.command(name="ls")
def agent_ls() -> None:
    """List all available agents."""
    # Get agents directory from current working directory (project)
    agents_dir = Path.cwd() / ".cafe" / "agents"

    if not agents_dir.exists():
        console.print("[yellow]No agents found.[/yellow]")
        return

    # Get all role directories
    roles = ["pm", "developer", "reviewer"]
    has_agents = False

    # Create table
    table = Table(title="Available Agents", show_header=True, header_style="bold cyan")
    table.add_column("Role", style="green")
    table.add_column("Agent", style="yellow")
    table.add_column("Description", style="dim")

    for role in roles:
        role_dir = agents_dir / role
        if not role_dir.exists():
            continue

        # Get all .md files in role directory
        agent_files = sorted(role_dir.glob("*.md"))

        for agent_file in agent_files:
            has_agents = True
            agent_name = agent_file.stem

            # Try to extract description from frontmatter
            description = ""
            try:
                content = agent_file.read_text()
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        description = frontmatter.get("description", "")
            except Exception:
                pass

            table.add_row(role, agent_name, description)

    if not has_agents:
        console.print("[yellow]No agents found.[/yellow]")
        return

    console.print(table)


@agent_app.command(name="rm")
def agent_rm() -> None:
    """Remove an agent interactively."""
    # Get agents directory from current working directory (project)
    agents_dir = Path.cwd() / ".cafe" / "agents"

    # Prompt for role
    try:
        role = prompt_list(
            message="Select agent role:",
            choices=["pm", "developer", "reviewer"],
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Get agents in this role
    role_dir = agents_dir / role
    if not role_dir.exists():
        console.print(f"[red]No agents found in role '{role}'[/red]")
        raise typer.Exit(1)

    agent_files = sorted([f.name for f in role_dir.glob("*.md")])
    if not agent_files:
        console.print(f"[red]No agents found in role '{role}'[/red]")
        raise typer.Exit(1)

    # Prompt for agent
    try:
        agent_filename = prompt_list(
            message="Select agent to delete:",
            choices=agent_files,
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    agent_file = role_dir / agent_filename
    agent_path = f"{role}/{agent_filename}"

    # Confirm deletion
    try:
        confirm = prompt_confirm(f"Are you sure you want to delete agent '{agent_path}'?", default=False)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    if not confirm:
        console.print("[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Delete the agent file
    try:
        agent_file.unlink()
        console.print(f"[green]✓[/green] Agent '{agent_path}' deleted successfully")
    except Exception as e:
        console.print(f"[red]Error: Failed to delete agent: {e}[/red]")
        raise typer.Exit(1)


@agent_app.command(name="create")
def agent_create() -> None:
    """Create a new agent interactively."""
    # Get agents directory from current working directory (project)
    agents_dir = Path.cwd() / ".cafe" / "agents"

    # Prompt for role
    try:
        role = prompt_list(
            message="Select agent role:",
            choices=["pm", "developer", "reviewer"],
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Prompt for name
    try:
        name = prompt_text(
            message="Agent name (eg: Michael):",
            default="",
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Strip whitespace from name
    name = name.strip()
    if not name:
        console.print("[red]Error: Agent name cannot be empty[/red]")
        raise typer.Exit(1)

    # Check if agent already exists
    agent_file = agents_dir / role / f"{name}.md"
    if agent_file.exists():
        console.print(f"[red]Error: Agent '{role}/{name}.md' already exists[/red]")
        raise typer.Exit(1)

    # Prompt for description
    try:
        description = prompt_text(
            message="Description (eg: A senior Rust developer):",
            default="",
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Strip whitespace from description
    description = description.strip()
    if not description:
        console.print("[red]Error: Description cannot be empty[/red]")
        raise typer.Exit(1)

    # Prompt for code of conduct (using editor)
    editor = os.environ.get("EDITOR", "vim")

    # Create temp file with agent template
    template_content = f"""---
name: {name}
description: {description}
---

# Please write the agent's code of conduct below
# Delete this comment and write the agent's behavior guidelines and responsibilities
#
# Example:
# You are a {description}.
# Your responsibilities include:
# - Writing clean and maintainable code
# - Following best practices and coding standards
# - Providing helpful and accurate responses

"""

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tf:
        tf.write(template_content)
        temp_path = tf.name

    try:
        # Open editor for code of conduct
        subprocess.run([editor, temp_path], check=True)

        # Read the entire agent file content (including frontmatter)
        with open(temp_path, "r") as f:
            content = f.read().strip()

        # Remove template comments if user didn't modify
        if "# Please write the agent's code of conduct below" in content:
            # Remove comment lines
            lines = [line for line in content.split('\n') if not (line.strip().startswith('#') and 'Please write' in line or 'Delete this comment' in line or 'Example:' in line or 'Your responsibilities' in line or line.strip().startswith('# - '))]
            content = '\n'.join(lines).strip()
    finally:
        # Clean up temp file
        os.unlink(temp_path)

    # Ensure directory exists
    agent_file.parent.mkdir(parents=True, exist_ok=True)

    # Write agent file
    agent_file.write_text(content)

    # Show relative path from current directory
    relative_path = agent_file.relative_to(Path.cwd())
    console.print(f"[green]✓[/green] Agent created successfully: {relative_path}")


@agent_app.command(name="edit")
def agent_edit() -> None:
    """Edit an existing agent."""
    # Get agents directory from current working directory (project)
    agents_dir = Path.cwd() / ".cafe" / "agents"

    # Prompt for role
    try:
        role = prompt_list(
            message="Select agent role:",
            choices=["pm", "developer", "reviewer"],
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Get agents in this role
    role_dir = agents_dir / role
    if not role_dir.exists():
        console.print(f"[red]No agents found in role '{role}'[/red]")
        raise typer.Exit(1)

    agent_files = sorted([f.name for f in role_dir.glob("*.md")])
    if not agent_files:
        console.print(f"[red]No agents found in role '{role}'[/red]")
        raise typer.Exit(1)

    # Prompt for agent
    try:
        agent_filename = prompt_list(
            message="Select agent to edit:",
            choices=agent_files,
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    agent_file = role_dir / agent_filename

    # Open editor
    editor = os.environ.get("EDITOR", "vim")
    try:
        subprocess.run([editor, str(agent_file)], check=True)
        # Show relative path from current directory
        relative_path = agent_file.relative_to(Path.cwd())
        console.print(f"[green]✓[/green] Agent updated successfully: {relative_path}")
    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit agent[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        raise typer.Exit(1)
