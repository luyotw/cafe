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
from aaf.phases.develop_phase import DevelopPhase
from aaf.phases.pr_phase import PRPhase
from aaf.phases.spec_phase import SpecPhase
from aaf.phases.review_phase import ReviewPhase
from aaf.utils.config import ConfigManager
from aaf.utils.template import TemplateManager
from aaf.ui.template_selector import select_template

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
        "cli": "copilot",
    })
    dev_config = config_manager.get("agents.developer", {
        "name": "David",
        "cli": "copilot",
    })
    reviewer_config = config_manager.get("agents.reviewer", {
        "name": "Richard",
        "cli": "copilot",
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
            interactive=True,
        )
    )

    # Phase 3: Development
    git_ops = GitOperations()
    workflow.add_phase(
        DevelopPhase(
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

        # Get PM agent CLI
        pm_executor = agent_manager.get_agent(pm_agent)
        pm_cli = pm_executor.config.cli.value

        # Display start message
        console.print("[bold blue]🎯 Spec Phase: Specification Clarification[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"PM Agent: {pm_agent} (by {pm_cli})")
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
        if is_interactive:
            console.print("[dim]💡 Tip: Press Ctrl+C anytime to pause and save progress.[/dim]")
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
    template: Optional[str] = typer.Option(
        None,
        "--template",
        "-t",
        help="Plan template name (if not specified, will prompt interactively)",
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

        # Get developer agent CLI
        dev_executor = agent_manager.get_agent(dev_agent)
        dev_cli = dev_executor.config.cli.value

        # Handle template selection
        template_manager = TemplateManager(config_dir)
        selected_template = None

        if template:
            # Template specified via --template option
            if not template_manager.template_exists(template):
                console.print(f"[red]Error: Template '{template}' not found[/red]")
                console.print("[dim]Use 'aaf template list' to see available templates[/dim]")
                raise typer.Exit(1)
            selected_template = template
        else:
            # Interactive template selection
            templates = template_manager.list_templates()
            if not templates:
                console.print("[yellow]Warning: No templates found[/yellow]")
                console.print("[dim]You can add templates with 'aaf template add <source> <name>'[/dim]")
            else:
                # Always use interactive selector (even with single template)
                template_paths = {name: template_manager.get_template_path(name) for name in templates}
                selected_template = select_template(templates, template_paths)
                if selected_template:
                    console.print(f"[dim]Using template: {selected_template}[/dim]")

        # Display start message
        console.print("[bold blue]📋 Plan Phase: Implementation Planning[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent} (by {dev_cli})")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec file: {spec_file}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        console.print()

        # Get template path if selected
        template_path_str = None
        if selected_template:
            template_path_obj = template_manager.get_template_path(selected_template)
            if template_path_obj:
                template_path_str = str(template_path_obj)

        # Create and execute plan phase
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            issue_name=issue_name,
            dev_agent=dev_agent,
            interactive=True,
            template_path=template_path_str,
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
            # Build plan file path
            plan_file = f".aaf/issues/{issue_name}/plan/plan.md"
            if Path(plan_file).exists():
                console.print(f"Saved to: {plan_file}")
        else:
            console.print()
            console.print(f"[bold red]❌ Plan phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def config(
    action: Optional[str] = typer.Argument(None, help="Action: set, get, edit, reset, or config key"),
    key: Optional[str] = typer.Argument(None, help="Configuration key"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
) -> None:
    """Manage AAF configuration.

    Examples:
        # Show all configuration
        aaf config

        # Set a configuration value (with alias support)
        aaf config set pm gemini
        aaf config set pm.cli gemini
        aaf config set agents.pm.cli gemini

        # Get a configuration value
        aaf config get pm
        aaf config get agents.pm.cli

        # Edit config file in editor
        aaf config edit

        # Reset to defaults
        aaf config reset
    """
    config_manager = ConfigManager()
    import yaml
    import subprocess
    import os

    # No arguments: show all config
    if not action:
        loaded_config = config_manager.load_config()
        console.print("[bold cyan]Current Configuration:[/bold cyan]")
        console.print(yaml.dump(loaded_config, default_flow_style=False))
        return

    # Sub-commands
    if action == "set":
        if not key or not value:
            console.print("[red]Error: 'set' requires both key and value[/red]")
            console.print("Usage: aaf config set <key> <value>")
            raise typer.Exit(1)

        config_manager.set(key, value)
        console.print(f"[green]✓ Set {key} = {value}[/green]")

    elif action == "get":
        if not key:
            console.print("[red]Error: 'get' requires a key[/red]")
            console.print("Usage: aaf config get <key>")
            raise typer.Exit(1)

        val = config_manager.get(key)
        if val is None:
            console.print(f"[yellow]Key not found: {key}[/yellow]")
        else:
            import json
            console.print(f"{key} = {json.dumps(val, indent=2)}")

    elif action == "edit":
        # Open config file in editor
        config_file = config_manager.config_file

        # Ensure config file exists
        if not config_file.exists():
            config_manager.save_config(config_manager.get_default_config())

        # Use EDITOR env var, or fallback to vim
        editor = os.environ.get('EDITOR', 'vim')

        try:
            subprocess.run([editor, str(config_file)], check=True)
            console.print(f"[green]✓ Config file edited: {config_file}[/green]")
        except subprocess.CalledProcessError:
            console.print(f"[red]Error: Failed to edit config[/red]")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print(f"[red]Error: Editor '{editor}' not found[/red]")
            console.print(f"[dim]Set EDITOR environment variable or install vim[/dim]")
            raise typer.Exit(1)

    elif action == "reset":
        confirm = typer.confirm("Reset configuration to defaults?")
        if confirm:
            config_manager.reset()
            console.print("[green]✓ Configuration reset to defaults[/green]")
        else:
            console.print("Cancelled")

    else:
        # Treat action as a key for backward compatibility
        # e.g., "aaf config pm" -> get pm
        val = config_manager.get(action)
        if val is None:
            console.print(f"[yellow]Key not found: {action}[/yellow]")
        else:
            import json
            console.print(f"{action} = {json.dumps(val, indent=2)}")


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


@app.command()
def template(
    action: str = typer.Argument(..., help="Action: add, list, or remove"),
    source: Optional[str] = typer.Argument(None, help="Source file path (for 'add' action)"),
    name: Optional[str] = typer.Argument(None, help="Template name (for 'add' or 'remove' action)"),
    config_file: str = typer.Option(
        ".aaf/config.yaml",
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
        aaf template add path/to/template.md my-template

        # List all templates
        aaf template ls

        # View template content
        aaf template cat my-template

        # Edit a template
        aaf template edit my-template

        # Remove a template
        aaf template rm my-template
    """
    try:
        config_dir = str(Path(config_file).parent) if config_file != ".aaf/config.yaml" else ".aaf"
        manager = TemplateManager(config_dir)

        if action == "add":
            if not source or not name:
                console.print("[red]Error: 'add' action requires both source file path and template name[/red]")
                console.print("[dim]Usage: aaf template add <source-file> <template-name>[/dim]")
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
                console.print("[dim]Usage: aaf template rm <template-name>[/dim]")
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
                console.print("[dim]Usage: aaf template cat <template-name>[/dim]")
                raise typer.Exit(1)

            template_path = manager.get_template_path(source)
            if not template_path:
                console.print(f"[red]Error: Template '{source}' not found[/red]")
                raise typer.Exit(1)

            # Display template content using pager
            import subprocess
            try:
                subprocess.run(["less", "-R", str(template_path)], check=False)
            except FileNotFoundError:
                # Fallback: print to console
                content = template_path.read_text()
                console.print(content)

        elif action == "edit":
            if not source:
                console.print("[red]Error: 'edit' action requires template name[/red]")
                console.print("[dim]Usage: aaf template edit <template-name>[/dim]")
                raise typer.Exit(1)

            template_path = manager.get_template_path(source)
            if not template_path:
                console.print(f"[red]Error: Template '{source}' not found[/red]")
                raise typer.Exit(1)

            # Open template in editor
            import subprocess
            import os

            editor = os.environ.get('EDITOR', 'vim')
            try:
                subprocess.run([editor, str(template_path)], check=True)
                console.print(f"[green]✅ Template '{source}' updated[/green]")
            except subprocess.CalledProcessError:
                console.print(f"[red]Error: Failed to edit template[/red]")
                raise typer.Exit(1)
            except FileNotFoundError:
                console.print(f"[red]Error: Editor '{editor}' not found[/red]")
                console.print(f"[dim]Set EDITOR environment variable or install vim[/dim]")
                raise typer.Exit(1)

        else:
            console.print(f"[red]Error: Unknown action '{action}'[/red]")
            console.print("[dim]Valid actions: add, ls, rm, cat, edit[/dim]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def main() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
