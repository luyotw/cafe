"""Command-line interface for AAF."""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode
from aaf.core.workflow import Workflow
from aaf.phases.analysis_phase import AnalysisPhase
from aaf.phases.implementation_phase import ImplementationPhase
from aaf.phases.pr_phase import PRPhase
from aaf.phases.spec_phase import SpecPhase
from aaf.phases.review_phase import ReviewPhase
from aaf.utils.config import ConfigManager

app = typer.Typer(
    name="aaf",
    help="AI Agent Flow - Automated development workflow with AI agents",
    no_args_is_help=True,
)
console = Console()


def _setup_agents(config_manager: ConfigManager) -> AgentManager:
    """Setup agent manager with default agents.

    Args:
        config_manager: Configuration manager

    Returns:
        Configured agent manager
    """
    agent_manager = AgentManager()

    # Get agent configurations from config or use defaults
    pm_config = config_manager.get("agents.pm", {
        "name": "Roger",
        "cli": "claude",
    })
    dev_config = config_manager.get("agents.developer", {
        "name": "David",
        "cli": "claude",
    })
    reviewer_config = config_manager.get("agents.reviewer", {
        "name": "Richard",
        "cli": "claude",
    })

    # Register agents
    agent_manager.register_agent(
        AgentConfig(
            name=pm_config["name"],
            cli=AgentCLI(pm_config["cli"]),
        )
    )
    agent_manager.register_agent(
        AgentConfig(
            name=dev_config["name"],
            cli=AgentCLI(dev_config["cli"]),
        )
    )
    agent_manager.register_agent(
        AgentConfig(
            name=reviewer_config["name"],
            cli=AgentCLI(reviewer_config["cli"]),
        )
    )

    return agent_manager


def _build_workflow(
    mode: WorkflowMode,
    requirements: str,
    issue_id: Optional[str],
    agent_manager: AgentManager,
    permission_handler: PermissionHandler,
    config_manager: ConfigManager,
) -> Workflow:
    """Build workflow with all phases.

    Args:
        mode: Workflow mode
        requirements: Requirements file path
        issue_id: GitHub issue ID
        agent_manager: Agent manager
        permission_handler: Permission handler
        config_manager: Configuration manager

    Returns:
        Configured workflow
    """
    # Get agent names from config
    pm_name = config_manager.get("agents.pm.name", "Roger")
    dev_name = config_manager.get("agents.developer.name", "David")
    reviewer_name = config_manager.get("agents.reviewer.name", "Richard")

    workflow = Workflow(max_retries=config_manager.get("workflow.max_retries", 0))

    # Spec phase: Requirements clarification
    workflow.add_phase(
        SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=requirements,
            workflow_mode=mode,
            issue_id=issue_id,
            pm_agent=pm_name,
        )
    )

    # Phase 2: Implementation analysis
    workflow.add_phase(
        AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=requirements,
            workflow_mode=mode,
            issue_id=issue_id,
            dev_agent=dev_name,
        )
    )

    # Phase 3: Development implementation
    git_ops = GitOperations()
    workflow.add_phase(
        ImplementationPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=requirements,
            workflow_mode=mode,
            issue_id=issue_id,
            dev_agent=dev_name,
        )
    )

    # Phase 4: Code review
    workflow.add_phase(
        ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=requirements,
            workflow_mode=mode,
            issue_id=issue_id,
            review_agent=reviewer_name,
            dev_agent=dev_name,
        )
    )

    # Phase 5: PR creation
    workflow.add_phase(
        PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            requirements_file=requirements,
            workflow_mode=mode,
            issue_id=issue_id,
        )
    )

    return workflow


