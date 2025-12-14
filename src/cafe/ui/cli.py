import typer
from rich.console import Console
import os
from collections import defaultdict

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


app.add_typer(agent_app)
# --- End Agent Command Group ---