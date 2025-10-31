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
from aaf.phases.plan_phase import PlanPhase
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


def _setup_agents(config_manager: ConfigManager, issue_name: Optional[str] = None) -> AgentManager:
    """Setup agent manager with default agents.

    Args:
        config_manager: Configuration manager
        issue_name: Issue name for issue-specific sessions

    Returns:
        Configured agent manager
    """
    agent_manager = AgentManager(issue_name=issue_name)

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
    spec_file: str,
    issue_id: Optional[str],
    agent_manager: AgentManager,
    permission_handler: PermissionHandler,
    config_manager: ConfigManager,
) -> Workflow:
    """Build workflow with all phases.

    Args:
        mode: Workflow mode
        spec_file: Specification file path
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

    # Spec phase: Specification clarification
    workflow.add_phase(
        SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=mode,
            issue_id=issue_id,
            pm_agent=pm_name,
        )
    )

    # Phase 2: Implementation plan
    workflow.add_phase(
        PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
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
            spec_file=spec_file,
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
            spec_file=spec_file,
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
            spec_file=spec_file,
            workflow_mode=mode,
            issue_id=issue_id,
        )
    )

    return workflow


@app.command()
def run(
    spec_file: str = typer.Option(
        "spec.md",
        "--spec",
        "-s",
        help="Path to specification file (for local mode)",
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
        # Local mode with spec file
        aaf run -m local -s spec.md

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

        # Validate spec file for local mode
        if workflow_mode == WorkflowMode.LOCAL:
            spec_path = Path(spec_file)
            if not spec_path.exists():
                console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
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
            console.print(f"Spec file: {spec_file}")

        workflow = _build_workflow(
            mode=workflow_mode,
            spec_file=spec_file,
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
    issue_name: str = typer.Argument(
        ...,
        help="Issue name (will be saved to .aaf/issues/{issue-name}/spec/spec.md)",
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
    rigor: Optional[str] = typer.Option(
        None,
        "--rigor",
        "-r",
        help="Specification rigor level: low, medium, or high (will prompt if not specified)",
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
    """Run specification phase: Spec clarification with conversational generation.

    The PM agent will engage in a dialogue with you to clarify and generate
    a complete specification document. No technical details will be discussed.

    Examples:
        # Generate spec through conversation for "user-auth" issue
        aaf spec user-auth

        # Generate spec for "new-feature" issue
        aaf spec new-feature

        # Create new GitHub issue with spec
        aaf spec my-feature -m github

        # Update existing GitHub issue
        aaf spec my-feature -m github -i 123

        # Use custom PM agent
        aaf spec my-feature --pm CustomPM

        # Specify rigor level
        aaf spec my-feature --rigor low
    """
    try:
        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Validate rigor (if specified)
        spec_rigor = None
        if rigor:
            try:
                from aaf.core.types import SpecRigor
                spec_rigor = SpecRigor(rigor)
            except ValueError:
                console.print(f"[red]Error: Invalid rigor '{rigor}'. Use 'low', 'medium', or 'high'.[/red]")
                raise typer.Exit(1)

        # Build spec file path: .aaf/issues/{issue-name}/spec/spec.md
        spec_file = f".aaf/issues/{issue_name}/spec/spec.md"

        # Create directory if it doesn't exist
        spec_dir = Path(spec_file).parent
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()

        # Display start message
        console.print("[bold blue]🎯 Spec Phase: Specification Clarification[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"PM Agent: {pm_agent}")
        if spec_rigor:
            console.print(f"Rigor: {spec_rigor.value}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec file: {spec_file}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        console.print()

        # Determine if should be interactive
        import sys
        is_interactive = not non_interactive and sys.stdin.isatty()

        # Create and execute spec phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            pm_agent=pm_agent,
            interactive=is_interactive,
            rigor=spec_rigor,
        )

        console.print("[bold]Starting conversational spec generation...[/bold]")
        console.print("[dim]The PM will ask questions to clarify all necessary information.[/dim]")
        console.print("[dim]Focus on WHAT you want, not HOW to implement it.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            console.print()
            console.print("[bold green]✅ Spec clarification completed![/bold green]")
            console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
            if workflow_mode == WorkflowMode.LOCAL:
                console.print(f"Saved to: {spec_file}")
            elif result.data.get('issue_id'):
                console.print(f"Created issue: #{result.data['issue_id']}")
            elif issue_id:
                console.print(f"Updated issue: #{issue_id}")
        else:
            console.print()
            console.print(f"[bold red]❌ Spec phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def plan(
    issue_name: str = typer.Argument(
        ...,
        help="Issue name (reads spec from .aaf/issues/{issue-name}/spec/spec.md)",
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
    dev_agent: str = typer.Option(
        "David",
        "--dev",
        help="Developer agent name",
    ),
    config_file: str = typer.Option(
        ".aaf/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Run plan phase: Implementation planning with developer agent.

    The developer agent will analyze the specification and create a detailed
    implementation plan with technical considerations and development guide.

    Examples:
        # Analyze spec and create plan for "user-auth" issue
        aaf plan user-auth

        # Analyze spec for "new-feature" issue
        aaf plan new-feature

        # Analyze GitHub issue and create plan
        aaf plan my-feature -m github -i 123

        # Use custom developer agent
        aaf plan my-feature --dev CustomDev
    """
    try:
        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Build spec file path: .aaf/issues/{issue-name}/spec/spec.md
        spec_file = f".aaf/issues/{issue_name}/spec/spec.md"

        # Check if spec file exists
        if not Path(spec_file).exists():
            console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
            console.print(f"[dim]Hint: Run 'aaf spec {issue_name}' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()

        # Display start message
        console.print("[bold blue]📋 Plan Phase: Implementation Planning[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec file: {spec_file}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        console.print()

        # Create and execute plan phase
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            dev_agent=dev_agent,
        )

        console.print("[bold]Starting implementation planning...[/bold]")
        console.print("[dim]The developer will analyze technical feasibility and create implementation plan.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            console.print()
            console.print("[bold green]✅ Implementation plan completed![/bold green]")
            console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
        else:
            console.print()
            console.print(f"[bold red]❌ Plan phase failed: {result.message}[/bold red]")
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


@app.command(name="ls")
def list_issues() -> None:
    """List all issues."""
    from rich.table import Table

    issues_dir = Path(".aaf/issues")

    if not issues_dir.exists():
        console.print("[yellow]No issues directory found[/yellow]")
        console.print("Run 'aaf run <issue-name>' to create your first issue")
        return

    # Get all issue directories
    issues = [d for d in issues_dir.iterdir() if d.is_dir()]

    if not issues:
        console.print("[yellow]No issues found[/yellow]")
        console.print("Run 'aaf run <issue-name>' to create your first issue")
        return

    # Create table
    table = Table(title="AAF Issues", show_header=True, header_style="bold cyan")
    table.add_column("Issue Name", style="green")
    table.add_column("Phases", style="dim")
    table.add_column("Modified", style="dim")

    for issue in sorted(issues, key=lambda x: x.stat().st_mtime, reverse=True):
        # Check which phases exist
        phases = []
        for phase in ["spec", "analysis", "implementation", "review", "pr"]:
            phase_dir = issue / phase
            if phase_dir.exists():
                phases.append(phase)

        phases_str = ", ".join(phases) if phases else "empty"

        # Get last modified time
        import datetime
        mtime = datetime.datetime.fromtimestamp(issue.stat().st_mtime)
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")

        table.add_row(issue.name, phases_str, mtime_str)

    console.print(table)
    console.print(f"\n[dim]Total: {len(issues)} issue(s)[/dim]")


@app.command(name="rm")
def remove_issue(
    issue_name: str = typer.Argument(..., help="Name of the issue to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove an issue and all its data."""
    import shutil

    issue_path = Path(".aaf/issues") / issue_name

    if not issue_path.exists():
        console.print(f"[red]Issue '{issue_name}' not found[/red]")
        console.print("\nRun 'aaf ls' to see available issues")
        raise typer.Exit(1)

    # Show what will be deleted
    if not force:
        console.print(f"[yellow]About to delete issue: {issue_name}[/yellow]")
        console.print(f"[dim]Path: {issue_path}[/dim]\n")

        confirm = typer.confirm("Are you sure you want to delete this issue?")
        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Delete the issue directory
    try:
        shutil.rmtree(issue_path)
        console.print(f"[green]✓[/green] Issue '{issue_name}' deleted successfully")
    except Exception as e:
        console.print(f"[red]Failed to delete issue: {e}[/red]")
        raise typer.Exit(1)


def main() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
