"""Agent management command implementations extracted from cli.py."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console

from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text
from cafe.ui.init_helpers import list_available_agents

# ---------------------------------------------------------------------------
# Sentinel constants & role-phase mapping (agent-setup flow)
# ---------------------------------------------------------------------------
BACK_SENTINEL = "__BACK__"
CUSTOM_MODEL_SENTINEL = "__CUSTOM__"
KEEP_MODEL_SENTINEL = "__KEEP__"
SAVE_SENTINEL = "save"
ROLE_PHASES = {
    "pm": ["spec"],
    "developer": ["plan", "develop", "pr"],
    "reviewer": ["review"],
}

# ---------------------------------------------------------------------------
# Typer sub-app
# ---------------------------------------------------------------------------
agent_app = typer.Typer(help="Manage agents")

# ---------------------------------------------------------------------------
# Runtime bridge – injected from cafe.ui.cli at import time
# ---------------------------------------------------------------------------
console = Console()


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """No-op retained for backward compatibility.

    Runtime dependencies are now imported directly or defined locally.
    """


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _has_complete_agent_setup(agents_config: dict) -> bool:
    """Return True if role setup has minimum required fields for selective editing."""
    required_roles = ["pm", "developer", "reviewer"]
    if not isinstance(agents_config, dict):
        return False

    for role_key in required_roles:
        role_config = agents_config.get(role_key)
        if not isinstance(role_config, dict):
            return False
        if not role_config.get("cli") or not role_config.get("name"):
            return False

    return True


def _interactive_agent_setup_selective(existing_agents_config: dict, available_clis: list) -> dict:
    """Run selective agent configuration with explicit Save action."""
    from InquirerPy.separator import Separator

    role_choices = [
        {"name": "PM", "value": "pm"},
        {"name": "Developer", "value": "developer"},
        {"name": "Reviewer", "value": "reviewer"},
        Separator(),
        {"name": "Save", "value": SAVE_SENTINEL},
    ]

    role_display_map = {
        "pm": "PM",
        "developer": "Developer",
        "reviewer": "Reviewer",
    }

    staged_agents_config = copy.deepcopy(existing_agents_config)

    while True:
        selected_role = prompt_list(
            message="Select role to update:",
            choices=role_choices,
        )

        if selected_role == SAVE_SENTINEL:
            return staged_agents_config

        role_display = role_display_map.get(selected_role)
        if not role_display:
            console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
            raise typer.Exit(1)

        staged_agents_config[selected_role] = _interactive_role_setup(
            role_key=selected_role,
            role_display=role_display,
            available_clis=available_clis,
            existing_role_config=staged_agents_config.get(selected_role),
            allow_back=True,
        )
        if staged_agents_config[selected_role] == BACK_SENTINEL:
            staged_agents_config[selected_role] = copy.deepcopy(existing_agents_config.get(selected_role))
            continue
        console.print("")


def _interactive_agent_setup(available_clis: list) -> dict:
    """Run interactive agent configuration for all three roles.

    Prompts user to select CLI, agent, and phase-specific models for each role.
    Supports back navigation within each role's configuration steps.

    Args:
        available_clis: List of available CLI names

    Returns:
        Agents configuration dictionary

    Raises:
        typer.Exit: If user cancels or agents not found
    """
    agents_config = {}
    roles = [("pm", "PM"), ("developer", "Developer"), ("reviewer", "Reviewer")]

    for role_key, role_display in roles:
        agents_config[role_key] = _interactive_role_setup(
            role_key=role_key,
            role_display=role_display,
            available_clis=available_clis,
        )
        console.print("")

    return agents_config


def _interactive_role_setup(
    role_key: str,
    role_display: str,
    available_clis: list,
    existing_role_config: Optional[dict] = None,
    allow_back: bool = False,
) -> dict | str:
    """Run interactive setup for a single role."""
    from InquirerPy.separator import Separator

    phase_recommendations = {
        "spec": "high-speed / economical models",
        "plan": "smarter models",
        "develop": "smarter models",
        "review": "flexible based on your needs",
        "pr": "high-speed / economical models",
    }

    console.print(f"[bold cyan]Configuring {role_display} role:[/bold cyan]")

    phases = ROLE_PHASES.get(role_key, [])

    # Steps: 0=CLI, 1=agent, 2..N=phase models
    step = 0
    total_steps = 2 + len(phases)
    selected_cli = None
    selected_agent_name = None
    phase_models = {}
    if isinstance(existing_role_config, dict):
        for phase in phases:
            phase_config = existing_role_config.get(phase)
            if isinstance(phase_config, dict):
                model = phase_config.get("model")
                if isinstance(model, str) and model.strip():
                    phase_models[phase] = model.strip()

    while step < total_steps:
        if step == 0:
            cli_choices = list(available_clis)
            if allow_back:
                cli_choices.extend([
                    Separator(),
                    {"name": "\u2190 Back", "value": BACK_SENTINEL},
                ])

            selected_cli = prompt_list(
                message=f"Select CLI for {role_display}:",
                choices=cli_choices,
            )
            if selected_cli == BACK_SENTINEL:
                return BACK_SENTINEL
            if not selected_cli:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)
            step += 1
            continue

        if step == 1:
            agents = list_available_agents(role_key)

            if not agents:
                console.print(f"[red]Error: Agent files not found for {role_display} role.[/red]")
                console.print(
                    f"[yellow]Please ensure valid .md files exist in ~/.cafe/agents/{role_key}/ or src/cafe/data/agents/{role_key}/ directory.[/yellow]"
                )
                raise typer.Exit(1)

            agent_choices = []
            for name, desc, _, source_type in agents:
                source_label = " (custom)" if source_type == "custom" else " (system default)"
                agent_choices.append(f"{name}: {desc}{source_label}")

            agent_choices.append(Separator())
            agent_choices.append({"name": "\u2190 Back to CLI selection", "value": BACK_SENTINEL})

            selected = prompt_list(
                message=f"Select agent for {role_display}:",
                choices=agent_choices,
            )

            if selected == BACK_SENTINEL:
                step = 0
                continue

            if not selected:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)

            selected_agent_name = selected.split(":")[0].strip()
            step += 1
            continue

        phase_idx = step - 2
        phase = phases[phase_idx]
        recommendation = phase_recommendations.get(phase, "")
        recommendation_text = f" (recommended: {recommendation})" if recommendation else ""

        model_choices = [
            {"name": "Use default model", "value": ""},
            {"name": "Keep current setting", "value": KEEP_MODEL_SENTINEL},
            {"name": "Custom (type model name)", "value": CUSTOM_MODEL_SENTINEL},
            Separator(),
            {"name": "\u2190 Back", "value": BACK_SENTINEL},
        ]

        selected = prompt_list(
            message=f"Model for {phase} phase{recommendation_text}:",
            choices=model_choices,
        )

        if selected == BACK_SENTINEL:
            step -= 1
            continue

        if selected == CUSTOM_MODEL_SENTINEL:
            model_name = prompt_text(
                message=f"Enter {selected_cli} model name for {phase} phase:",
                default="",
            )
            if model_name and model_name.strip():
                phase_models[phase] = model_name.strip()
            else:
                phase_models.pop(phase, None)
        elif selected == KEEP_MODEL_SENTINEL:
            # Preserve current phase override (or default) without changes.
            pass
        else:
            # "Use default model" clears any existing phase-specific override.
            phase_models.pop(phase, None)

        step += 1

    role_config = {
        "name": selected_agent_name,
        "cli": selected_cli,
    }
    for phase, model in phase_models.items():
        role_config[phase] = {"model": model}

    if not isinstance(existing_role_config, dict):
        return role_config

    # Preserve non-interactive role-level settings (for example backup/models)
    # while replacing editable fields from the setup flow.
    merged_role_config = copy.deepcopy(existing_role_config)
    merged_role_config["name"] = role_config["name"]
    merged_role_config["cli"] = role_config["cli"]

    for phase in phases:
        merged_role_config.pop(phase, None)
    for phase, model_config in role_config.items():
        if phase in phases:
            merged_role_config[phase] = model_config

    return merged_role_config


def _display_agent_summary(agents_config: dict) -> None:
    """Display a summary of agent configuration.

    Args:
        agents_config: Agents configuration dictionary
    """
    roles = [
        ("pm", "PM"),
        ("developer", "Developer"),
        ("reviewer", "Reviewer"),
    ]

    for role_key, role_display in roles:
        role_config = agents_config[role_key]
        phases = ROLE_PHASES.get(role_key, [])

        # Build phase models display
        phase_models = []
        for phase in phases:
            if phase in role_config and "model" in role_config[phase]:
                phase_models.append(f"{phase}={role_config[phase]['model']}")
            else:
                phase_models.append(f"{phase}=default")

        models_display = ", ".join(phase_models) if phase_models else "default"

        console.print(
            f"- {role_display}: {role_config['cli']} "
            f"(agent: {role_config['name']}, models: {models_display})"
        )


# ---------------------------------------------------------------------------
# Agent commands
# ---------------------------------------------------------------------------

def _print_agents(custom_only: bool = False) -> None:
    """Print agents table. Used by agent ls, edit, rm."""
    from rich.table import Table

    roles = ["pm", "developer", "reviewer"]
    has_agents = False

    table = Table(title="Custom Agents" if custom_only else "Available Agents", show_header=True, header_style="bold cyan")
    table.add_column("Role", style="green")
    table.add_column("Agent", style="yellow")
    table.add_column("Description", style="dim")
    table.add_column("Source", style="dim")

    for role in roles:
        for agent_name, description, _, source_type in list_available_agents(role):
            if custom_only and source_type == "system":
                continue
            has_agents = True
            table.add_row(role, agent_name, description, source_type)

    if not has_agents:
        console.print(f"[yellow]No {'custom ' if custom_only else ''}agents found.[/yellow]")
        return

    console.print(table)


@agent_app.command(name="ls")
def agent_ls(
    custom_only: bool = typer.Option(False, "--custom-only", help="Show only custom agents"),
) -> None:
    """List all available agents (system and custom)."""
    _print_agents(custom_only)


@agent_app.command(name="rm")
def agent_rm() -> None:
    """Remove an agent interactively."""
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

    _print_agents(custom_only=True)
    console.print()

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
    agent_files = sorted([f.name for f in role_dir.glob("*.md")]) if role_dir.exists() else []
    if not agent_files:
        console.print(f"[yellow]No custom agents found in role '{role}'[/yellow]")
        console.print(f"[dim]Use 'cafe agent add' to create a custom agent.[/dim]")
        raise typer.Exit(0)

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
    from pathlib import Path
    import os
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

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
        console.print("[yellow]Use 'cafe agent edit' to modify the existing agent.[/yellow]")
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
    import tempfile

    # Create temp file with agent template
    template_content = f"""---
