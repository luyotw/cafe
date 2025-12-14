import os
import subprocess
from collections import defaultdict
from pathlib import Path

import inquirer
import typer
from rich.console import Console

from cafe.agents.manager import AgentManager

app = typer.Typer(
    name="cafe",
    help="AI Agent Flow - Automated development workflow with AI agents",
    no_args_is_help=True,
)
console = Console()

# --- Agent Command Group ---
agent_app = typer.Typer(
    name="agent",
    help="Manage agents for different roles (pm, developer, reviewer).",
    no_args_is_help=True,
)


@agent_app.command("ls", help="List all available agents.")
def agent_ls():
    """Lists all available agents, grouped by role."""
    try:
        manager = AgentManager()
        agents = manager.list_agents()
        if not agents:
            console.print("No agents found in ~/.cafe/agents/")
            return

        console.print("[bold]Available agents:[/bold]")
        grouped_agents = defaultdict(list)
        for agent_path in agents:
            try:
                role, agent_name = os.path.split(agent_path)
                if role:
                    grouped_agents[role].append(agent_name)
            except ValueError:
                console.print(f"[yellow]Warning: Could not parse agent path: {agent_path}[/yellow]")

        for role, agent_names in sorted(grouped_agents.items()):
            console.print(f"  [cyan]{role}/[/cyan]")
            for agent_name in sorted(agent_names):
                console.print(f"    - {agent_name}")

    except Exception as e:
        console.print(f"[red]Error listing agents: {e}[/red]")
        raise typer.Exit(1)


@agent_app.command("rm", help="Remove an agent.")
def agent_rm(
    agent_name: str = typer.Argument(..., help="Agent name (e.g., pm/Roger.md or developer/David.md)")
):
    """Remove an agent file."""
    try:
        manager = AgentManager()
        
        if not manager.agent_exists(agent_name):
            console.print(f"[red]Error: Agent '{agent_name}' not found.[/red]")
            raise typer.Exit(1)

        if not typer.confirm(f"Are you sure you want to remove agent '{agent_name}'?"):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

        manager.remove_agent(agent_name)
        console.print(f"[green]✅ Agent '{agent_name}' removed successfully.[/green]")

    except FileNotFoundError:
        console.print(f"[red]Error: Agent '{agent_name}' not found.[/red]")
        raise typer.Exit(1)
    except PermissionError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error removing agent: {e}[/red]")
        raise typer.Exit(1)


@agent_app.command("create", help="Create a new agent interactively.")
def agent_create():
    """Create a new agent file interactively."""
    try:
        manager = AgentManager()
        
        questions = [
            inquirer.List("role", message="Select a role", choices=["pm", "developer", "reviewer"]),
            inquirer.Text("name", message="Agent name (e.g., Michael)"),
            inquirer.Text("description", message="Description (e.g., A senior Rust developer)"),
            inquirer.Editor("conduct", message="Code of Conduct (opens editor for multi-line input)"),
        ]
        answers = inquirer.prompt(questions)
        if not answers:
            console.print("\n[yellow]Agent creation cancelled.[/yellow]")
            raise typer.Exit(0)

        role, name, description, conduct = (
            answers["role"],
            answers["name"].strip(),
            answers["description"].strip(),
            answers["conduct"].strip(),
        )

        if not name:
            console.print("[red]Error: Agent name cannot be empty.[/red]")
            raise typer.Exit(1)

        new_path = manager.create_agent(role, name, description, conduct)
        console.print(f"[green]✅ Agent '{name}' created successfully at:[/green] {new_path}")

    except FileExistsError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Agent creation cancelled.[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}[/red]")
        raise typer.Exit(1)


def _edit_file_with_editor(file_path: Path) -> None:
    """Open a file in the user's editor.

    Args:
        file_path: Path to the file to edit

    Raises:
        typer.Exit: If editor is not found or execution fails
    """
    # Use EDITOR env var, or fallback to vim
    editor = os.environ.get("EDITOR", "vim")

    try:
        subprocess.run([editor, str(file_path)], check=True)
    except subprocess.CalledProcessError:
        console.print(f"[red]Error: Editor '{editor}' closed with an error.[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found.[/red]")
        console.print("[dim]Set your EDITOR environment variable or install a default editor like vim.[/dim]")
        raise typer.Exit(1)


@agent_app.command("edit", help="Edit an existing agent.")
def agent_edit():
    """Edit an existing agent file."""
    try:
        manager = AgentManager()
        
        role_question = [
            inquirer.List("role", message="Select a role to edit from", choices=["pm", "developer", "reviewer"])
        ]
        role_answer = inquirer.prompt(role_question)
        if not role_answer:
            console.print("\n[yellow]Operation cancelled.[/yellow]")
            raise typer.Exit(0)
        
        role = role_answer['role']
        agents = manager.list_agents_by_role(role)

        if not agents:
            console.print(f"[yellow]No agents found for role '{role}'.[/yellow]")
            console.print("[dim]Use 'cafe agent create' to add one.[/dim]")
            raise typer.Exit(0)

        agent_question = [
            inquirer.List("agent_name", message=f"Select an agent from '{role}' to edit", choices=agents)
        ]
        agent_answer = inquirer.prompt(agent_question)
        if not agent_answer:
            console.print("\n[yellow]Operation cancelled.[/yellow]")
            raise typer.Exit(0)
            
        agent_name = agent_answer['agent_name']
        agent_path = manager.get_agent_path(role, agent_name)

        if not agent_path:
            # This should not happen if the list is correct, but as a safeguard
            console.print(f"[red]Error: Could not find agent file for '{agent_name}'.[/red]")
            raise typer.Exit(1)

        _edit_file_with_editor(agent_path)
        console.print(f"[green]✅ Agent '{role}/{agent_name}.md' updated successfully.[/green]")
        
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled.[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(agent_app)
# --- End Agent Command Group ---