@app.command()
def run(
    requirements: str = typer.Option(
        "requirements.md",
        "--requirements",
        "-r",
        help="Path to requirements file (for local mode)",
    ),
    mode: str = typer.Option(
        "github",
        "--mode",
        "-m",
        help="Workflow mode: 'local' or 'github'",
    ),
    issue_id: Optional[str] = typer.Option(
        None,
        "--issue",
        "-i",
        help="GitHub issue ID (required for github mode)",
    ),
    config_file: str = typer.Option(
        ".aaf/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Run the AAF workflow.

    Examples:
        # Local mode with requirements file
        aaf run -m local -r requirements.md

        # GitHub mode with issue
        aaf run -m github -i 123
    """
    try:
        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Validate issue_id for github mode
        if workflow_mode == WorkflowMode.GITHUB and not issue_id:
            console.print("[red]Error: --issue is required for github mode.[/red]")
            raise typer.Exit(1)

        # Validate requirements file for local mode
        if workflow_mode == WorkflowMode.LOCAL:
            req_path = Path(requirements)
            if not req_path.exists():
                console.print(f"[red]Error: Requirements file not found: {requirements}[/red]")
                raise typer.Exit(1)

        # Initialize components
        # ConfigManager takes config_dir, so extract the directory
        config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager)
        permission_handler = PermissionHandler()

        # Build and execute workflow
        console.print("[bold blue]Starting AAF workflow...[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        if workflow_mode == WorkflowMode.GITHUB:
            console.print(f"Issue: #{issue_id}")
        else:
            console.print(f"Requirements: {requirements}")

        workflow = _build_workflow(
            mode=workflow_mode,
            requirements=requirements,
            issue_id=issue_id,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            config_manager=config_manager,
        )

        results = workflow.execute()

        # Display results
        console.print("\n[bold]Workflow Results:[/bold]")
        for i, result in enumerate(results):
            status_color = {
                "completed": "green",
                "failed": "red",
                "skipped": "yellow",
            }.get(result.status.value, "white")

            console.print(
                f"Phase {i}: [{status_color}]{result.status.value.upper()}[/{status_color}]"
            )
            if result.message:
                console.print(f"  {result.message}")

        # Exit with error if any phase failed
        if any(r.status.value == "failed" for r in results):
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Workflow interrupted by user.[/yellow]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show AAF version."""
    console.print("AAF version 0.1.0")


@app.command()
def spec(
    output: str = typer.Option(
        "requirements.md",
        "--output",
        "-o",
        help="Output requirements file path (local mode)",
    ),
    mode: str = typer.Option(
        "local",
        "--mode",
        "-m",
        help="Workflow mode: local or github",
    ),
    issue_id: Optional[str] = typer.Option(
        None,
        "--issue",
        "-i",
        help="GitHub issue ID (github mode)",
    ),
    pm_agent: str = typer.Option(
        "Roger",
        "--pm",
        help="PM agent name",
    ),
    config_file: str = typer.Option(
        ".aaf/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Detect if stdin is redirected and use it for input",
    ),
) -> None:
    """Run specification phase: Requirements clarification with conversational generation.

    The PM agent will engage in a dialogue with you to clarify and generate
    a complete requirements document. No technical details will be discussed.

    Examples:
        # Generate requirements through conversation (local)
        aaf spec -o requirements.md

        # Create new GitHub issue with requirements
        aaf spec -m github

        # Update existing GitHub issue
        aaf spec -m github -i 123

        # Use custom PM agent
        aaf spec -o req.md --pm CustomPM
    """
    try:
        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager)
        permission_handler = PermissionHandler()

        # Display start message
        console.print("[bold blue]🎯 Spec Phase: Requirements Clarification[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"PM Agent: {pm_agent}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Output: {output}")
        elif issue_id:
            console.print(f"Issue: #{issue_id}")
        console.print()

        # Determine if should be interactive
        import sys
        is_interactive = not non_interactive and sys.stdin.isatty()

        # Create and execute spec phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=output,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            pm_agent=pm_agent,
            interactive=is_interactive,
        )

        console.print("[bold]Starting conversational requirements generation...[/bold]")
        console.print("[dim]The PM will ask questions to clarify all necessary information.[/dim]")
        console.print("[dim]Focus on WHAT you want, not HOW to implement it.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            console.print()
            console.print("[bold green]✅ Requirements clarification completed![/bold green]")
            console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
            if workflow_mode == WorkflowMode.LOCAL:
                console.print(f"Saved to: {output}")
            elif result.data.get('issue_id'):
                console.print(f"Created issue: #{result.data['issue_id']}")
            elif issue_id:
                console.print(f"Updated issue: #{issue_id}")
        else:
            console.print()
            console.print(f"[bold red]❌ Requirements phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Configuration key to get/set"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
    config_file: str = typer.Option(
        ".aaf/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    list_all: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="List all configuration",
    ),
) -> None:
    """Manage AAF configuration.

    Examples:
        # List all configuration
        aaf config --list

        # Get a configuration value
        aaf config agents.pm.name

        # Set a configuration value
        aaf config agents.pm.tool claude
    """
    # ConfigManager takes config_dir, so extract the directory
    config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
    config_manager = ConfigManager(config_dir)

    if list_all:
        # List all configuration
        import yaml
        loaded_config = config_manager.load_config()
        console.print(yaml.dump(loaded_config, default_flow_style=False))
    elif key and value:
        # Set configuration
        config_manager.set(key, value)  # set() already calls save_config()
        console.print(f"[green]Set {key} = {value}[/green]")
    elif key:
        # Get configuration
        val = config_manager.get(key)
        if val is None:
            console.print(f"[yellow]Key not found: {key}[/yellow]")
        else:
            console.print(f"{key} = {val}")
    else:
        console.print("[yellow]Use --list, or provide key [value][/yellow]")


def main() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
