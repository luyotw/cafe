"""Command-line interface for CAFE."""

import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import AgentConfig, AgentCLI, WorkflowMode
from cafe.core.workflow import Workflow
from cafe.phases.plan_phase import PlanPhase
from cafe.phases.develop_phase import DevelopPhase
from cafe.phases.pr_phase import PRPhase
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.review_phase import ReviewPhase
from cafe.utils.config import ConfigManager
from cafe.utils.git_utils import is_branch_initialized
from cafe.utils.github import GitHubOps, GitHubError
from cafe.utils.template import TemplateManager
from cafe.ui.template_selector import select_template

app = typer.Typer(
    name="cafe",
    help="AI Agent Flow - Automated development workflow with AI agents",
    no_args_is_help=True,
)
console = Console()


def _get_and_validate_branch(ctx: typer.Context, phase_name: str) -> str:
    """Get current branch and validate it for core phase commands.

    Args:
        ctx: Typer context (used to check for extra arguments)
        phase_name: Name of the phase (for error messages)

    Returns:
        Current branch name

    Raises:
        typer.Exit: If validation fails
    """
    # Check for extra positional arguments
    if ctx.args:
        console.print(
            f"[red]Error: The '{phase_name}' command no longer accepts an issue name. "
            f"It automatically uses the current Git branch.[/red]"
        )
        raise typer.Exit(1)

    # Get current branch
    git = GitOperations()
    try:
        if not git.is_valid_branch():
            console.print(
                "[red]Error: You are not currently on a valid Git branch. "
                "Please checkout a branch first.[/red]"
            )
            raise typer.Exit(1)

        branch_name = git.get_current_branch()

        # Check if branch is initialized
        if not is_branch_initialized(branch_name):
            console.print(
                f"[red]Error: This branch has not been initialized. "
                f"Please run 'cafe prepare' first.[/red]"
            )
            raise typer.Exit(1)

        return branch_name

    except Exception as e:
        console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
        raise typer.Exit(1)


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
    git_ops: GitOperations,
) -> Workflow:
    """Build workflow with all phases.

    Args:
        mode: Workflow mode
        spec_file: Specification file path
        issue_id: GitHub issue ID
        agent_manager: Agent manager
        permission_handler: Permission handler
        config_manager: Configuration manager
        git_ops: Git operations

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
            git_ops=git_ops,
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
            git_ops=git_ops,
            spec_file=spec_file,
            workflow_mode=mode,
            issue_id=issue_id,
            dev_agent=dev_name,
            interactive=True,
        )
    )

    # Determine plan file path from spec file
    spec_path = Path(spec_file)
    if ".cafe/issues/" in spec_file:
        # Extract issue name from spec path: .cafe/issues/{issue-name}/spec/spec.md
        plan_file = str(spec_path.parent.parent / "plan" / "plan.md")
    else:
        # Fallback to simple plan.md
        plan_file = "plan.md"

    # Phase 3: Development
    workflow.add_phase(
        DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=spec_file,
            plan_file=plan_file,
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
            plan_file=plan_file,
            workflow_mode=mode,
            issue_id=issue_id,
            review_agent=reviewer_name,
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
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Run the CAFE workflow.

    Examples:
        # Local mode with spec file
        cafe run -m local -s spec.md

        # GitHub mode with issue
        cafe run -m github -i 123
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
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Build and execute workflow
        console.print("[bold blue]Starting CAFE workflow...[/bold blue]")
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
            git_ops=git_ops,
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
    """Show CAFE version."""
    console.print("CAFE version 0.1.0")


@app.command()
def prepare(
    issue_name: Optional[str] = typer.Argument(
        None,
        help="Issue name (will create directory at .cafe/issues/{issue-name}/)",
    ),
    base_branch: Optional[str] = typer.Option(
        None,
        "--base",
        "-b",
        help="Base branch to branch from (default: current branch)",
    ),
    check_uncommitted: bool = typer.Option(
        True,
        "--check/--no-check",
        help="Check for uncommitted changes before switching branch (default: True)",
    ),
    worktree: Optional[str] = typer.Option(
        "",
        "--worktree",
        help="Use worktree mode with specified path (e.g., worktrees/my-feature)",
    ),
) -> None:
    """Prepare issue environment (directory, config, git branch) before running spec phase.

    This command sets up the necessary directory structure, creates a feature branch,
    and saves initial configuration for the issue.

    Examples:
        # Interactive mode (will ask for issue name)
        cafe prepare

        # Specify issue name directly
        cafe prepare fix-login-bug

        # Specify custom base branch
        cafe prepare my-feature --base develop

        # Skip uncommitted changes check
        cafe prepare my-feature --no-check
    """
    import yaml

    try:
        # 1. Get issue name (from argument or prompt)
        is_interactive = not issue_name  # Track if we're in interactive mode
        if not issue_name:
            issue_name = typer.prompt("Issue name")
            if not issue_name or not issue_name.strip():
                console.print("[red]Error: Issue name cannot be empty.[/red]")
                raise typer.Exit(1)
            issue_name = issue_name.strip()

        # 2. Initialize Git operations
        try:
            git_ops = GitOperations()
        except Exception as e:
            console.print(f"[red]Error: Not a git repository. {e}[/red]")
            console.print("[yellow]Hint: Run 'git init' to initialize a git repository.[/yellow]")
            raise typer.Exit(1)

        # 3. Check for uncommitted changes (warning only)
        if check_uncommitted and git_ops.has_uncommitted_changes():
            console.print("[yellow]⚠️  Warning: You have uncommitted changes.[/yellow]")
            console.print("[yellow]    It's recommended to commit or stash them before switching branches.[/yellow]")
            console.print()

            # Ask if user wants to continue
            continue_anyway = typer.confirm("Continue anyway?", default=False)
            if not continue_anyway:
                console.print("[dim]Cancelled.[/dim]")
                raise typer.Exit(0)

        # 4. Determine base branch
        if not base_branch:
            base_branch = git_ops.get_current_branch()

        # 4.5. Determine worktree mode (interactive or from parameter)
        use_worktree = False
        worktree_path = None

        # If --worktree parameter is provided (non-interactive)
        if worktree and worktree.strip():
            use_worktree = True
            worktree_path = worktree.strip()
        # If in interactive mode and no --worktree parameter
        elif is_interactive and not worktree:
            # Ask user if they want to use worktree mode
            use_worktree = typer.confirm(
                "Use Git worktree mode for this issue?",
                default=False
            )

            if use_worktree:
                # Suggest default path
                default_path = f".cafe/worktrees/{issue_name}"
                console.print(f"[dim]Default path: {default_path}[/dim]")

                # Prompt for path (allow empty input to use default)
                user_path = typer.prompt(
                    "Worktree path (press Enter for default)",
                    default=default_path,
                    show_default=False
                )
                worktree_path = user_path.strip() if user_path.strip() else default_path

        console.print()
        console.print(f"[bold blue]🔧 Preparing issue: {issue_name}[/bold blue]")
        console.print(f"Base branch: {base_branch}")
        console.print()

        # 5. Create issue directory structure
        issue_dir = Path(f".cafe/issues/{issue_name}")
        spec_dir = issue_dir / "spec"
        sessions_dir = issue_dir / "sessions"

        spec_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # 6. Create or switch to feature branch (or worktree)
        feature_branch = issue_name

        if use_worktree:
            # Worktree mode
            console.print(f"[dim]Creating worktree at '{worktree_path}'...[/dim]")
            git_ops.create_worktree(worktree_path, feature_branch, base_branch)

            # Create symlink to .cafe directory so worktree can access cafe commands
            import os
            worktree_abs = Path(worktree_path).resolve()
            cafe_dir = Path(".cafe").resolve()
            worktree_cafe_link = worktree_abs / ".cafe"

            # Only create symlink if:
            # 1. .cafe directory exists
            # 2. worktree directory exists (created by git worktree add)
            # 3. symlink doesn't already exist
            if cafe_dir.exists() and worktree_abs.exists() and not worktree_cafe_link.exists():
                # Calculate relative path from worktree to main repo's .cafe
                relative_cafe = os.path.relpath(cafe_dir, worktree_abs)
                worktree_cafe_link.symlink_to(relative_cafe)
        else:
            # Normal branch mode
            if git_ops.branch_exists(feature_branch):
                console.print(f"[dim]Branch '{feature_branch}' already exists, switching to it...[/dim]")
                git_ops.checkout_branch(feature_branch)
            else:
                console.print(f"[dim]Creating and switching to branch '{feature_branch}'...[/dim]")
                git_ops.create_branch(feature_branch)

        # 7. Save config.yaml
        config_file = issue_dir / "config.yaml"
        config_data = {
            "base_branch": base_branch,
            "feature_branch": feature_branch,
        }

        # Add worktree_path if using worktree mode
        if use_worktree:
            config_data["worktree_path"] = worktree_path

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

        # 8. Display success message
        console.print()
        console.print(f"[green]✓ Successfully prepared issue: {issue_name}[/green]")
        console.print(f"  📁 Directory: .cafe/issues/{issue_name}/")
        console.print(f"  🌿 Feature branch: {feature_branch}")
        console.print(f"  ⚓ Base branch: {base_branch}")
        if use_worktree:
            console.print(f"  📂 Worktree: {worktree_path}")
        console.print(f"  ⚙️  Config: .cafe/issues/{issue_name}/config.yaml")
        console.print()

        # Show next steps
        if use_worktree:
            console.print(f"[bold]Next steps:[/bold]")
            console.print(f"  cd {worktree_path}")
            console.print(f"  cafe spec")
        else:
            console.print(f"[bold]Next step:[/bold] cafe spec")
        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error during prepare: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def close() -> None:
    """Close current feature and return to base branch.

    This command:
    1. Checks for open/draft PRs (blocks if found)
    2. For worktree mode: switches back to main repo, removes worktree, deletes branch
    3. For normal mode: switches to base branch, deletes feature branch
    4. Pulls latest changes from remote
    5. Preserves .cafe/issues/<issue-name>/ directory
    """
    import yaml
    import os

    try:
        # 1. Initialize Git operations
        try:
            git_ops = GitOperations()
        except Exception as e:
            console.print(f"[red]Error: Not a git repository. {e}[/red]")
            raise typer.Exit(1)

        # 2. Get current branch
        current_branch = git_ops.get_current_branch()
        if not current_branch:
            console.print("[red]Error: Not on a valid branch (detached HEAD?).[/red]")
            raise typer.Exit(1)

        # 3. Check for open/draft PRs
        try:
            github_ops = GitHubOps()
            pr = github_ops.get_pr_for_branch(current_branch)

            if pr:
                pr_state = pr.get("state", "UNKNOWN")
                is_draft = pr.get("isDraft", False)
                pr_url = pr.get("url", "")

                # Block if PR is open (OPEN state) or draft
                if pr_state == "OPEN" or is_draft:
                    console.print()
                    console.print("[red]❌ Cannot close: Open PR found for this branch[/red]")
                    console.print(f"   PR #{pr.get('number')}: {pr.get('title')}")
                    console.print(f"   State: {pr_state}{' (DRAFT)' if is_draft else ''}")
                    console.print(f"   URL: {pr_url}")
                    console.print()
                    console.print("[yellow]Please merge or close the PR first, or use --no-pr-check to skip the check.[/yellow]")
                    raise typer.Exit(1)
        except GitHubError:
            # If gh CLI is not installed or not authenticated, skip PR check
            pass

        # 4. Load issue config
        config_file = Path(f".cafe/issues/{current_branch}/config.yaml")
        if not config_file.exists():
            console.print(f"[red]Error: Issue config not found: {config_file}[/red]")
            console.print(f"[yellow]Hint: This branch may not be initialized with 'cafe prepare'.[/yellow]")
            raise typer.Exit(1)

        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)

        base_branch = config_data.get("base_branch", "main")
        feature_branch = current_branch
        worktree_path = config_data.get("worktree_path")

        console.print()
        console.print(f"[bold blue]🔒 Closing issue: {feature_branch}[/bold blue]")
        console.print()

        # 5. Handle worktree mode vs normal mode
        if worktree_path:
            # === WORKTREE MODE ===
            # Step 1: Switch back to main repository
            try:
                console.print(f"[dim]Switching to main repository...[/dim]")
                # Find the main repository path (parent of .cafe/worktrees)
                current_dir = Path.cwd()
                main_repo = current_dir
                while main_repo != main_repo.parent:
                    git_dir = main_repo / ".git"
                    if git_dir.exists() and git_dir.is_dir():
                        break
                    main_repo = main_repo.parent

                os.chdir(str(main_repo))
                console.print(f"[green]✓ Switched to main repository: {main_repo}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to switch to main repository: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. cd to main repository")
                console.print(f"  2. git checkout {base_branch}")
                console.print(f"  3. git pull")
                console.print(f"  4. git worktree remove {worktree_path}")
                console.print(f"  5. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 2: Checkout base branch (in main repo)
            try:
                console.print(f"[dim]Switching to base branch: {base_branch}[/dim]")
                # Re-initialize git_ops in main repo
                git_ops = GitOperations()
                git_ops.checkout_branch(base_branch)
                console.print(f"[green]✓ Switched to base branch: {base_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to switch to base branch: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git checkout {base_branch}")
                console.print(f"  2. git pull")
                console.print(f"  3. git worktree remove {worktree_path}")
                console.print(f"  4. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 3: Pull latest changes
            try:
                console.print(f"[dim]Updating base branch...[/dim]")
                git_ops.pull()
                console.print(f"[green]✓ Updated base branch[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to update base branch: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git pull")
                console.print(f"  2. git worktree remove {worktree_path}")
                console.print(f"  3. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 4: Remove worktree
            try:
                console.print(f"[dim]Removing worktree: {worktree_path}[/dim]")
                git_ops.remove_worktree(worktree_path)
                console.print(f"[green]✓ Removed worktree: {worktree_path}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to remove worktree: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git worktree remove {worktree_path}")
                console.print(f"  2. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 5: Delete feature branch
            try:
                console.print(f"[dim]Deleting feature branch: {feature_branch}[/dim]")
                git_ops.delete_branch(feature_branch)
                console.print(f"[green]✓ Deleted feature branch: {feature_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to delete branch: {e}[/red]")
                console.print(f"[yellow]The branch may not be fully merged.[/yellow]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git branch -D {feature_branch}  # Force delete if needed")
                console.print()
                raise typer.Exit(1)

        else:
            # === NORMAL MODE (no worktree) ===
            # Step 1: Checkout base branch
            try:
                console.print(f"[dim]Switching to base branch: {base_branch}[/dim]")
                git_ops.checkout_branch(base_branch)
                console.print(f"[green]✓ Switched to base branch: {base_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to switch to base branch: {e}[/red]")
                console.print(f"[yellow]Hint: You may have uncommitted changes. Please commit or stash them first.[/yellow]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git checkout {base_branch}")
                console.print(f"  2. git pull")
                console.print(f"  3. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 2: Pull latest changes
            try:
                console.print(f"[dim]Updating base branch...[/dim]")
                git_ops.pull()
                console.print(f"[green]✓ Updated base branch[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to update base branch: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git pull")
                console.print(f"  2. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 3: Delete feature branch
            try:
                console.print(f"[dim]Deleting feature branch: {feature_branch}[/dim]")
                git_ops.delete_branch(feature_branch)
                console.print(f"[green]✓ Deleted feature branch: {feature_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to delete branch: {e}[/red]")
                console.print(f"[yellow]The branch may not be fully merged.[/yellow]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git branch -D {feature_branch}  # Force delete if needed")
                console.print()
                raise typer.Exit(1)

        # 6. Display success message
        console.print()
        console.print(f"[green]✓ Successfully closed issue: {feature_branch}[/green]")
        console.print(f"  📁 Issue data preserved at: .cafe/issues/{feature_branch}/")
        console.print(f"  🌿 Current branch: {base_branch}")
        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error during close: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def spec(
    ctx: typer.Context,
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
    fetch_issue_id: Optional[int] = typer.Option(
        None,
        "--issue-id",
        help="Fetch issue content from GitHub (provide issue number)",
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
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
    show_prompt: bool = typer.Option(
        False,
        "--show-prompt",
        help="Show the prompt sent to agent",
    ),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        "-u",
        help="User input for non-interactive mode (required when --no-interactive)",
    ),
) -> None:
    """Run specification phase: Spec clarification with conversational generation.

    The PM agent will engage in a dialogue with you to clarify and generate
    a complete specification document. No technical details will be discussed.

    This command automatically uses the current Git branch name as the issue identifier.

    Examples:
        # Generate spec through conversation (uses current branch)
        cafe spec

        # Create new GitHub issue with spec
        cafe spec -m github

        # Update existing GitHub issue
        cafe spec -m github -i 123

        # Use custom PM agent
        cafe spec --pm CustomPM

        # Specify rigor level
        cafe spec --rigor low
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "spec")

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
                from cafe.core.types import SpecRigor
                spec_rigor = SpecRigor(rigor)
            except ValueError:
                console.print(f"[red]Error: Invalid rigor '{rigor}'. Use 'low', 'medium', or 'high'.[/red]")
                raise typer.Exit(1)

        # Build spec file path: .cafe/issues/{issue-name}/spec/spec.md
        spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

        # Create directory if it doesn't exist
        spec_dir = Path(spec_file).parent
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get PM agent CLI
        pm_executor = agent_manager.get_agent(pm_agent)
        pm_cli = pm_executor.config.cli.value
        pm_session_id = pm_executor.config.session_id or "(will be created)"

        # Display start message
        console.print("[bold blue]🎯 Spec Phase: Specification Clarification[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"PM Agent: {pm_agent}")
        console.print(f"CLI: {pm_cli}")
        console.print(f"Session ID: {pm_session_id}")
        if spec_rigor:
            console.print(f"Rigor: {spec_rigor.value}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec file: {spec_file}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        console.print()

        # Determine if should be interactive
        import sys
        is_interactive = interactive and sys.stdin.isatty()

        # Validate user_input in non-interactive mode (unless using --issue-id to fetch)
        if not is_interactive and not user_input and not fetch_issue_id:
            console.print("[red]Error: --user-input is required when using --no-interactive (or use --issue-id to fetch from GitHub)[/red]")
            raise typer.Exit(1)

        # Create and execute spec phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=spec_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            pm_agent=pm_agent,
            interactive=is_interactive,
            rigor=spec_rigor,
            user_input=user_input or "",
            issue_name=issue_name,
            fetch_issue_id=fetch_issue_id,
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
            status_code = result.data.get('status_code')

            if status_code == "CAFE_NEED_CLARIFICATION":
                console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if workflow_mode == WorkflowMode.LOCAL:
                    console.print(f"Saved to: {spec_file}")
                console.print()
                console.print("[dim]To continue, run:[/dim] [bold]cafe spec[/bold]")
            elif status_code == "CAFE_REJECTED":
                console.print("[bold red]❌ Spec rejected by agent[/bold red]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if workflow_mode == WorkflowMode.LOCAL:
                    console.print(f"Saved to: {spec_file}")
            else:
                # CAFE_READY_FOR_REVIEW or CAFE_CONFIRMED
                console.print("[bold green]✅ Spec clarification completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if workflow_mode == WorkflowMode.LOCAL:
                    console.print(f"Saved to: {spec_file}")
                elif result.data.get('issue_id'):
                    console.print(f"Created issue: #{result.data['issue_id']}")
                elif issue_id:
                    console.print(f"Updated issue: #{issue_id}")
                console.print()
                console.print("[dim]Next step:[/dim] [bold]cafe plan[/bold]")
        else:
            console.print()
            console.print(f"[bold red]❌ Spec phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def plan(
    ctx: typer.Context,
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
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    show_prompt: bool = typer.Option(
        False,
        "--show-prompt",
        help="Show the prompt sent to agent",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
) -> None:
    """Run plan phase: Implementation planning with developer agent.

    The developer agent will analyze the specification and create a detailed
    implementation plan with technical considerations and development guide.

    This command automatically uses the current Git branch name as the issue identifier.

    Examples:
        # Analyze spec and create plan (uses current branch)
        cafe plan

        # Analyze GitHub issue and create plan
        cafe plan -m github -i 123

        # Use custom developer agent
        cafe plan --dev CustomDev
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "plan")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Build spec file path: .cafe/issues/{issue-name}/spec/spec.md
        spec_file = f".cafe/issues/{issue_name}/spec/spec.md"

        # Check if spec file exists
        if not Path(spec_file).exists():
            console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Check if plan already exists
        plan_file = f".cafe/issues/{issue_name}/plan/plan.md"
        is_resume = Path(plan_file).exists()

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get developer agent CLI
        dev_executor = agent_manager.get_agent(dev_agent)
        dev_cli = dev_executor.config.cli.value
        dev_session_id = dev_executor.config.session_id or "(will be created)"

        # Handle template selection
        template_manager = TemplateManager(config_dir)
        selected_template = None

        if is_resume:
            console.print(f"[dim]Resuming existing plan from: {plan_file}[/dim]")

        if template:
            # Template specified via --template option
            if not template_manager.template_exists(template):
                console.print(f"[red]Error: Template '{template}' not found[/red]")
                console.print("[dim]Use 'cafe template list' to see available templates[/dim]")
                raise typer.Exit(1)
            selected_template = template

        # Display start message
        console.print("[bold blue]📋 Plan Phase: Implementation Planning[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent}")
        console.print(f"CLI: {dev_cli}")
        console.print(f"Session ID: {dev_session_id}")
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
            git_ops=git_ops,
            spec_file=spec_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            issue_name=issue_name,
            dev_agent=dev_agent,
            interactive=interactive,
            template_path=template_path_str,
        )

        console.print("[bold]Starting implementation planning...[/bold]")
        console.print("[dim]The developer will analyze technical feasibility and create implementation plan.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            console.print()
            status_code = result.data.get('status_code')
            plan_file = f".cafe/issues/{issue_name}/plan/plan.md"

            if status_code == "CAFE_NEED_CLARIFICATION":
                console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if Path(plan_file).exists():
                    console.print(f"Saved to: {plan_file}")
                console.print()
                console.print("[dim]To continue, run:[/dim] [bold]cafe plan[/bold]")
            elif status_code == "CAFE_READY_FOR_REVIEW":
                console.print("[bold yellow]📋 Plan ready for review[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if Path(plan_file).exists():
                    console.print(f"Saved to: {plan_file}")
                console.print()
                console.print("[dim]Review the plan and run:[/dim] [bold]cafe plan[/bold] [dim](to modify) or[/dim] [bold]cafe develop[/bold] [dim](to continue)[/dim]")
            elif status_code == "CAFE_REJECTED":
                console.print("[bold red]❌ Plan rejected by agent[/bold red]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if Path(plan_file).exists():
                    console.print(f"Saved to: {plan_file}")
            else:
                # CAFE_CONFIRMED
                console.print("[bold green]✅ Implementation plan completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                if Path(plan_file).exists():
                    console.print(f"Saved to: {plan_file}")
                console.print()
                console.print("[dim]Next step:[/dim] [bold]cafe develop[/bold]")
        else:
            console.print()
            console.print(f"[bold red]❌ Plan phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def develop(
    ctx: typer.Context,
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
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    show_prompt: bool = typer.Option(
        False,
        "--show-prompt",
        help="Show the prompt sent to agent",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
    approve_denied_tools: Optional[str] = typer.Option(
        None,
        "--approve-denied-tools",
        help="Comma-separated indices of permission denials to approve (non-interactive mode)",
    ),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        help="Additional user instructions or context (non-interactive mode)",
    ),
    pr_number: Optional[int] = typer.Option(
        None,
        "--pr-number",
        help="PR number to fetch unresolved comments from",
    )
) -> None:
    """Run develop phase: Execute development work according to plan.

    The developer agent will implement the planned features, running tests and
    making commits according to the implementation plan.

    This command automatically uses the current Git branch name as the issue identifier.

    Examples:
        # Execute development (uses current branch)
        cafe develop

        # Use custom developer agent
        cafe develop --dev CustomDev

        # Fetch unresolved PR comments to guide development
        cafe develop --pr-number 123

        # Non-interactive mode with permission approval
        cafe develop --no-interactive --approve-denied-tools 0,2 --user-input "請小心處理"
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "develop")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Build file paths
        spec_file = f".cafe/issues/{issue_name}/spec/spec.md"
        plan_file = f".cafe/issues/{issue_name}/plan/plan.md"

        # Check if spec file exists
        if not Path(spec_file).exists():
            console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Check if plan file exists
        if not Path(plan_file).exists():
            console.print(f"[red]Error: Plan file not found: {plan_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe plan' first to create the implementation plan.[/dim]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get developer agent CLI
        dev_executor = agent_manager.get_agent(dev_agent)
        dev_cli = dev_executor.config.cli.value
        dev_session_id = dev_executor.config.session_id or "(will be created)"

        # Display start message
        console.print("[bold blue]🔨 Develop Phase: Development Execution[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent}")
        console.print(f"CLI: {dev_cli}")
        console.print(f"Session ID: {dev_session_id}")
        console.print(f"Spec file: {spec_file}")
        console.print(f"Plan file: {plan_file}")
        console.print()

        # Parse approve_denied_tools if provided
        approved_denial_indices: List[int] = []
        if approve_denied_tools is not None:
            try:
                # Ensure it's a string (defensive programming)
                tools_str = str(approve_denied_tools)
                approved_denial_indices = [int(idx.strip()) for idx in tools_str.split(",")]
            except (ValueError, AttributeError) as e:
                console.print(f"[red]Error: --approve-denied-tools must be comma-separated integers (e.g., '0,1,3'). Got: {approve_denied_tools}[/red]")
                console.print(f"[dim]Debug: type={type(approve_denied_tools)}, error={e}[/dim]")
                raise typer.Exit(1)

        # Create and execute develop phase
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=spec_file,
            plan_file=plan_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            issue_name=issue_name,
            dev_agent=dev_agent,
            interactive=interactive,
            approved_denial_indices=approved_denial_indices if approved_denial_indices else None,
            user_input=user_input or "",
            pr_number=pr_number,
        )

        console.print("[bold]Starting development execution...[/bold]")
        console.print("[dim]The developer will implement features according to the plan.[/dim]")
        console.print("[dim]💡 Tip: Press Ctrl+C anytime to pause and save progress.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            console.print()
            console.print("[bold green]✅ Development completed![/bold green]")
            console.print(f"Branch: {result.data.get('branch', 'N/A')}")
            console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
            console.print()
            console.print("[dim]Next steps:[/dim]")
            console.print(f"[dim]  1. Review changes: git diff[/dim]")
            console.print(f"[dim]  2. Run tests: pytest[/dim]")
            console.print(f"[dim]  3. Code review: cafe review[/dim]")
        elif result.status.value == "failed":
            console.print(f"[red]❌ Development failed: {result.message}[/red]")
            raise typer.Exit(1)
        elif result.status.value == "in_progress":
            console.print(f"[yellow]⏸️  Development paused: {result.message}[/yellow]")
            console.print(f"[dim]Resume with: cafe develop[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Add "dev" as an alias for "develop"
@app.command(name="dev")
def dev_alias(
    ctx: typer.Context,
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
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    show_prompt: bool = typer.Option(
        False,
        "--show-prompt",
        help="Show the prompt sent to agent",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
    approve_denied_tools: Optional[str] = typer.Option(
        None,
        "--approve-denied-tools",
        help="Comma-separated indices of permission denials to approve (non-interactive mode)",
    ),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        help="Additional user instructions or context (non-interactive mode)",
    ),
    pr_number: Optional[int] = typer.Option(
        None,
        "--pr-number",
        help="PR number to fetch unresolved comments from",
    )
) -> None:
    """Alias for 'develop' command."""
    # Call the develop function with all parameters
    develop(
        ctx=ctx,
        mode=mode,
        issue_id=issue_id,
        dev_agent=dev_agent,
        config_file=config_file,
        show_prompt=show_prompt,
        interactive=interactive,
        approve_denied_tools=approve_denied_tools,
        user_input=user_input,
        pr_number=pr_number,
    )


@app.command()
def review(
    ctx: typer.Context,
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
    commit: Optional[str] = typer.Option(
        None,
        "--commit",
        "-c",
        help="Specific commit SHA to review (default: review entire branch)",
    ),
    base_branch: str = typer.Option(
        "main",
        "--base",
        "-b",
        help="Base branch for diff (default: main)",
    ),
    reviewer_agent: str = typer.Option(
        "Richard",
        "--reviewer",
        help="Reviewer agent name",
    ),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        help="Path to configuration file",
    ),
    show_prompt: bool = typer.Option(
        False,
        "--show-prompt",
        help="Show the prompt sent to agent",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
    pr_number: Optional[int] = typer.Option(
        None,
        "--pr-number",
        help="PR number to fetch unresolved comments from",
    ),
) -> None:
    """Run review phase: Code review by reviewer agent.

    The reviewer agent will review code changes and provide feedback.
    Each execution performs one review iteration.

    This command automatically uses the current Git branch name as the issue identifier.

    Examples:
        # Review entire feature branch (uses current branch)
        cafe review

        # Review specific commit
        cafe review --commit abc123

        # Use custom reviewer agent
        cafe review --reviewer CustomReviewer
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "review")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Build file paths
        spec_file = f".cafe/issues/{issue_name}/spec/spec.md"
        plan_file = f".cafe/issues/{issue_name}/plan/plan.md"

        # Check if spec file exists
        if not Path(spec_file).exists():
            console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get reviewer agent CLI
        reviewer_executor = agent_manager.get_agent(reviewer_agent)
        reviewer_cli = reviewer_executor.config.cli.value
        reviewer_session_id = reviewer_executor.config.session_id or "(will be created)"

        # Create review phase (this will read base_branch from config if available)
        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=spec_file,
            plan_file=plan_file,
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            review_agent=reviewer_agent,
            target_commit=commit,
            base_branch=base_branch,
            interactive=interactive,
            pr_number=pr_number,
        )

        # Display start message (use actual base_branch from phase)
        console.print("[bold blue]🔍 Review Phase: Code Review[/bold blue]")
        console.print(f"Mode: {workflow_mode.value}")
        console.print(f"Issue: {issue_name}")
        console.print(f"Reviewer Agent: {reviewer_agent}")
        console.print(f"CLI: {reviewer_cli}")
        console.print(f"Session ID: {reviewer_session_id}")
        console.print(f"Spec file: {spec_file}")
        console.print(f"Base branch: {phase.base_branch}")
        if commit:
            console.print(f"Target commit: {commit}")
        else:
            console.print(f"Review scope: {phase.base_branch}..HEAD")
        console.print()

        console.print("[bold]Starting code review...[/bold]")
        console.print("[dim]The reviewer will analyze code changes and provide feedback.[/dim]")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            status_code = result.data.get("status_code")
            console.print()
            if status_code == "CAFE_CONFIRMED":
                console.print("[bold green]✅ Code review passed![/bold green]")
                console.print()
                console.print("[dim]Next steps:[/dim]")
                console.print(f"[dim]  1. Create PR: cafe pr[/dim]")
            else:
                console.print(f"[bold yellow]📝 Code review completed with status: {status_code}[/bold yellow]")
                console.print()

                # Find latest review file (review_XXX.md)
                review_dir = Path(f".cafe/issues/{issue_name}/review")
                review_files = sorted(review_dir.glob("review_*.md"))
                if review_files:
                    latest_review = review_files[-1]
                    review_path = f".cafe/issues/{issue_name}/review/{latest_review.name}"
                else:
                    # Fallback to review.md if no numbered files found
                    review_path = f".cafe/issues/{issue_name}/review/review.md"

                console.print("[dim]Review feedback saved to:[/dim]")
                console.print(f"[dim]  {review_path}[/dim]")
                console.print()
                console.print("[dim]Next steps:[/dim]")
                console.print(f"[dim]  1. Review feedback: cat {review_path}[/dim]")
                console.print(f"[dim]  2. Make changes: cafe develop[/dim]")
                console.print(f"[dim]  3. Review again: cafe review[/dim]")
        else:
            console.print()
            console.print(f"[bold red]❌ Review phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def pr(
    ctx: typer.Context,
    base: str = typer.Option(
        "main",
        "--base",
        "-b",
        help="Base branch for PR (default: main)",
    ),
    draft: Optional[bool] = typer.Option(
        None,
        "--draft/--no-draft",
        help="Create as draft PR (default: ask in interactive mode, True in non-interactive)",
    ),
    title: Optional[str] = typer.Option(
        None,
        "--title",
        "-t",
        help="Custom PR title (leave empty for auto-generation)",
    ),
    body: Optional[str] = typer.Option(
        None,
        "--body",
        help="Custom PR body (leave empty for auto-generation)",
    ),
    update: bool = typer.Option(
        False,
        "--update",
        help="Force regenerate PR title/body even if they already exist",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force push to remote (use with caution)",
    ),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        help="Path to configuration file",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Allow interactive prompts (default: True)",
    ),
) -> None:
    """Create pull request for the issue.

    The PR phase will push the feature branch and create a GitHub Pull Request.

    This command automatically uses the current Git branch name as the issue identifier.

    Examples:
        # Create draft PR (uses current branch, interactive mode will ask for confirmation)
        cafe pr

        # Create non-draft PR
        cafe pr --no-draft

        # Create PR with custom title and body
        cafe pr --title "Add user authentication" --body "Implements login/logout"

        # Non-interactive mode (creates draft PR by default)
        cafe pr --no-interactive
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "pr")

        # Build file paths
        spec_file = f".cafe/issues/{issue_name}/spec/spec.md"
        plan_file = f".cafe/issues/{issue_name}/plan/plan.md"

        # Check if spec file exists
        if not Path(spec_file).exists():
            console.print(f"[red]Error: Spec file not found: {spec_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Check if plan file exists
        if not Path(plan_file).exists():
            console.print(f"[red]Error: Plan file not found: {plan_file}[/red]")
            console.print(f"[dim]Hint: Run 'cafe plan' first to create the plan.[/dim]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        from cafe.utils.github import GitHubOps
        github_ops = GitHubOps()

        # Determine final draft value
        final_draft = draft if draft is not None else True  # Default to draft

        # Create PR phase
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,  # Always use local mode (no --mode flag)
            issue_name=issue_name,
            draft=final_draft,
            custom_title=title,
            custom_body=body,
            update=update,
            force_push=force,
            interactive=interactive,
            base_branch=base if base != "main" else None,  # Pass base only if not default
        )

        # Display start message
        console.print("[bold blue]🚀 PR Phase: Create Pull Request[/bold blue]")
        console.print(f"Issue: {issue_name}")
        console.print(f"Base branch: {phase.base_branch}")
        console.print()

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            pr_number = result.data.get("pr_number")
            pr_url = result.data.get("pr_url")
            console.print()
            console.print(f"[bold green]✅ {result.message}![/bold green]")
            console.print()
            if pr_url:
                console.print(f"[bold cyan]{pr_url}[/bold cyan]")
                console.print()
            console.print("[dim]Next steps:[/dim]")
            console.print(f"[dim]  1. View PR: gh pr view {pr_number}[/dim]")
            console.print(f"[dim]  2. Edit PR: gh pr edit {pr_number}[/dim]")
            if final_draft:
                console.print(f"[dim]  3. Mark as ready: gh pr ready {pr_number}[/dim]")
        else:
            console.print()
            console.print(f"[bold red]❌ PR phase failed: {result.message}[/bold red]")
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
    """Manage CAFE configuration.

    Examples:
        # Show all configuration
        cafe config

        # Set a configuration value (with alias support)
        cafe config set pm gemini
        cafe config set pm.cli gemini
        cafe config set agents.pm.cli gemini

        # Get a configuration value
        cafe config get pm
        cafe config get agents.pm.cli

        # Edit config file in editor
        cafe config edit

        # Reset to defaults
        cafe config reset
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
            console.print("Usage: cafe config set <key> <value>")
            raise typer.Exit(1)

        config_manager.set(key, value)
        console.print(f"[green]✓ Set {key} = {value}[/green]")

    elif action == "get":
        if not key:
            console.print("[red]Error: 'get' requires a key[/red]")
            console.print("Usage: cafe config get <key>")
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
        # e.g., "cafe config pm" -> get pm
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

    issues_dir = Path(".cafe/issues")

    if not issues_dir.exists():
        console.print("[yellow]No issues directory found[/yellow]")
        console.print("Run 'cafe run <issue-name>' to create your first issue")
        return

    # Get all issue directories
    issues = [d for d in issues_dir.iterdir() if d.is_dir()]

    if not issues:
        console.print("[yellow]No issues found[/yellow]")
        console.print("Run 'cafe run <issue-name>' to create your first issue")
        return

    # Create table
    table = Table(title="CAFE Issues", show_header=True, header_style="bold cyan")
    table.add_column("Issue Name", style="green")
    table.add_column("Phases", style="dim")
    table.add_column("Modified", style="dim")

    for issue in sorted(issues, key=lambda x: x.stat().st_mtime, reverse=True):
        # Check which phases exist
        phases = []
        for phase in ["spec", "plan", "develop", "review", "pr"]:
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
    issue_names: list[str] = typer.Argument(..., help="Names of the issues to delete (supports wildcards like 'test-*')"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more issues and all their data."""
    import shutil
    import fnmatch

    # Expand wildcards
    issues_dir = Path(".cafe/issues")
    expanded_issues = []
    for pattern in issue_names:
        if '*' in pattern or '?' in pattern:
            # Wildcard pattern - find matching issues
            if not issues_dir.exists():
                continue
            matches = [d.name for d in issues_dir.iterdir() if d.is_dir() and fnmatch.fnmatch(d.name, pattern)]
            expanded_issues.extend(matches)
        else:
            # Literal issue name
            expanded_issues.append(pattern)

    # Remove duplicates while preserving order
    seen = set()
    issue_names = []
    for name in expanded_issues:
        if name not in seen:
            seen.add(name)
            issue_names.append(name)

    if not issue_names:
        console.print("[red]No issues matched the given patterns[/red]")
        console.print("\nRun 'cafe ls' to see available issues")
        raise typer.Exit(1)

    # Check all issues exist first
    missing_issues = []
    existing_issues = []
    for issue_name in issue_names:
        issue_path = Path(".cafe/issues") / issue_name
        if not issue_path.exists():
            missing_issues.append(issue_name)
        else:
            existing_issues.append((issue_name, issue_path))

    # Report missing issues
    if missing_issues:
        console.print(f"[red]Issue(s) not found: {', '.join(missing_issues)}[/red]")
        console.print("\nRun 'cafe ls' to see available issues")
        if not existing_issues:
            raise typer.Exit(1)

    # Show what will be deleted
    if not force and existing_issues:
        console.print(f"[yellow]About to delete {len(existing_issues)} issue(s):[/yellow]")
        for issue_name, issue_path in existing_issues:
            console.print(f"  • {issue_name} [dim]({issue_path})[/dim]")
        console.print()

        confirm = typer.confirm(f"Are you sure you want to delete {len(existing_issues)} issue(s)?")
        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Delete the issue directories
    success_count = 0
    for issue_name, issue_path in existing_issues:
        try:
            shutil.rmtree(issue_path)
            console.print(f"[green]✓[/green] Issue '{issue_name}' deleted successfully")
            success_count += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to delete issue '{issue_name}': {e}")

    # Summary
    if len(existing_issues) > 1:
        console.print(f"\n[green]{success_count}/{len(existing_issues)} issue(s) deleted successfully[/green]")

    if success_count < len(existing_issues):
        raise typer.Exit(1)


@app.command()
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
        config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        manager = TemplateManager(config_dir)

        if action == "add":
            if not source or not name:
                console.print("[red]Error: 'add' action requires both source file path and template name[/red]")
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
                console.print("[dim]Usage: cafe template edit <template-name>[/dim]")
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
    # Check if all dependencies are installed
    _check_dependencies()
    app()


def _check_dependencies() -> None:
    """Check if pyproject.toml dependencies are installed."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        import tomli as tomllib  # Python 3.10
    from pathlib import Path
    import importlib.metadata

    # Find pyproject.toml (should be in project root)
    # Try from current file location
    project_root = Path(__file__).parent.parent.parent.parent
    pyproject_file = project_root / "pyproject.toml"

    if not pyproject_file.exists():
        # If not found, skip check (might be installed as package)
        return

    try:
        with open(pyproject_file, "rb") as f:
            pyproject = tomllib.load(f)

        dependencies = pyproject.get("project", {}).get("dependencies", [])
        missing = []

        for dep in dependencies:
            # Parse dependency string (e.g., "typer>=0.9.0" -> "typer")
            package_name = dep.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip()

            try:
                importlib.metadata.version(package_name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(package_name)

        if missing:
            console.print(f"[red]Error: Missing required dependencies: {', '.join(missing)}[/red]")
            console.print(f"[yellow]Please run: pip install -e .[/yellow]")
            sys.exit(1)

    except Exception:
        # If check fails, continue anyway
        pass


if __name__ == "__main__":
    main()