name: {name}
description: {description}
---

# Please write the agent's code of conduct below
# Delete this comment and write the agent's behavior guidelines and responsibilities
#
# IMPORTANT: Each guideline MUST start with "-" to maximize effectiveness
#
# Example:
# You are a {description}.
# Your responsibilities include:
# - Use camelCase for variable names (e.g., userName, not user_name)
# - Always add JSDoc comments for public functions
# - Prefer async/await over Promise.then() chains
# - Write unit tests in __tests__/ directory using Jest
# - Follow the project's existing error handling patterns

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

    # Show path relative to home directory
    try:
        relative_path = agent_file.relative_to(Path.home())
        console.print(f"[green]✓[/green] Agent created successfully: ~/{relative_path}")
    except ValueError:
        console.print(f"[green]✓[/green] Agent created successfully: {agent_file}")


@agent_app.command(name="edit")
def agent_edit() -> None:
    """Edit an existing agent."""
    from pathlib import Path
    import os
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

    _print_agents(custom_only=True)
    console.print()

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
    agent_files = sorted([f.name for f in role_dir.glob("*.md")]) if role_dir.exists() else []
    if not agent_files:
        console.print(f"[yellow]No custom agents found in role '{role}'[/yellow]")
        console.print(f"[dim]Use 'cafe agent add' to create a custom agent.[/dim]")
        raise typer.Exit(0)

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
        # Show path relative to home directory
        try:
            relative_path = agent_file.relative_to(Path.home())
            console.print(f"[green]✓[/green] Agent updated successfully: ~/{relative_path}")
        except ValueError:
            console.print(f"[green]✓[/green] Agent updated successfully: {agent_file}")

    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit agent[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        raise typer.Exit(1)


@agent_app.command(name="cat")
def agent_cat(
    role: Optional[str] = typer.Option(None, "--role", "-r", help="Agent role: pm, developer, or reviewer"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Agent name to view"),
) -> None:
    """View agent content.

    \b
    Examples:
        cafe agent cat --role developer --name Nick
        cafe agent cat  # Interactive mode
    """
    # Interactive prompting for missing arguments
    try:
        if not role:
            role = prompt_list(
                message="Select agent role:",
                choices=["pm", "developer", "reviewer"],
            )

        # Validate role
        if role not in ["pm", "developer", "reviewer"]:
            console.print(f"[red]Error: Invalid role '{role}'. Must be 'pm', 'developer', or 'reviewer'.[/red]")
            raise typer.Exit(1)

        if not name:
            # Get all agents for this role (system + custom)
            agents = list_available_agents(role)
            if not agents:
                console.print(f"[red]No agents found for role '{role}'[/red]")
                raise typer.Exit(1)

            # Create choices with source indicators
            choices = []
            agent_map = {}
            for agent_name, description, agent_path, source_type in agents:
                if source_type == "custom":
                    display_name = f"{agent_name} (custom)"
                else:
                    display_name = f"{agent_name} (system default)"
                choices.append(display_name)
                agent_map[display_name] = (agent_name, agent_path)

            selected = prompt_list(
                message="Select agent to view:",
                choices=choices,
            )
            name, agent_path = agent_map[selected]
        else:
            # Find agent by name
            agents = list_available_agents(role)
            agent_path = None
            for agent_name, _, path, _ in agents:
                if agent_name == name:
                    agent_path = path
                    break

            if not agent_path:
                console.print(f"[red]Error: Agent '{name}' not found in role '{role}'[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Display agent content using pager
    try:
        subprocess.run(["less", "-R", str(agent_path)], check=False)
    except FileNotFoundError:
        # Fallback: print to console
        content = agent_path.read_text()
        console.print(content)


@agent_app.command(name="sync")
def agent_sync() -> None:
    """Sync agent files from global/system sources to local .cafe directory.

    Updates all agent files in .cafe/agents to their latest versions from
    ~/.cafe/agents (custom) or src/cafe/data/agents (system default).
    Global custom agents take precedence over system defaults.
    """
    from cafe.ui.init_helpers import sync_agents

    # Check if .cafe directory exists
    cafe_dir = Path(".cafe")
    if not cafe_dir.exists():
        console.print("[red]Error: CAFE not initialized in this directory[/red]")
        console.print("[dim]Run 'cafe init' first[/dim]")
        raise typer.Exit(1)

    # Sync agents
    agent_success, agent_failed = sync_agents(cafe_dir)

    # Display summary
    if agent_success > 0:
        console.print(f"  [green]✓[/green] Updated .cafe directory with {agent_success} agent(s)")

    if agent_failed > 0:
        console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {agent_failed} agent file(s)")
