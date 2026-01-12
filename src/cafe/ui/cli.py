"""Command-line interface for CAFE."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import typer

from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text
import yaml
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import AgentCLI, AgentConfig, WorkflowMode
from cafe.phases.develop_phase import DevelopPhase
from cafe.phases.plan_phase import PlanPhase
from cafe.phases.pr_phase import PRPhase
from cafe.phases.review_phase import ReviewPhase
from cafe.phases.spec_phase import SpecPhase
from cafe.templates.manager import TemplateManager
from cafe.ui import init_helpers
from cafe.ui.display import Display
from cafe.ui.init_helpers import (
    check_available_clis,
    list_available_agents,
)
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor
from cafe.ui.template_selector import select_template
from cafe.utils.config import ConfigManager
from cafe.utils.git_utils import is_branch_initialized
from cafe.utils.github import GitHubError, GitHubOps

app = typer.Typer(
    name="cafe",
    help="AI Agent Flow - Automated development workflow with AI agents",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

# List of all phases in order
ALL_PHASES = ["spec", "plan", "develop", "review", "pr"]

# Constants for cafe show command
VALID_PHASES = ["spec", "plan", "develop", "review", "pr"]
VALID_CONTENT_TYPES = [
    "context", "output", "streaming", "error",
    "title", "body", "status", "iterations"
]
CONTENT_TYPE_FILE_MAP = {
    "context": "context.json",
    "output": "output.md",
    "streaming": "streaming.jsonl",
    "error": "error.json",
    "title": "title.txt",
    "body": "body.md",
    "status": "status.json",
    "iterations": "iterations.jsonl",
}


def _handle_phase_exception(e: Exception, phase_name: str, auto: bool = False) -> None:
    """Unified exception handling for phase execution.

    Args:
        e: Caught exception
        phase_name: Phase name (for error messages)
        auto: Whether running in auto mode (to reduce redundant output)

    Raises:
        typer.Exit: Always raises exit(1)
    """
    from cafe.core.types import CriticalPhaseError

    # In auto mode, suppress output for most errors as they're already reported
    # BUT always show programming errors (AttributeError, TypeError, NameError, etc.)
    if auto and not isinstance(e, CriticalPhaseError):
        # Check if it's a programming error
        # These indicate bugs in the code, not normal workflow errors
        programming_errors = (AttributeError, TypeError, NameError, KeyError, IndexError, ImportError, SyntaxError)
        if isinstance(e, programming_errors):
            # These are programming/configuration errors - always show them
            console.print()
            console.print(f"[bold red]❌ Error in {phase_name} phase[/bold red]")
            console.print(f"[red]{type(e).__name__}: {e}[/red]")
        raise typer.Exit(1)

    console.print()

    # Check if it's a critical error that should stop the entire workflow
    if isinstance(e, CriticalPhaseError):
        console.print(f"[bold red]❌ Critical error in {phase_name} phase[/bold red]")
        console.print()
        if e.error_type == "rate_limit":
            console.print("[yellow]⚠️  API rate limit reached. Please try again later.[/yellow]")
        elif e.error_type == "cli_not_found":
            console.print("[yellow]⚠️  Required CLI tool not found. Please install it and try again.[/yellow]")
        else:
            console.print(f"[yellow]⚠️  {e}[/yellow]")
        console.print()
        console.print("[dim]ℹ️  The workflow has been stopped to prevent wasting resources.[/dim]")
        console.print()
    else:
        console.print(f"[bold red]❌ Error in {phase_name} phase: {e}[/bold red]")
        console.print()

    raise typer.Exit(1)


def _check_agent_clis_available(config_manager: ConfigManager) -> List[str]:
    """Check if all agent CLI tools are installed.

    Args:
        config_manager: Configuration manager

    Returns:
        List of missing CLI tools (empty list if none missing)
    """
    # Read all agent configurations
    pm_config = config_manager.get("agents.pm", {"name": "Roger", "cli": "copilot"})
    dev_config = config_manager.get("agents.developer", {"name": "David", "cli": "copilot"})
    reviewer_config = config_manager.get("agents.reviewer", {"name": "Richard", "cli": "copilot"})

    # Collect all CLI tools to check
    required_clis = [pm_config["cli"], dev_config["cli"], reviewer_config["cli"]]

    # Check if each CLI exists
    missing_clis = []
    for cli in required_clis:
        if shutil.which(cli) is None:
            if cli not in missing_clis:  # Avoid duplicates
                missing_clis.append(cli)

    return missing_clis


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
                "[red]Error: This branch has not been initialized. "
                "Please run 'cafe prepare' first.[/red]"
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
    pm_config = config_manager.get(
        "agents.pm",
        {
            "name": "Roger",
            "cli": "copilot",
        },
    )
    dev_config = config_manager.get(
        "agents.developer",
        {
            "name": "David",
            "cli": "copilot",
        },
    )
    reviewer_config = config_manager.get(
        "agents.reviewer",
        {
            "name": "Richard",
            "cli": "copilot",
        },
    )

    # Register agents
    agent_manager.register_agent(
        AgentConfig(
            name=pm_config["name"],
            cli=AgentCLI(pm_config["cli"]),
            model=pm_config.get("model"),
        )
    )
    agent_manager.register_agent(
        AgentConfig(
            name=dev_config["name"],
            cli=AgentCLI(dev_config["cli"]),
            model=dev_config.get("model"),
        )
    )
    agent_manager.register_agent(
        AgentConfig(
            name=reviewer_config["name"],
            cli=AgentCLI(reviewer_config["cli"]),
            model=reviewer_config.get("model"),
        )
    )

    return agent_manager


def _get_latest_versioned_file(phase_name: str, issue_name: str) -> Optional[Path]:
    """Get the latest versioned file for a phase.

    Args:
        phase_name: Phase name (e.g., "spec", "plan")
        issue_name: Issue name

    Returns:
        Path to the latest iteration's output.md, or None if no output files exist
    """
    phase_dir = Path(f".cafe/issues/{issue_name}/{phase_name}")
    if not phase_dir.exists():
        return None

    # Find all iteration output files (iteration_XXX/output.md)
    output_files = sorted(phase_dir.glob("iteration_*/output.md"))

    if output_files:
        # Return the latest (highest numbered iteration) file
        return output_files[-1]

    return None


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
        console.print(f"[green]✓ File edited: {file_path}[/green]")
    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit file[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
        raise typer.Exit(1)


def _resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number based on iterations that have the specified file.

    Args:
        phase_dir: Phase directory path (e.g., .cafe/issues/issue84/spec)
        iteration_input: User input iteration number (can be positive, 0, negative)
        content_type: Content type to search for (output, context, etc.)

    Returns:
        Actual iteration number (positive integer)

    Raises:
        ValueError: When iteration number does not exist or file not found
    """
    # Get filename for the content type
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # Get all iteration directories (verified by context.json file)
    all_iteration_files = sorted(phase_dir.glob("iteration_*/context.json"))

    if not all_iteration_files:
        raise ValueError(f"No iterations found in {phase_dir}")

    # Extract all iteration numbers
    all_iteration_numbers = []
    for file_path in all_iteration_files:
        dir_name = file_path.parent.name
        if dir_name.startswith("iteration_"):
            try:
                num = int(dir_name.split("_")[1])
                all_iteration_numbers.append(num)
            except (IndexError, ValueError):
                continue

    if not all_iteration_numbers:
        raise ValueError(f"No valid iterations found in {phase_dir}")

    # Find iterations that have the requested file
    iteration_numbers_with_file = []
    for iter_num in all_iteration_numbers:
        iteration_dir = phase_dir / f"iteration_{iter_num:03d}"
        file_path = iteration_dir / filename
        if file_path.exists():
            iteration_numbers_with_file.append(iter_num)

    if not iteration_numbers_with_file:
        raise ValueError(
            f"No iterations found with file '{filename}'. "
            f"Available iterations: {all_iteration_numbers}"
        )

    # Handle different iteration number formats
    if iteration_input == 0:
        # Zero means latest iteration with the file
        return iteration_numbers_with_file[-1]
    elif iteration_input > 0:
        # Positive number used directly, but need to verify existence
        if iteration_input not in iteration_numbers_with_file:
            raise ValueError(
                f"Iteration {iteration_input} not found or does not have '{filename}'. "
                f"Iterations with this file: {iteration_numbers_with_file}"
            )
        return iteration_input
    else:
        # Negative number: -1 means one before latest, -2 means two before, etc.
        # Since 0 already represents latest (iteration_numbers_with_file[-1]),
        # we need to offset: -1 -> [-2], -2 -> [-3], etc.
        try:
            return iteration_numbers_with_file[iteration_input - 1]
        except IndexError:
            raise ValueError(
                f"Iteration index {iteration_input} out of range. "
                f"Iterations with '{filename}': {iteration_numbers_with_file}"
            )


def _get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get file path for specified content type.

    Args:
        phase_dir: Phase directory path
        iteration: Iteration number (resolved to positive integer)
        content_type: Content type (context, output, streaming, etc.)

    Returns:
        Complete file path
    """
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # status and iterations are located at phase directory root level
    if content_type in ["status", "iterations"]:
        return phase_dir / filename
    else:
        # Other files are located in iteration directory
        iteration_dir = phase_dir / f"iteration_{iteration:03d}"
        return iteration_dir / filename


def _get_latest_review_iteration(issue_name: str) -> int:
    """Get the latest review iteration number from iteration directories.

    Args:
        issue_name: Issue name

    Returns:
        Latest iteration number, or 0 if no iterations exist
    """
    review_dir = Path(f".cafe/issues/{issue_name}/review")
    if not review_dir.exists():
        return 0

    # Find all iteration directories
    iteration_dirs = sorted(review_dir.glob("iteration_*"))
    if not iteration_dirs:
        return 0

    # Extract iteration number from the latest directory (e.g., iteration_005 -> 5)
    latest_dir = iteration_dirs[-1]
    try:
        iteration_num = int(latest_dir.name.split("_")[1])
        return iteration_num
    except (IndexError, ValueError):
        return 0


def _execute_next_phase_auto(next_phase: str, issue_name: str) -> None:
    """Execute the next phase in auto mode.

    Args:
        next_phase: Name of the next phase to execute ("plan", "develop", "review", "pr")
        issue_name: Issue name for tracking
    """
    console.print()
    console.print(f"[bold cyan]🤖 Auto mode: executing [bold]{next_phase}[/bold]...[/bold cyan]")
    console.print()

    # Build command
    cmd = [sys.executable, "-m", "cafe.ui.cli", next_phase, "--auto"]

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            # Error already printed by the phase command, just exit
            raise typer.Exit(result.returncode)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]❌ Error executing {next_phase}: {e}[/bold red]")
        raise typer.Exit(1)


@app.command()
def init() -> None:
    """Initialize CAFE configuration for the project.

    Creates .cafe/config.yaml and copies default agents and templates.
    """
    try:
        config_manager = ConfigManager()

        # 1. Check if config already exists
        if config_manager.config_file.exists():
            console.print("[yellow]⚠️  Configuration already exists.[/yellow]")
            console.print(f"[dim]Current config: {config_manager.config_file}[/dim]")
            console.print()

            # Ask user if they want to overwrite
            from cafe.ui.inquirer_prompts import prompt_confirm
            overwrite = prompt_confirm(
                message="Do you want to overwrite the existing configuration?",
                default=False
            )

            if overwrite is None or not overwrite:
                console.print("[yellow]Cancelled. To modify existing config, use `cafe config` commands.[/yellow]")
                raise typer.Exit(0)

            console.print("[yellow]⚠️  Proceeding to overwrite existing configuration...[/yellow]")
            console.print()

        # 2. Copy agents and templates directories
        try:
            # Get package data directory
            import cafe

            package_dir = Path(cafe.__file__).parent
            agents_source = package_dir / "data" / "agents"
            templates_source = package_dir / "data" / "templates"

            console.print("[cyan]Initializing project environment...[/cyan]")

            init_helpers.copy_data_directory(str(agents_source), ".cafe/agents")
            console.print("[green]✓ Copied agents directory[/green]")

            init_helpers.copy_data_directory(str(templates_source), ".cafe/templates")
            console.print("[green]✓ Copied templates directory[/green]")

        except Exception as e:
            console.print(f"[red]Error: Failed to copy files - {e}[/red]")
            raise typer.Exit(1)

        # 3. Check available CLIs
        available_clis = check_available_clis()

        if not available_clis:
            console.print("[red]No supported AI agents found. Please install at least one agent before retrying.[/red]")
            console.print("[yellow]Supported agents: claude, gemini, cursor-agent, copilot[/yellow]")
            raise typer.Exit(1)

        console.print(f"[green]Found available AI agents: {', '.join(available_clis)}[/green]\n")

        # 4. Interactive configuration for three roles
        config = {
            "agents": {},
        }

        roles = [
            ("pm", "PM"),
            ("developer", "Developer"),
            ("reviewer", "Reviewer"),
        ]

        for role_key, role_display in roles:
            console.print(f"[bold cyan]Configuring {role_display} role:[/bold cyan]")

            # Select CLI
            selected_cli = prompt_list(
                message=f"Please select an AI agent for {role_display}:",
                choices=available_clis,
            )
            if not selected_cli:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)

            # Input model name
            model_name_input = prompt_text(
                message=f"Enter model name for {selected_cli} (optional, press Enter to use default):",
                default="",
            )
            if model_name_input is None:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)
            model_name = model_name_input.strip() if model_name_input else None

            # List available agents for this role
            agents = list_available_agents(role_key)

            if not agents:
                console.print(f"[red]Error: Agent files not found for {role_display} role.[/red]")
                console.print(
                    f"[yellow]Please ensure valid .md files exist in .cafe/agents/{role_key}/ directory.[/yellow]"
                )
                raise typer.Exit(1)

            # Create agent choices in "name: description" format
            agent_choices = [f"{name}: {desc}" for name, desc, _ in agents]

            # Select agent
            selected_agent_display = prompt_list(
                message=f"Please select an agent for {role_display}:",
                choices=agent_choices,
            )
            if not selected_agent_display:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)

            # Extract agent name from display string
            selected_agent_name = selected_agent_display.split(":")[0].strip()

            # Store configuration
            config["agents"][role_key] = {
                "name": selected_agent_name,
                "cli": selected_cli,
            }

            if model_name:
                config["agents"][role_key]["model"] = model_name

            console.print("")

        # 5. Save configuration
        config_manager.save_config(config)

        # 6. Display success message
        console.print("[bold green]Configuration saved successfully![/bold green]\n")

        for role_key, role_display in roles:
            role_config = config["agents"][role_key]
            model_display = role_config.get("model") or "default"
            console.print(
                f"- {role_display}: {role_config['cli']} "
                f"(model: {model_display}) (agent: {role_config['name']})"
            )

        console.print("\n[cyan]You can now use `cafe prepare` to start a new development task.[/cyan]")
        console.print(
            "[cyan]To modify settings, use `cafe config` commands. See `cafe config --help` for details.[/cyan]"
        )

    except KeyboardInterrupt:
        console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show CAFE version."""
    console.print("CAFE version 0.1.0")


def _ensure_default_content(cafe_dir: Path) -> None:
    """Ensure .cafe/templates and .cafe/agents exist, copy from package data directory if not.

    Args:
        cafe_dir: Path to .cafe directory
    """
    from cafe.agents.manager import AgentManager

    # Get package data directory
    package_dir = Path(__file__).parent.parent / "data"

    # Initialize templates if not exists
    cafe_templates = cafe_dir / "templates"
    if not cafe_templates.exists():
        # Try package data first, then repo root
        package_templates = package_dir / "templates"
        repo_templates = Path("templates")

        if package_templates.exists():
            shutil.copytree(package_templates, cafe_templates)
        elif repo_templates.exists():
            shutil.copytree(repo_templates, cafe_templates)

    # Initialize agents if not exists
    cafe_agents = cafe_dir / AgentManager.AGENTS_DIR
    if not cafe_agents.exists():
        # Try package data first, then repo root
        package_agents = package_dir / AgentManager.AGENTS_DIR
        repo_agents = Path(AgentManager.AGENTS_DIR)

        if package_agents.exists():
            shutil.copytree(package_agents, cafe_agents)
        elif repo_agents.exists():
            shutil.copytree(repo_agents, cafe_agents)


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
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Enable/disable interactive mode (default: True)",
    ),
    input_method: Optional[str] = typer.Option(
        None,
        "--input-method",
        help="Spec input method: 'manual' or 'github' (required in non-interactive mode)",
    ),
    issue_id: Optional[int] = typer.Option(
        None,
        "--issue-id",
        help="GitHub issue ID (required when --input-method=github)",
    ),
    rigor: Optional[str] = typer.Option(
        None,
        "--rigor",
        help="Spec rigor level: 'low', 'medium', or 'high' (default: medium)",
    ),
    template: Optional[str] = typer.Option(
        None,
        "--template",
        help="Plan template name (default: default)",
    ),
    auto_create_pr: bool = typer.Option(
        False,
        "--auto-create-pr/--no-auto-create-pr",
        help="Automatically create PR after development (default: False, GitHub repos only)",
    ),
) -> None:
    """Prepare issue environment (directory, config, git branch) before running spec phase.

    This command sets up the necessary directory structure, creates a feature branch,
    and saves initial configuration for the issue.

    \b
    Supports both interactive and non-interactive modes:
    - Interactive mode: Prompts for spec/plan configuration (default behavior)
    - Non-interactive mode: Requires configuration via command-line parameters

    \b
    Examples:
        cafe prepare
        cafe prepare fix-login-bug
        cafe prepare fix-bug --no-interactive --input-method=manual --rigor=medium --template=default
        cafe prepare issue-123 --no-interactive --input-method=github --issue-id=123 --rigor=high
        cafe prepare my-feature --base develop
        cafe prepare my-feature --no-check
    """

    try:
        # 1. Check if .cafe/config.yaml exists
        config_file_path = Path(".cafe/config.yaml")
        if not config_file_path.exists():
            console.print("[red]Error: CAFE is not initialized in this repository.[/red]")
            console.print("[yellow]Please run 'cafe init' first to set up CAFE.[/yellow]")
            raise typer.Exit(1)

        # 2. Determine interactive mode and config prompt behavior
        # should_prompt_for_config: Should we show config prompts?
        #   - True if user didn't provide issue_name as argument AND interactive flag is True
        #   - This preserves backward compatibility
        # Store this BEFORE prompting for issue_name
        should_prompt_for_config = (issue_name is None) and interactive

        # 3. Get issue name (from argument or prompt)
        if not issue_name:
            if interactive:
                issue_name = prompt_text("Issue name:")
                if not issue_name or not issue_name.strip():
                    console.print("[red]Error: Issue name cannot be empty.[/red]")
                    raise typer.Exit(1)
                issue_name = issue_name.strip()
            else:
                console.print("[red]Error: Issue name is required in non-interactive mode.[/red]")
                raise typer.Exit(1)

        # 4. Validate non-interactive mode parameters (only when user explicitly passed --no-interactive)
        if not interactive:
            # 4.1. input_method is required in non-interactive mode
            if input_method is None:
                console.print("[red]Error: --input-method is required in non-interactive mode[/red]")
                raise typer.Exit(1)

            # 4.2. input_method must be 'manual' or 'github'
            if input_method not in ["manual", "github"]:
                console.print("[red]Error: --input-method must be 'manual' or 'github'[/red]")
                raise typer.Exit(1)

            # 4.3. When input_method is 'github', issue_id is required
            if input_method == "github" and issue_id is None:
                console.print("[red]Error: --issue-id is required when using --input-method=github[/red]")
                raise typer.Exit(1)

            # 4.4. Validate rigor if provided
            if rigor and rigor not in ["low", "medium", "high"]:
                console.print("[red]Error: --rigor must be 'low', 'medium', or 'high'[/red]")
                raise typer.Exit(1)

            # 4.5. Set default values for optional parameters
            if rigor is None:
                rigor = "medium"
            if template is None:
                template = "default"

        # 5. Initialize Git operations
        try:
            git_ops = GitOperations()
        except Exception as e:
            console.print(f"[red]Error: Not a git repository. {e}[/red]")
            console.print("[yellow]Hint: Run 'git init' to initialize a git repository.[/yellow]")
            raise typer.Exit(1)

        # 6. Check for uncommitted changes (warning only)
        if check_uncommitted and git_ops.has_uncommitted_changes():
            console.print("[yellow]⚠️  Warning: You have uncommitted changes.[/yellow]")
            console.print(
                "[yellow]    It's recommended to commit or stash them before switching branches.[/yellow]"
            )
            console.print()

            # Ask if user wants to continue
            continue_anyway = prompt_confirm("Continue anyway?", default=False)
            if not continue_anyway:
                console.print("[dim]Cancelled.[/dim]")
                raise typer.Exit(0)

        # 7. Determine base branch
        if not base_branch:
            base_branch = git_ops.get_current_branch()

        # 8. Determine worktree mode (interactive or from parameter)
        use_worktree = False
        worktree_path = None

        # If --worktree parameter is provided (non-interactive)
        if worktree and worktree.strip():
            use_worktree = True
            worktree_path = worktree.strip()
        # If in config prompt mode and no --worktree parameter
        elif should_prompt_for_config and not worktree:
            # Ask user if they want to use worktree mode
            use_worktree = prompt_confirm("Use Git worktree mode for this issue?", default=False)

            if use_worktree:
                # Suggest default path
                default_path = f".cafe/worktrees/{issue_name}"
                console.print(f"[dim]Default path: {default_path}[/dim]")

                # Prompt for path (allow empty input to use default)
                user_path = prompt_text(
                    "Worktree path (press Enter for default):",
                    default=default_path,
                )
                worktree_path = user_path.strip() if user_path.strip() else default_path

        console.print()
        console.print(f"[bold blue]🔧 Preparing issue: {issue_name}[/bold blue]")
        console.print(f"Base branch: {base_branch}")
        console.print()

        # 9. Initialize default templates and agents if not exists (in repo root)
        cafe_dir = Path(".cafe")
        _ensure_default_content(cafe_dir)

        # 9.1. Validate template exists (only in non-interactive mode after templates are initialized)
        if not interactive and template:
            template_file = Path(".cafe") / "templates" / "plan" / f"{template}.md"
            if not template_file.exists():
                console.print(f"[red]Error: Template '{template}' not found[/red]")
                console.print(f"   Expected at: {template_file}")
                console.print()
                console.print("[yellow]Available templates:[/yellow]")
                template_dir = Path(".cafe") / "templates" / "plan"
                if template_dir.exists():
                    available = [f.stem for f in template_dir.glob("*.md")]
                    if available:
                        for tmpl in sorted(available):
                            console.print(f"  - {tmpl}")
                    else:
                        console.print("  (none)")
                raise typer.Exit(1)

        # 10. Assemble spec/plan/pr configuration (prompt mode or parameter mode)
        spec_config = {}
        plan_config = {}
        pr_config = {}

        if should_prompt_for_config:
            console.print()
            console.print("[bold cyan]📝 Pre-configure spec and plan phases[/bold cyan]")
            console.print(
                "[dim]This will save time by not asking these questions again in spec/plan phases.[/dim]"
            )
            console.print()

            # Initialize Display for prompts
            display = Display()
            github_ops = GitHubOps()

            # Prompt for input method and issue ID (only for GitHub repos)
            from cafe.utils.git_utils import is_github_repo

            if is_github_repo():
                input_method, issue_id = prompt_for_input_method(display, github_ops)
                spec_config["input_method"] = input_method
                if issue_id is not None:
                    spec_config["issue_id"] = str(issue_id)
            else:
                # Non-GitHub repo: use manual input only
                spec_config["input_method"] = "manual"

            # Prompt for rigor level
            rigor = prompt_for_rigor(display)
            spec_config["rigor"] = rigor

            # Prompt for plan template
            template_manager = TemplateManager(".cafe")
            templates = template_manager.list_templates()

            if templates:
                console.print()
                console.print("[bold cyan]Please select a plan template:[/bold cyan]")
                template_paths = {
                    name: template_manager.get_template_path(name) for name in templates
                }
                selected_template = select_template(templates, template_paths)
                if selected_template:
                    plan_config["template"] = selected_template
            else:
                console.print()
                console.print(
                    "[yellow]⚠️  No plan templates found. Using default template.[/yellow]"
                )
                console.print(
                    "[dim]    Tip: Use 'cafe template add <source> <name>' to add templates.[/dim]"
                )

            # Prompt for PR auto-create setting (only for GitHub repos)
            from cafe.ui.phase_prompts import prompt_and_save_auto_create

            config_file = Path(".cafe") / "issues" / issue_name / "issue.yaml"
            auto_create_pr_result = prompt_and_save_auto_create(config_file, "pr.auto_create")
            pr_config["auto_create"] = auto_create_pr_result
        elif not interactive:
            # Explicit non-interactive mode (--no-interactive): use CLI parameters
            from cafe.utils.git_utils import is_github_repo

            # Spec config
            spec_config["input_method"] = input_method
            if input_method == "github" and issue_id is not None:
                spec_config["issue_id"] = str(issue_id)
            spec_config["rigor"] = rigor

            # Plan config
            plan_config["template"] = template

            # PR config (only for GitHub repos)
            if is_github_repo() and auto_create_pr:
                pr_config["auto_create"] = True
        # else: issue_name was provided as argument but not --no-interactive
        #       Don't save any config (old behavior for backward compatibility)

        # 11. Prepare config data (but don't write yet)
        feature_branch = issue_name

        # Load global config to get default auto settings
        from cafe.utils.config import ConfigManager

        config_manager = ConfigManager(".cafe")
        global_config = config_manager.load_config()
        max_review_iterations = global_config.get("auto", {}).get("max_review_iterations", 5)

        config_data = {
            "base_branch": base_branch,
            "feature_branch": feature_branch,
            "auto": {
                "max_review_iterations": max_review_iterations,
            },
        }

        # Add spec config if present
        if spec_config:
            config_data["spec"] = spec_config

        # Add plan config if present
        if plan_config:
            config_data["plan"] = plan_config

        # Add pr config if present
        if pr_config:
            config_data["pr"] = pr_config

        # Add worktree_path if using worktree mode
        if use_worktree:
            config_data["worktree_path"] = worktree_path

        # 10. Perform Git operations (before writing config)
        if use_worktree:
            # Worktree mode - check if worktree already exists
            if git_ops.worktree_exists(worktree_path):
                console.print(f"[yellow]⚠️  Worktree at '{worktree_path}' already exists[/yellow]")
                console.print("[dim]Reusing existing worktree. Config will be updated.[/dim]")
            else:
                # Check if branch already exists
                if git_ops.branch_exists(feature_branch):
                    # Branch exists but worktree doesn't - create worktree with existing branch
                    console.print(f"[dim]Branch '{feature_branch}' exists, creating worktree...[/dim]")
                    git_ops.run_git("worktree", "add", worktree_path, feature_branch)
                else:
                    # Neither branch nor worktree exists - create both
                    console.print(f"[dim]Creating worktree at '{worktree_path}' with new branch...[/dim]")
                    git_ops.create_worktree(worktree_path, feature_branch, base_branch)

            # Create actual .cafe directory in worktree instead of symlink
            # This avoids permission issues with agent CLIs that resolve symlinks
            import shutil

            worktree_abs = Path(worktree_path).resolve()
            repo_cafe_dir = Path(".cafe").resolve()
            worktree_cafe_dir = worktree_abs / ".cafe"

            # Create .cafe directory structure in worktree
            worktree_cafe_dir.mkdir(parents=True, exist_ok=True)

            # Copy config.yaml from repo root
            repo_config = repo_cafe_dir / "config.yaml"
            worktree_config = worktree_cafe_dir / "config.yaml"
            if repo_config.exists():
                shutil.copy2(repo_config, worktree_config)

            # Create issues directory structure in worktree
            worktree_issues_dir = worktree_cafe_dir / "issues" / issue_name
            worktree_issues_dir.mkdir(parents=True, exist_ok=True)
            (worktree_issues_dir / "spec").mkdir(exist_ok=True)
            (worktree_issues_dir / "sessions").mkdir(exist_ok=True)

            # Initialize default templates and agents in worktree .cafe
            _ensure_default_content(worktree_cafe_dir)

            # Set issue_dir to worktree location for config writing
            issue_dir = worktree_issues_dir
        else:
            # Normal branch mode
            # First create issue directory structure
            issue_dir = Path(f".cafe/issues/{issue_name}")
            spec_dir = issue_dir / "spec"
            sessions_dir = issue_dir / "sessions"

            spec_dir.mkdir(parents=True, exist_ok=True)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            # Then perform git operations
            if git_ops.branch_exists(feature_branch):
                console.print(
                    f"[dim]Branch '{feature_branch}' already exists, switching to it...[/dim]"
                )
                git_ops.checkout_branch(feature_branch)
            else:
                console.print(f"[dim]Creating and switching to branch '{feature_branch}'...[/dim]")
                git_ops.create_branch(feature_branch)

        # 11. Write issue.yaml (after git operations succeed)
        issue_config_file = issue_dir / "issue.yaml"
        with open(issue_config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

        # For worktree mode, also create issue.yaml in repo root for cafe ls to read
        if use_worktree:
            repo_root_issue_dir = Path(f".cafe/issues/{issue_name}")
            repo_root_issue_dir.mkdir(parents=True, exist_ok=True)
            repo_root_config_file = repo_root_issue_dir / "issue.yaml"
            with open(repo_root_config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

        console.print()
        # Display relative path instead of absolute path
        try:
            relative_config_path = issue_config_file.relative_to(Path.cwd())
        except ValueError:
            # If path is not relative to cwd, show absolute path
            relative_config_path = issue_config_file
        console.print(f"[green]✓ Issue config saved to {relative_config_path}[/green]")
        console.print()

        # 12. Display success message
        console.print()
        console.print(f"[green]✓ Successfully prepared issue: {issue_name}[/green]")
        console.print(f"  📁 Directory: .cafe/issues/{issue_name}/")
        console.print(f"  🌿 Feature branch: {feature_branch}")
        console.print(f"  ⚓ Base branch: {base_branch}")
        if use_worktree:
            console.print(f"  📂 Worktree: {worktree_path}")
        console.print(f"  ⚙️  Config: .cafe/issues/{issue_name}/issue.yaml")
        console.print()

        # Show next steps
        if use_worktree:
            console.print("[dim]Next step:[/dim]")
            console.print(f"  [bold]cd {worktree_path}; cafe make[/bold]")
        else:
            console.print("[dim]Next step:[/dim] [bold]cafe make[/bold]")
        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error during prepare: {e}[/red]")
        raise typer.Exit(1)


def _get_project_path() -> str:
    """Get the project path in the ~/.claude/projects/ naming format.

    Converts absolute path like /Users/YO/side_projects/my-project
    to -Users-YO-side-projects-my-project
    """
    repo_root = Path.cwd()
    # Find the git repository root
    original_root = repo_root
    while repo_root != repo_root.parent:
        if (repo_root / ".git").exists():
            break
        repo_root = repo_root.parent
    else:
        # If no .git directory found, use current working directory
        repo_root = original_root

    # Convert to ~/.claude/projects/ naming format: replace / with -
    abs_path = str(repo_root.resolve())
    # Remove leading / and replace remaining / with -
    project_path = abs_path.lstrip("/").replace("/", "-")
    return project_path


@app.command()
def close() -> None:
    """Close current feature and return to base branch.

    \b
    This command:
    1. Checks for open/draft PRs (blocks if found)
    2. For worktree mode: switches back to main repo, removes worktree, deletes branch
    3. For normal mode: switches to base branch, deletes feature branch
    4. Pulls latest changes from remote
    5. Archives .cafe/issues/<issue-name>/ to ~/.cafe/projects/<project-path>/archived/<issue-name>/
    """
    import os
    import shutil

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
                    console.print(
                        "[yellow]Please merge or close the PR first, or use --no-pr-check to skip the check.[/yellow]"
                    )
                    raise typer.Exit(1)
        except GitHubError:
            # If gh CLI is not installed or not authenticated, skip PR check
            pass

        # 4. Load issue config
        issue_config_file = Path(f".cafe/issues/{current_branch}/issue.yaml")
        if not issue_config_file.exists():
            console.print(f"[red]Error: Issue config not found: {issue_config_file}[/red]")
            console.print(
                "[yellow]Hint: This branch may not be initialized with 'cafe prepare'.[/yellow]"
            )
            raise typer.Exit(1)

        with open(issue_config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        base_branch = config_data.get("base_branch", "main")
        feature_branch = current_branch
        issue_name = current_branch  # Issue name is the same as current branch
        worktree_path = config_data.get("worktree_path")

        console.print()
        console.print(f"[bold blue]🔒 Closing issue: {issue_name}[/bold blue]")
        console.print()

        # 5. Handle worktree mode vs normal mode
        if worktree_path:
            # === WORKTREE MODE ===
            # Step 1: Switch back to main repository
            try:
                console.print("[dim]Switching to main repository...[/dim]")
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
                console.print("  1. cd to main repository")
                console.print(f"  2. git checkout {base_branch}")
                console.print("  3. git pull")
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
                console.print("  2. git pull")
                console.print(f"  3. git worktree remove {worktree_path}")
                console.print(f"  4. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 3: Merge or pull changes based on pr.auto_create config
            pr_auto_create = config_data.get("pr", {}).get("auto_create", True)
            try:
                if pr_auto_create is False:
                    # Local review mode: merge feature branch into base branch
                    console.print("[dim]Merging feature branch into base branch...[/dim]")
                    git_ops.merge(feature_branch)
                    console.print(f"[green]✓ Merged feature branch: {feature_branch}[/green]")
                else:
                    # GitHub PR mode: pull latest changes
                    console.print("[dim]Updating base branch...[/dim]")
                    git_ops.pull()
                    console.print("[green]✓ Updated base branch[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to update base branch: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                if pr_auto_create is False:
                    console.print(f"  1. git merge {feature_branch}")
                else:
                    console.print("  1. git pull")
                console.print(f"  2. git worktree remove {worktree_path}")
                console.print(f"  3. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 4: Sync .cafe/issues/{issue_name}/ from worktree to repo root
            try:
                console.print("[dim]Syncing issue data from worktree to repo root...[/dim]")
                import shutil

                worktree_abs = Path(worktree_path).resolve()
                worktree_issue_dir = worktree_abs / ".cafe" / "issues" / feature_branch
                # Use absolute path for repo_issue_dir since we're in main_repo after os.chdir()
                repo_issue_dir = (Path.cwd() / ".cafe" / "issues" / feature_branch).resolve()

                if worktree_issue_dir.exists():
                    # Ensure repo issue dir exists
                    repo_issue_dir.mkdir(parents=True, exist_ok=True)

                    # Copy all subdirectories and files from worktree to repo root
                    for item in worktree_issue_dir.iterdir():
                        if item.is_dir():
                            dest = repo_issue_dir / item.name
                            if dest.exists():
                                shutil.rmtree(dest)
                            shutil.copytree(item, dest)
                        else:
                            shutil.copy2(item, repo_issue_dir / item.name)

                console.print("[green]✓ Synced issue data to repo root[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️  Failed to sync issue data: {e}[/yellow]")
                console.print(
                    f"[yellow]   Issue data remains in worktree at: {worktree_path}/.cafe/issues/{feature_branch}/[/yellow]"
                )
                # Continue with worktree removal even if sync fails

            # Step 5: Remove worktree
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

            # Step 6: Delete feature branch
            try:
                console.print(f"[dim]Deleting feature branch: {feature_branch}[/dim]")
                git_ops.delete_branch(feature_branch)
                console.print(f"[green]✓ Deleted feature branch: {feature_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to delete branch: {e}[/red]")
                console.print("[yellow]The branch may not be fully merged.[/yellow]")
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
                console.print(
                    "[yellow]Hint: You may have uncommitted changes. Please commit or stash them first.[/yellow]"
                )
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git checkout {base_branch}")
                console.print("  2. git pull")
                console.print(f"  3. git branch -d {feature_branch}")
                console.print()
                raise typer.Exit(1)

            # Step 2: Merge or pull changes based on pr.auto_create config
            pr_auto_create = config_data.get("pr", {}).get("auto_create", True)
            try:
                if pr_auto_create is False:
                    # Local review mode: merge feature branch into base branch
                    console.print("[dim]Merging feature branch into base branch...[/dim]")
                    git_ops.merge(feature_branch)
                    console.print(f"[green]✓ Merged feature branch: {feature_branch}[/green]")
                else:
                    # GitHub PR mode: pull latest changes
                    console.print("[dim]Updating base branch...[/dim]")
                    git_ops.pull()
                    console.print("[green]✓ Updated base branch[/green]")
            except Exception as e:
                console.print(f"[red]❌ Failed to update base branch: {e}[/red]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                if pr_auto_create is False:
                    console.print(f"  1. git merge {feature_branch}")
                else:
                    console.print("  1. git pull")
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
                console.print("[yellow]The branch may not be fully merged.[/yellow]")
                console.print()
                console.print("[yellow]Remaining steps (please execute manually):[/yellow]")
                console.print(f"  1. git branch -D {feature_branch}  # Force delete if needed")
                console.print()
                raise typer.Exit(1)

        # 6. Archive issue data to ~/.cafe/projects/<project-path>/archived/<issue-name>/
        try:
            console.print("[dim]Archiving issue data...[/dim]")

            # Get project path in ~/.claude/projects/ naming format
            project_path = _get_project_path()

            # Construct archive path
            home_dir = Path.home()
            archive_base = home_dir / ".cafe" / "projects" / project_path / "archived"
            archive_path = archive_base / issue_name

            # Ensure archive directory exists
            archive_base.mkdir(parents=True, exist_ok=True)

            # Move issue directory to archive
            issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name
            if issue_dir.exists():
                # If archive already exists, remove it first
                if archive_path.exists():
                    shutil.rmtree(archive_path)
                shutil.move(str(issue_dir), str(archive_path))
                console.print(f"[green]✓ Archived issue data to: {archive_path}[/green]")
            else:
                console.print(
                    f"[yellow]⚠️  No issue data found at .cafe/issues/{issue_name}/[/yellow]"
                )
        except Exception as e:
            console.print(f"[yellow]⚠️  Failed to archive issue data: {e}[/yellow]")
            console.print(f"[yellow]   Issue data remains at: .cafe/issues/{issue_name}/[/yellow]")

        # 7. Display success message
        console.print()
        console.print(f"[green]✓ Successfully closed issue: {issue_name}[/green]")
        console.print(
            f"  📁 Issue data archived to: {archive_path if 'archive_path' in locals() else '~/.cafe/projects/.../archived/' + issue_name}"
        )
        console.print(f"  🌿 Current branch: {base_branch}")

        # For worktree mode, remind user to change directory
        if worktree_path:
            console.print()
            console.print(
                "[yellow]⚠️  Your terminal is still in the deleted worktree directory.[/yellow]"
            )
            console.print(f"[yellow]   Please run:[/yellow] [bold]cd {main_repo}[/bold]")

        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error during close: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def restore(
    issue_name: str = typer.Argument(..., help="Issue name to restore")
) -> None:
    """Restore archived issue from backup.

    This command restores an archived issue from ~/.cafe/projects/<project-path>/archived/<issue-name>/
    back to .cafe/issues/<issue-name>/.

    \b
    It performs the following operations:
    1. Verifies backup exists
    2. Auto-checks out the feature branch (creates it if necessary)
    3. For worktree mode, auto-navigates to the worktree directory
    4. Prompts user for confirmation
    5. Restores all files from backup

    \b
    Examples:
        cafe restore issue80
    """
    import shutil

    try:
        # 1. Get project path and construct archive path
        project_path = _get_project_path()
        home_dir = Path.home()
        archive_base = home_dir / ".cafe" / "projects" / project_path / "archived"
        archive_path = archive_base / issue_name

        # 2. Check if backup exists
        if not archive_path.exists():
            console.print()
            console.print(f"[red]❌ Error: Backup not found for issue '{issue_name}'[/red]")
            console.print(f"   Backup path: {archive_path}")
            console.print()
            raise typer.Exit(1)

        console.print()
        console.print(f"[bold blue]🔄 Restoring issue: {issue_name}[/bold blue]")
        console.print(f"   From: {archive_path}")
        console.print()

        # 3. Read issue.yaml from backup to get branch and worktree configuration
        issue_config_file = archive_path / "issue.yaml"
        if not issue_config_file.exists():
            console.print(f"[red]❌ Error: issue.yaml not found in backup[/red]")
            console.print(f"   Expected at: {issue_config_file}")
            console.print()
            raise typer.Exit(1)

        with open(issue_config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        feature_branch = config_data.get("feature_branch", issue_name)
        worktree_path = config_data.get("worktree_path")

        # 4. Initialize Git operations and auto-checkout feature branch
        try:
            git_ops = GitOperations()
        except Exception as e:
            console.print(f"[red]Error: Not a git repository. {e}[/red]")
            raise typer.Exit(1)

        current_branch = git_ops.get_current_branch()
        if not current_branch:
            console.print("[red]Error: Not on a valid branch (detached HEAD?).[/red]")
            raise typer.Exit(1)

        # 5. For worktree mode, create worktree first (before checkout)
        # This avoids branch conflict issues
        if worktree_path:
            worktree_path_obj = Path(worktree_path)
            if not worktree_path_obj.exists():
                console.print(f"[yellow]ℹ️  Creating worktree: {worktree_path}[/yellow]")
                try:
                    # Create worktree parent directory
                    worktree_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    # Try to create worktree with new branch first
                    git_ops.run_git("worktree", "add", "-b", feature_branch, worktree_path)
                    console.print(f"[green]✓ Created worktree at: {worktree_path}[/green]")
                    current_branch = feature_branch  # Update since we created the branch
                except Exception as e:
                    # If branch already exists, try without -b flag
                    if "already exists" in str(e):
                        console.print(f"[yellow]ℹ️  Branch '{feature_branch}' already exists, creating worktree[/yellow]")
                        try:
                            git_ops.run_git("worktree", "add", worktree_path, feature_branch)
                            console.print(f"[green]✓ Created worktree at: {worktree_path}[/green]")
                            current_branch = feature_branch
                        except Exception as e2:
                            if "already used by worktree" in str(e2):
                                console.print(f"[yellow]ℹ️  Branch '{feature_branch}' is already in another worktree[/yellow]")
                            else:
                                console.print(f"[red]❌ Error: Failed to create worktree: {e2}[/red]")
                                raise typer.Exit(1)
                    else:
                        console.print(f"[red]❌ Error: Failed to create worktree: {e}[/red]")
                        raise typer.Exit(1)

        # 6. Auto-checkout feature branch if not already on it
        if current_branch != feature_branch:
            console.print(f"[yellow]ℹ️  Checking out feature branch: {feature_branch}[/yellow]")
            try:
                # Check if branch exists, create if it doesn't
                if not git_ops.branch_exists(feature_branch):
                    console.print(f"[dim]Creating new branch: {feature_branch}[/dim]")
                    git_ops.create_branch(feature_branch)
                git_ops.checkout_branch(feature_branch)
                console.print(f"[green]✓ Checked out branch: {feature_branch}[/green]")
            except Exception as e:
                console.print(f"[red]❌ Error: Failed to checkout branch {feature_branch}: {e}[/red]")
                raise typer.Exit(1)

        # 7. Navigate to worktree directory if it was created
        if worktree_path:
            worktree_path_obj = Path(worktree_path)
            if worktree_path_obj.exists():
                current_path = Path.cwd().resolve()
                expected_worktree = worktree_path_obj.resolve()

                # Check if already in worktree
                try:
                    is_in_worktree = current_path.is_relative_to(expected_worktree)
                except AttributeError:
                    try:
                        current_path.relative_to(expected_worktree)
                        is_in_worktree = True
                    except ValueError:
                        is_in_worktree = False

                if not is_in_worktree:
                    console.print(f"[yellow]ℹ️  Navigating to worktree directory: {worktree_path}[/yellow]")
                    try:
                        import os
                        os.chdir(worktree_path)
                        console.print(f"[green]✓ Changed directory to: {worktree_path}[/green]")
                    except Exception as e:
                        console.print(f"[red]❌ Error: Failed to change directory to {worktree_path}: {e}[/red]")
                        raise typer.Exit(1)

        # 8. Prompt user for confirmation
        console.print("[yellow]⚠️  Warning: This will restore the issue from backup.[/yellow]")
        console.print("[yellow]   Any current changes in .cafe/issues/{} will be overwritten.[/yellow]".format(issue_name))
        console.print()

        # Use typer.confirm for confirmation
        confirmed = typer.confirm("Do you want to continue?", default=False)
        if not confirmed:
            console.print()
            console.print("[yellow]Restore cancelled.[/yellow]")
            console.print()
            raise typer.Exit(1)

        # 9. Perform the restore operation
        console.print()
        console.print("[dim]Restoring issue data...[/dim]")

        # Target path
        issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name

        # If target path already exists, delete it first
        if issue_dir.exists():
            console.print(f"[dim]Removing existing issue directory...[/dim]")
            shutil.rmtree(issue_dir)

        # Copy data from backup
        console.print(f"[dim]Copying data from backup...[/dim]")
        shutil.copytree(archive_path, issue_dir)

        # 10. Display success message
        console.print()
        console.print(f"[green]✓ Successfully restored issue: {issue_name}[/green]")
        console.print(f"  📁 Restored to: .cafe/issues/{issue_name}/")
        console.print(f"  🌿 Branch: {feature_branch}")
        if worktree_path:
            console.print(f"  📂 Worktree: {worktree_path}")
        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error during restore: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def spec(
    ctx: typer.Context,
    action: Optional[str] = typer.Argument(None, help="Action: edit (to edit latest spec file)"),
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
    pm_agent: Optional[str] = typer.Option(
        None,
        "--pm",
        help="PM agent name (defaults to config)",
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
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto mode: automatically continue iterations until CAFE_CONFIRMED",
    ),
) -> None:
    """Run specification phase: Spec clarification with conversational generation.

    The PM agent will engage in a dialogue with you to clarify and generate
    a complete specification document. No technical details will be discussed.

    This command automatically uses the current Git branch name as the issue identifier.

    Use 'cafe spec edit' to edit the latest specification file.

    \b
    Examples:
        cafe spec
        cafe spec --auto
        cafe spec -m github
        cafe spec -m github -i 123
        cafe spec --pm CustomPM
        cafe spec --rigor low
        cafe spec edit
    """
    # Handle edit action
    if action == "edit":
        try:
            # Get and validate current branch
            issue_name = _get_and_validate_branch(ctx, "spec")

            # Find latest spec file
            spec_file = _get_latest_versioned_file("spec", issue_name)
            if not spec_file:
                console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
                console.print(
                    "[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]"
                )
                raise typer.Exit(1)

            # Edit the file
            _edit_file_with_editor(spec_file)
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "spec")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Load issue config to get saved rigor setting
        import yaml

        issue_config_file = Path(f".cafe/issues/{issue_name}/issue.yaml")
        saved_rigor = None
        if issue_config_file.exists():
            with open(issue_config_file, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
                spec_config = config_data.get("spec", {})
                saved_rigor = spec_config.get("rigor")

        # Validate rigor (if specified via flag, otherwise use saved value)
        spec_rigor = None
        if rigor:
            # CLI flag takes precedence
            try:
                from cafe.core.types import SpecRigor

                spec_rigor = SpecRigor(rigor)
            except ValueError:
                console.print(
                    f"[red]Error: Invalid rigor '{rigor}'. Use 'low', 'medium', or 'high'.[/red]"
                )
                raise typer.Exit(1)
        elif saved_rigor:
            # Use saved rigor from config
            try:
                from cafe.core.types import SpecRigor

                spec_rigor = SpecRigor(saved_rigor)
            except ValueError:
                # Ignore invalid saved value
                pass

        # Create spec directory if it doesn't exist
        spec_dir = Path(f".cafe/issues/{issue_name}/spec")
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get PM agent name (from flag or config)
        if pm_agent is None:
            pm_agent = config_manager.get("agents.pm.name", "Roger")

        # Get PM agent CLI
        pm_executor = agent_manager.get_agent(pm_agent)
        pm_cli = pm_executor.config.cli.value
        pm_session_id = pm_executor.config.session_id or "(will be created)"

        # Display start message
        console.print("[bold blue]🎯 Spec Phase: Specification Clarification[/bold blue]")
        console.print(f"Issue: {issue_name}")
        console.print(f"PM Agent: {pm_agent}")
        pm_model = pm_executor.config.model or "default"
        console.print(f"CLI: {pm_cli}")
        console.print(f"Model: {pm_model}")
        console.print(f"Session ID: {pm_session_id}")
        if spec_rigor:
            console.print(f"Rigor: {spec_rigor.value}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec directory: {spec_dir}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        console.print()

        # Determine if should be interactive
        import sys

        is_interactive = interactive and sys.stdin.isatty()

        # Validate auto mode constraints
        if auto and not is_interactive:
            console.print("[red]Error: --auto can only be used in interactive mode[/red]")
            raise typer.Exit(1)

        # Validate user_input in non-interactive mode (unless using --issue-id to fetch)
        if not is_interactive and not user_input and not fetch_issue_id:
            console.print(
                "[red]Error: --user-input is required when using --no-interactive (or use --issue-id to fetch from GitHub)[/red]"
            )
            raise typer.Exit(1)

        # Create and execute spec phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
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
        if auto:
            console.print(
                "[dim]🤖 Auto mode: will automatically continue iterations until CAFE_CONFIRMED[/dim]"
            )
        console.print()

        # Execute phase iterations (with recursion for auto-continue)
        def execute_iteration(iteration_count=1):
            """Execute one iteration and optionally continue to next"""
            if iteration_count > 1:
                console.print(f"\n[bold cyan]━━━ Iteration {iteration_count} ━━━[/bold cyan]\n")

            # Execute phase
            result = phase.execute()

            # Check result status
            if result.status.value not in ["completed", "in_progress"]:
                return result  # Phase failed

            status_code = result.data.get("status_code")
            if not status_code:
                return result  # No valid status code

            # Check if we should continue
            if status_code == "CAFE_CONFIRMED":
                return result  # Reached final state

            elif status_code in ["CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"]:
                # Only continue iterations in interactive mode (with or without --auto)
                if not is_interactive:
                    # Non-interactive mode: stop after first iteration
                    return result

                # Show brief status
                console.print()
                if status_code == "CAFE_NEED_CLARIFICATION":
                    console.print("[yellow]💬 Agent needs clarification[/yellow]")
                else:  # CAFE_READY_FOR_REVIEW
                    console.print("[yellow]📝 Draft ready for review[/yellow]")

                # Decide whether to continue
                should_continue = False
                if auto:
                    # Auto mode: continue automatically
                    console.print("[dim]Auto mode: continuing to next iteration...[/dim]")
                    should_continue = True
                else:
                    # Interactive mode: ask user
                    should_continue = prompt_confirm(
                        message="Continue to next iteration?", default=True
                    )

                if should_continue:
                    console.print("[dim]Continuing...[/dim]")
                    return execute_iteration(iteration_count + 1)
                else:
                    console.print("[dim]Stopped by user.[/dim]")
                    return result
            else:
                # Unknown status
                console.print(f"\n[bold yellow]⚠️  Unknown status code: {status_code}[/bold yellow]")
                return result

        # Start execution
        result = execute_iteration()

        # Display result
        if result.status.value in ["completed", "in_progress"]:
            console.print()
            status_code = result.data.get("status_code")

            # If no valid status code, treat as failure
            if not status_code:
                console.print(
                    "[bold red]❌ Spec phase failed: No valid status code returned[/bold red]"
                )
                raise typer.Exit(1)

            if status_code == "CAFE_NEED_CLARIFICATION":
                console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                spec_file = result.data.get("spec_file")
                if spec_file:
                    console.print(f"Saved to: {spec_file}")
                console.print()
                console.print("[dim]To continue, run:[/dim] [bold]cafe spec[/bold]")
            elif status_code == "CAFE_READY_FOR_REVIEW":
                # Spec draft is ready, but needs user confirmation
                console.print("[bold green]✅ Spec draft completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                spec_file = result.data.get("spec_file")
                if spec_file:
                    console.print(f"Saved to: {spec_file}")
                console.print()
                console.print("[dim]Please review the spec and run:[/dim] [bold]cafe spec[/bold]")
            elif status_code == "CAFE_CONFIRMED":
                # Spec is confirmed, ready to proceed to plan
                console.print("[bold green]✅ Spec clarification completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                spec_file = result.data.get("spec_file")
                if spec_file:
                    console.print(f"Saved to: {spec_file}")
                console.print()

                # Auto mode: execute next phase
                if auto:
                    _execute_next_phase_auto("plan", issue_name)
                else:
                    console.print("[dim]Next step:[/dim] [bold]cafe plan[/bold]")
            else:
                # Unknown status code - show generic completion message
                console.print("[bold green]✅ Spec phase completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                console.print(f"Status: {status_code}")
                spec_file = result.data.get("spec_file")
                if spec_file:
                    console.print(f"Saved to: {spec_file}")
        else:
            console.print()
            console.print(f"[bold red]❌ Spec phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        _handle_phase_exception(e, "spec", auto=auto)


@app.command()
def plan(
    ctx: typer.Context,
    action: Optional[str] = typer.Argument(None, help="Action: edit (to edit latest plan file)"),
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
    dev_agent: Optional[str] = typer.Option(
        None,
        "--dev",
        help="Developer agent name (defaults to config)",
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
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto mode: automatically continue iterations until CAFE_CONFIRMED",
    ),
) -> None:
    """Run plan phase: Implementation planning with developer agent.

    The developer agent will analyze the specification and create a detailed
    implementation plan with technical considerations and development guide.

    This command automatically uses the current Git branch name as the issue identifier.

    Use 'cafe plan edit' to edit the latest plan file.

    \b
    Examples:
        cafe plan
        cafe plan --auto
        cafe plan -m github -i 123
        cafe plan --dev CustomDev
        cafe plan edit
    """
    # Handle edit action
    if action == "edit":
        try:
            # Get and validate current branch
            issue_name = _get_and_validate_branch(ctx, "plan")

            # Find latest plan file
            plan_file = _get_latest_versioned_file("plan", issue_name)
            if not plan_file:
                console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
                console.print("[dim]Hint: Run 'cafe plan' first to create the plan.[/dim]")
                raise typer.Exit(1)

            # Edit the file
            _edit_file_with_editor(plan_file)
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "plan")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Check if spec file exists (use latest versioned file)
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        # Check if plan already exists (any versioned plan file)
        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        is_resume = plan_file_path is not None

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get developer agent name (from flag or config)
        if dev_agent is None:
            dev_agent = config_manager.get("agents.developer.name", "David")

        # Get developer agent CLI
        dev_executor = agent_manager.get_agent(dev_agent)
        dev_cli = dev_executor.config.cli.value
        dev_session_id = dev_executor.config.session_id or "(will be created)"

        # Handle template selection
        template_manager = TemplateManager(config_dir)
        selected_template = None
        template_mode = "auto"  # Track if template is 'auto' or manually specified

        if is_resume:
            console.print(f"[dim]Resuming existing plan from: {plan_file_path}[/dim]")

        if template:
            # Template specified via --template option
            if not template_manager.template_exists(template):
                console.print(f"[red]Error: Template '{template}' not found[/red]")
                console.print("[dim]Use 'cafe template list' to see available templates[/dim]")
                raise typer.Exit(1)
            selected_template = template
            template_mode = "manual"
        elif not is_resume:
            # No template specified and not resuming
            # First, try to load template from issue.yaml
            import yaml
            issue_config_file = Path(f".cafe/issues/{issue_name}/issue.yaml")
            template_from_config = None

            if issue_config_file.exists():
                try:
                    with open(issue_config_file, "r") as f:
                        issue_config = yaml.safe_load(f)
                    template_from_config = issue_config.get("plan", {}).get("template")
                except Exception:
                    pass  # Ignore config read errors, will prompt user

            if template_from_config:
                # Use template from config
                selected_template = template_from_config
                if template_from_config == "auto":
                    template_mode = "auto"
                else:
                    template_mode = "manual"
                console.print(f"[dim]Using template from config: {template_from_config}[/dim]")
            else:
                # No template in config - need to select one for first iteration
                if interactive:
                    # Interactive mode: prompt user to select 'auto' or a specific template
                    import sys
                    is_interactive = sys.stdin.isatty()

                    if is_interactive:
                        from cafe.ui.template_selector import select_template

                        templates = template_manager.list_templates()
                        template_paths = {name: template_manager.get_template_path(name) for name in templates}
                        selected_template = select_template(templates, template_paths)

                        if selected_template == "auto":
                            template_mode = "auto"
                        else:
                            template_mode = "manual"
                    else:
                        # Non-interactive but interactive flag set (piped stdin)
                        # Default to auto mode
                        selected_template = "auto"
                        template_mode = "auto"
                else:
                    # Non-interactive mode with no --template: default to auto
                    selected_template = "auto"
                    template_mode = "auto"

        # Display start message
        console.print("[bold blue]📋 Plan Phase: Implementation Planning[/bold blue]")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent}")
        dev_model = dev_executor.config.model or "default"
        console.print(f"CLI: {dev_cli}")
        console.print(f"Model: {dev_model}")
        console.print(f"Session ID: {dev_session_id}")
        if workflow_mode == WorkflowMode.LOCAL:
            console.print(f"Spec file: {spec_file_path}")
        elif issue_id:
            console.print(f"GitHub Issue: #{issue_id}")
        if selected_template:
            if template_mode == "auto":
                console.print("[dim]Template mode: auto (agent will decide)[/dim]")
            else:
                console.print(f"[dim]Template: {selected_template}[/dim]")
        console.print()

        # Get template path if manually selected (not auto)
        template_path_str = None
        if selected_template and template_mode == "manual":
            template_path_obj = template_manager.get_template_path(selected_template)
            if template_path_obj:
                template_path_str = str(template_path_obj)

        # Create and execute plan phase
        # Note: spec_file parameter is deprecated, PlanPhase computes latest versioned files internally
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=(
                str(spec_file_path) if spec_file_path else ""
            ),  # Deprecated - computed internally
            workflow_mode=workflow_mode,
            issue_id=issue_id,
            issue_name=issue_name,
            dev_agent=dev_agent,
            interactive=interactive,
            template_path=template_path_str,
            template_mode=template_mode,  # Pass template mode to plan phase
        )

        # Determine if should be interactive
        import sys

        is_interactive = interactive and sys.stdin.isatty()

        # Validate auto mode constraints
        if auto and not is_interactive:
            console.print("[red]Error: --auto can only be used in interactive mode[/red]")
            raise typer.Exit(1)

        console.print("[bold]Starting implementation planning...[/bold]")
        console.print(
            "[dim]The developer will analyze technical feasibility and create implementation plan.[/dim]"
        )
        if auto:
            console.print(
                "[dim]🤖 Auto mode: will automatically continue iterations until CAFE_CONFIRMED[/dim]"
            )
        console.print()

        # Execute phase iterations (with recursion for auto-continue)
        def execute_iteration(iteration_count=1):
            """Execute one iteration and optionally continue to next"""
            if iteration_count > 1:
                console.print(f"\n[bold cyan]━━━ Iteration {iteration_count} ━━━[/bold cyan]\n")

            # Execute phase
            result = phase.execute()

            # Check result status
            if result.status.value != "completed":
                return result  # Phase failed

            status_code = result.data.get("status_code")
            if not status_code:
                return result  # No valid status code

            # Check if we should continue
            if status_code == "CAFE_CONFIRMED":
                return result  # Reached final state

            elif status_code in ["CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"]:
                # Only continue iterations in interactive mode (with or without --auto)
                if not is_interactive:
                    # Non-interactive mode: stop after first iteration
                    return result

                # Show brief status
                console.print()
                if status_code == "CAFE_NEED_CLARIFICATION":
                    console.print("[yellow]💬 Agent needs clarification[/yellow]")
                else:  # CAFE_READY_FOR_REVIEW
                    console.print("[yellow]📋 Plan ready for review[/yellow]")

                # Decide whether to continue
                should_continue = False
                if auto:
                    # Auto mode: continue automatically
                    console.print("[dim]Auto mode: continuing to next iteration...[/dim]")
                    should_continue = True
                else:
                    # Interactive mode: ask user
                    should_continue = prompt_confirm(
                        message="Continue to next iteration?", default=True
                    )

                if should_continue:
                    console.print("[dim]Continuing...[/dim]")
                    return execute_iteration(iteration_count + 1)
                else:
                    console.print("[dim]Stopped by user.[/dim]")
                    return result
            else:
                # Unknown status
                console.print(f"\n[bold yellow]⚠️  Unknown status code: {status_code}[/bold yellow]")
                return result

        # Start execution
        result = execute_iteration()

        # Display result
        if result.status.value == "completed":
            console.print()
            status_code = result.data.get("status_code")

            if status_code == "CAFE_NEED_CLARIFICATION":
                console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                plan_file = result.data.get("plan_file")
                if plan_file:
                    console.print(f"Saved to: {plan_file}")
                console.print()
                console.print("[dim]To continue, run:[/dim] [bold]cafe plan[/bold]")
            elif status_code == "CAFE_READY_FOR_REVIEW":
                console.print("[bold yellow]📋 Plan ready for review[/bold yellow]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                plan_file = result.data.get("plan_file")
                if plan_file:
                    console.print(f"Saved to: {plan_file}")
                console.print()
                console.print("[dim]To review the plan, run:[/dim] [bold]cafe plan[/bold]")
            else:
                # CAFE_CONFIRMED
                console.print("[bold green]✅ Implementation plan completed![/bold green]")
                console.print(f"Iterations: {result.data.get('iterations', 'N/A')}")
                plan_file = result.data.get("plan_file")
                if plan_file:
                    console.print(f"Saved to: {plan_file}")
                console.print()

                # Auto mode: execute next phase
                if auto:
                    _execute_next_phase_auto("develop", issue_name)
                else:
                    console.print("[dim]Next step:[/dim] [bold]cafe develop[/bold]")
        else:
            console.print()
            console.print(f"[bold red]❌ Plan phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        _handle_phase_exception(e, "plan", auto=auto)


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
    dev_agent: Optional[str] = typer.Option(
        None,
        "--dev",
        help="Developer agent name (defaults to config)",
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
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto mode: continue iterations automatically and execute cafe review after completion",
    ),
) -> None:
    """Run develop phase: Execute development work according to plan.

    The developer agent will implement the planned features, running tests and
    making commits according to the implementation plan.

    This command automatically uses the current Git branch name as the issue identifier.

    \b
    Examples:
        cafe develop
        cafe develop --dev CustomDev
        cafe develop --pr-number 123
        cafe develop --no-interactive --approve-denied-tools 0,2 --user-input "Please be careful"
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

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print(
                "[dim]Hint: Run 'cafe plan' first to create the implementation plan.[/dim]"
            )
            raise typer.Exit(1)

        # Convert to strings for compatibility
        spec_file = str(spec_file_path)
        plan_file = str(plan_file_path)

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get developer agent name (from flag or config)
        if dev_agent is None:
            dev_agent = config_manager.get("agents.developer.name", "David")

        # Get developer agent CLI
        dev_executor = agent_manager.get_agent(dev_agent)
        dev_cli = dev_executor.config.cli.value
        dev_session_id = dev_executor.config.session_id or "(will be created)"

        # Display start message
        console.print("[bold blue]🔨 Develop Phase: Development Execution[/bold blue]")
        console.print(f"Issue: {issue_name}")
        console.print(f"Developer Agent: {dev_agent}")
        dev_model = dev_executor.config.model or "default"
        console.print(f"CLI: {dev_cli}")
        console.print(f"Model: {dev_model}")
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
                console.print(
                    f"[red]Error: --approve-denied-tools must be comma-separated integers (e.g., '0,1,3'). Got: {approve_denied_tools}[/red]"
                )
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

            # Auto mode: execute next phase
            if auto:
                _execute_next_phase_auto("review", issue_name)
            else:
                console.print("[dim]Next steps:[/dim]")
                console.print("[dim]  1. Review changes: git diff[/dim]")
                console.print("[dim]  2. Run tests: pytest[/dim]")
                console.print("[dim]  3. Code review: cafe review[/dim]")
        elif result.status.value == "failed":
            console.print(f"[red]❌ Development failed: {result.message}[/red]")
            raise typer.Exit(1)
        elif result.status.value == "in_progress":
            # Development paused (e.g., NEED_CLARIFICATION, NEED_PERMISSION)
            if auto:
                _execute_next_phase_auto("develop", issue_name)
            else:
                console.print(f"[yellow]⏸️  Development paused: {result.message}[/yellow]")
                console.print("[dim]Resume with: cafe develop[/dim]")

    except Exception as e:
        _handle_phase_exception(e, "develop", auto=auto)


# Add "dev" as an alias for "develop"
# Use the same function with different name to ensure parameter sync
app.command(name="dev", hidden=False)(develop)


@app.command()
def review(
    ctx: typer.Context,
    action: Optional[str] = typer.Argument(None, help="Action: edit (to edit latest review file)"),
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
    reviewer_agent: Optional[str] = typer.Option(
        None,
        "--reviewer",
        help="Reviewer agent name (defaults to config)",
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
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto mode: automatically execute next phase based on result",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-execution even if review already completed",
    ),
) -> None:
    """Run review phase: Code review by reviewer agent.

    The reviewer agent will review code changes and provide feedback.
    Each execution performs one review iteration.

    This command automatically uses the current Git branch name as the issue identifier.

    \b
    Examples:
        cafe review
        cafe review --commit abc123
        cafe review --reviewer CustomReviewer
        cafe review --force
        cafe review edit
    """
    # Handle edit action
    if action == "edit":
        try:
            # Get and validate current branch
            issue_name = _get_and_validate_branch(ctx, "review")

            # Find latest review file
            review_file = _get_latest_versioned_file("review", issue_name)
            if not review_file:
                console.print(f"[red]Error: No review file found for issue '{issue_name}'[/red]")
                console.print("[dim]Hint: Run 'cafe review' first to create the review.[/dim]")
                raise typer.Exit(1)

            # Edit the file
            _edit_file_with_editor(review_file)
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "review")

        # Validate mode
        try:
            workflow_mode = WorkflowMode(mode)
        except ValueError:
            console.print(f"[red]Error: Invalid mode '{mode}'. Use 'local' or 'github'.[/red]")
            raise typer.Exit(1)

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print(
                "[dim]Hint: Run 'cafe plan' first to create the implementation plan.[/dim]"
            )
            raise typer.Exit(1)

        # Convert to strings for compatibility
        spec_file = str(spec_file_path)
        plan_file = str(plan_file_path)

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        # Set show_prompt flag
        agent_manager.show_prompt = show_prompt

        # Get reviewer agent name (from flag or config)
        if reviewer_agent is None:
            reviewer_agent = config_manager.get("agents.reviewer.name", "Richard")

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
            force=force,
        )

        # Display start message (use actual base_branch from phase)
        console.print("[bold blue]🔍 Review Phase: Code Review[/bold blue]")
        console.print(f"Issue: {issue_name}")
        console.print(f"Reviewer Agent: {reviewer_agent}")
        reviewer_model = reviewer_executor.config.model or "default"
        console.print(f"CLI: {reviewer_cli}")
        console.print(f"Model: {reviewer_model}")
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

                # Auto mode: execute PR phase
                if auto:
                    _execute_next_phase_auto("pr", issue_name)
                else:
                    console.print("[dim]Next steps:[/dim]")
                    console.print("[dim]  1. Create PR: cafe pr[/dim]")
            else:
                # CAFE_NEEDS_CHANGES or other status
                console.print(
                    f"[bold yellow]📝 Code review completed with status: {status_code}[/bold yellow]"
                )
                console.print()

                # Find latest review file (iteration_XXX/output.md format)
                review_dir = Path(f".cafe/issues/{issue_name}/review")
                iteration_files = sorted(review_dir.glob("iteration_*/output.md"))
                if iteration_files:
                    latest_review = iteration_files[-1]
                    review_path = f".cafe/issues/{issue_name}/review/{latest_review.parent.name}/output.md"
                else:
                    # Fallback for old format
                    review_path = f".cafe/issues/{issue_name}/review/review.md"

                console.print("[dim]Review feedback saved to:[/dim]")
                console.print(f"[dim]  {review_path}[/dim]")
                console.print()

                # Auto mode: check max_review_iterations and execute develop if not exceeded
                if auto:
                    # Read max_review_iterations from global config
                    max_iterations_value = config_manager.get("auto.max_review_iterations", 5)
                    # Convert to int if it's a string
                    try:
                        max_iterations = int(max_iterations_value)
                    except (ValueError, TypeError):
                        max_iterations = 5

                    # Get current review iteration count
                    current_iteration = _get_latest_review_iteration(issue_name)

                    if current_iteration >= max_iterations:
                        # Exceeded max iterations
                        console.print()
                        console.print(
                            f"[bold yellow]⚠️  Review loop limit reached ({max_iterations} times)[/bold yellow]"
                        )
                        console.print()
                        console.print("[dim]You can:[/dim]")
                        console.print(
                            "[dim]  • Continue: [bold]cafe review[/bold] (without --auto)[/dim]"
                        )
                        console.print(
                            "[dim]  • Adjust limit: [bold]cafe config set auto.max_review_iterations 10[/bold][/dim]"
                        )
                    else:
                        # Continue with develop phase
                        console.print(
                            f"[dim]Review iteration: {current_iteration}/{max_iterations}[/dim]"
                        )
                        _execute_next_phase_auto("develop", issue_name)
                else:
                    console.print("[dim]Next steps:[/dim]")
                    console.print(f"[dim]  1. Review feedback: cat {review_path}[/dim]")
                    console.print("[dim]  2. Make changes: cafe develop[/dim]")
                    console.print("[dim]  3. Review again: cafe review[/dim]")
        else:
            console.print()
            console.print(f"[bold red]❌ Review phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        _handle_phase_exception(e, "review", auto=auto)


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
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto mode: automatically update existing PR without asking",
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

    \b
    Examples:
        cafe pr
        cafe pr --no-draft
        cafe pr --title "Add user authentication" --body "Implements login/logout"
        cafe pr --no-interactive
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "pr")

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe plan' first to create the plan.[/dim]")
            raise typer.Exit(1)

        # Convert to strings for compatibility
        spec_file = str(spec_file_path)
        plan_file = str(plan_file_path)

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        agent_manager = _setup_agents(config_manager, issue_name=issue_name)
        permission_handler = PermissionHandler()
        git_ops = GitOperations()

        from cafe.utils.github import GitHubOps

        github_ops = GitHubOps()

        # Determine final draft value
        final_draft = draft if draft is not None else True  # Default to draft

        # In auto mode, automatically update existing PR
        final_update = update or auto

        # Get developer agent name from config (for PR generation)
        dev_agent = config_manager.get("agents.developer.name", "David")

        # Create PR phase
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,  # Always use local mode (no --mode flag)
            issue_name=issue_name,
            dev_agent=dev_agent,
            draft=final_draft,
            custom_title=title,
            custom_body=body,
            update=final_update,
            force_push=force,
            interactive=interactive,
            base_branch=base if base != "main" else None,  # Pass base only if not default
        )

        result = phase.execute()

        # Display result
        if result.status.value == "completed":
            pr_number = result.data.get("pr_number")
            pr_url = result.data.get("pr_url")
            is_local_review = result.data.get("local_review", False)
            status_code = result.data.get("status_code")

            # Only show success message and details for GitHub PR mode
            # Local review mode already prints its own messages
            if not is_local_review:
                console.print()
                console.print(f"[bold green]✅ {result.message}![/bold green]")
                console.print()

            if is_local_review:
                # Local review mode: Show local-specific next steps
                if status_code == "CAFE_CONFIRMED":
                    # Read issue config to get base_branch, feature_branch, worktree_path
                    import yaml

                    issue_config_file = Path(f".cafe/issues/{issue_name}/issue.yaml")
                    base_branch = "main"
                    feature_branch = issue_name
                    worktree_path = None

                    if issue_config_file.exists():
                        with open(issue_config_file, "r") as f:
                            issue_config = yaml.safe_load(f)
                        base_branch = issue_config.get("base_branch", "main")
                        feature_branch = issue_config.get("feature_branch", issue_name)
                        worktree_path = issue_config.get("worktree_path")

                    console.print("[dim]Next step: [bold]cafe close[/bold] - this will do[/dim]")
                    console.print(f"[dim]  1. checkout branch: {base_branch}[/dim]")
                    console.print(f"[dim]  2. merge branch: {feature_branch}[/dim]")
                    console.print(f"[dim]  3. delete branch: {feature_branch}[/dim]")
                    if worktree_path:
                        console.print(f"[dim]  4. delete worktree: {worktree_path}[/dim]")
                    console.print()
                elif status_code == "CAFE_NEEDS_CHANGES":
                    # If in auto mode, automatically run develop phase
                    if auto:
                        # Get the pr feedback file path from result
                        pr_file = result.data.get("pr_file")
                        if pr_file:
                            console.print(f"[dim]Using modification request from: {pr_file}[/dim]")
                            console.print()

                        # Execute develop phase in auto mode
                        _execute_next_phase_auto("develop", issue_name)
            elif pr_url:
                # GitHub PR mode: Show PR URL and GitHub-specific next steps
                files_url = pr_url + "/files"
                console.print(f"[bold cyan]{files_url}[/bold cyan]")
                console.print()
                console.print("[dim]Next steps:[/dim]")
                console.print(
                    "[dim]  1. Review PR: open the link above or run [bold]gh pr diff --web[/bold][/dim]"
                )
                console.print(
                    "[dim]  2. If OK: [bold]merge[/bold] the PR, then run [bold]cafe close[/bold][/dim]"
                )
                console.print(
                    "[dim]  3. If issues found: add comments and submit review, then run [bold]cafe develop --auto[/bold] (or [bold]cafe make[/bold])[/dim]"
                )

                # Automatically open PR diff in browser
                try:
                    subprocess.run(["gh", "pr", "diff", "--web"], capture_output=True, check=False, timeout=5)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass  # Silently ignore timeout or gh not found
                except Exception:
                    pass  # Silently ignore any other errors
        else:
            console.print()
            console.print(f"[bold red]❌ PR phase failed: {result.message}[/bold red]")
            raise typer.Exit(1)

    except Exception as e:
        _handle_phase_exception(e, "pr", auto=auto)


@app.command()
def config(
    action: Optional[str] = typer.Argument(
        None, help="Action: set, get, edit, reset, or config key"
    ),
    key: Optional[str] = typer.Argument(None, help="Configuration key"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
) -> None:
    """Manage CAFE configuration.

    \b
    Examples:
        cafe config
        cafe config set pm gemini
        cafe config set pm.cli gemini
        cafe config set agents.pm.cli gemini
        cafe config get pm
        cafe config get agents.pm.cli
        cafe config edit
        cafe config reset
    """
    config_manager = ConfigManager()
    import os
    import subprocess

    # No arguments: show all config
    if not action:
        loaded_config = config_manager.load_config()
        console.print("[bold cyan]Current Configuration:[/bold cyan]")
        console.print(yaml.dump(loaded_config, default_flow_style=False, allow_unicode=True))
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

        # Check if config file exists
        if not config_file.exists():
            console.print("[red]Error: Configuration file not found.[/red]")
            console.print("[yellow]Please run 'cafe init' first to initialize CAFE.[/yellow]")
            raise typer.Exit(1)

        # Use EDITOR env var, or fallback to vim
        editor = os.environ.get("EDITOR", "vim")

        try:
            subprocess.run([editor, str(config_file)], check=True)
            console.print(f"[green]✓ Config file edited: {config_file}[/green]")
        except subprocess.CalledProcessError:
            console.print("[red]Error: Failed to edit config[/red]")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print(f"[red]Error: Editor '{editor}' not found[/red]")
            console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
            raise typer.Exit(1)

    elif action == "reset":
        try:
            confirm = prompt_confirm("Reset configuration to defaults?", default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        if confirm:
            config_manager.reset()
            console.print("[green]✓ Configuration reset to defaults[/green]")
        else:
            console.print("[dim]Cancelled[/dim]")

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
        console.print("Run 'cafe prepare' to create your first issue")
        return

    # Get all issue directories
    issues = [d for d in issues_dir.iterdir() if d.is_dir()]

    if not issues:
        console.print("[yellow]No issues found[/yellow]")
        console.print("Run 'cafe prepare' to create your first issue")
        return

    # Create table
    table = Table(title="CAFE Issues", show_header=True, header_style="bold cyan")
    table.add_column("Issue Name", style="green")
    table.add_column("Phases", style="dim")
    table.add_column("Worktree", style="dim")
    table.add_column("Modified", style="dim")

    for issue in sorted(issues, key=lambda x: x.stat().st_mtime, reverse=True):
        # Get worktree path from issue.yaml first
        worktree_path = "-"
        config_file = issue / "issue.yaml"
        if config_file.exists():
            try:
                import yaml

                with open(config_file, "r") as f:
                    config = yaml.safe_load(f)
                    if config and "worktree_path" in config:
                        worktree_path = config["worktree_path"]
            except Exception:
                # If read fails, keep default value "-"
                pass

        # Check which phases exist
        # If worktree_path exists, read phases from worktree location
        phases = []
        if worktree_path != "-":
            # Read phases from worktree/.cafe/issues/{issue_name}/
            worktree_issue_dir = Path(worktree_path) / ".cafe" / "issues" / issue.name
            if worktree_issue_dir.exists():
                for phase in ALL_PHASES:
                    phase_dir = worktree_issue_dir / phase
                    if phase_dir.exists():
                        phases.append(phase)
            # If worktree issue dir doesn't exist, fall back to current location
            if not phases:
                for phase in ALL_PHASES:
                    phase_dir = issue / phase
                    if phase_dir.exists():
                        phases.append(phase)
        else:
            # No worktree, read phases from current location
            for phase in ALL_PHASES:
                phase_dir = issue / phase
                if phase_dir.exists():
                    phases.append(phase)

        phases_str = ", ".join(phases) if phases else "empty"

        # Get last modified time
        import datetime

        mtime = datetime.datetime.fromtimestamp(issue.stat().st_mtime)
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")

        table.add_row(issue.name, phases_str, worktree_path, mtime_str)

    console.print(table)
    console.print(f"\n[dim]Total: {len(issues)} issue(s)[/dim]")


@app.command(name="rm")
def remove_issue(
    issue_names: list[str] = typer.Argument(
        ..., help="Names of the issues to delete (supports wildcards like 'test-*')"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more issues and all their data."""
    import fnmatch
    import shutil

    # Expand wildcards
    issues_dir = Path(".cafe/issues")
    expanded_issues = []
    for pattern in issue_names:
        if "*" in pattern or "?" in pattern:
            # Wildcard pattern - find matching issues
            if not issues_dir.exists():
                continue
            matches = [
                d.name
                for d in issues_dir.iterdir()
                if d.is_dir() and fnmatch.fnmatch(d.name, pattern)
            ]
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

        try:
            confirm = prompt_confirm(f"Are you sure you want to delete {len(existing_issues)} issue(s)?", default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

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
        console.print(
            f"\n[green]{success_count}/{len(existing_issues)} issue(s) deleted successfully[/green]"
        )

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

    \b
    Actions:
        add  - Add a new template from a file
        ls   - List all available templates
        rm   - Remove a template
        cat  - View template content
        edit - Edit a template with $EDITOR

    \b
    Examples:
        cafe template add path/to/template.md my-template
        cafe template ls
        cafe template cat my-template
        cafe template edit my-template
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
            import os
            import subprocess

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


@app.command()
def make(
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """🚀 Check environment and execute complete development workflow.

    \b
    This command will:
    1. Check if all configured agent CLI tools are installed
    2. If environment check passes, execute `cafe spec --auto` to start automated workflow

    Please run `cafe prepare` first to initialize issue environment.

    \b
    Examples:
        cafe make
        cafe make --config /path/to/config.yaml
    """
    # Load configuration
    config_manager = ConfigManager(Path(config_file).parent)
    config_manager.load_config()

    # Check if all agent CLIs are available
    missing_clis = _check_agent_clis_available(config_manager)

    if missing_clis:
        console.print("[red]Error: The following agent CLI tools are not installed:[/red]")
        console.print()
        for cli in missing_clis:
            console.print(f"  [red]✗[/red] {cli}")
        console.print()
        console.print(
            "[yellow]Please install the missing tools before running 'cafe make'.[/yellow]"
        )
        console.print()
        console.print("[dim]Installation guides:[/dim]")
        console.print("[dim]  • claude: https://github.com/anthropics/anthropic-cli[/dim]")
        console.print("[dim]  • gemini: https://github.com/google-gemini/gemini-cli[/dim]")
        console.print("[dim]  • cursor-agent: https://cursor.com/docs/cli[/dim]")
        console.print(
            "[dim]  • copilot: https://docs.github.com/en/copilot/using-github-copilot/using-github-copilot-in-the-command-line[/dim]"
        )
        raise typer.Exit(1)

    # All CLIs available, execute cafe spec --auto
    console.print("[green]✓ All agent CLI tools are installed[/green]")
    console.print()
    console.print("[bold cyan]🚀 Starting automated workflow...[/bold cyan]")
    console.print()

    # Build command
    cmd = [sys.executable, "-m", "cafe.ui.cli", "spec", "--auto"]

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            # Error already printed by spec phase command, just exit
            raise typer.Exit(result.returncode)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error executing spec phase: {e}[/red]")
        raise typer.Exit(1)


# Agent management commands (similar to template commands)
agent_app = typer.Typer(help="Manage agents")
app.add_typer(agent_app, name="agent")


@agent_app.command(name="ls")
def agent_ls() -> None:
    """List all available agents."""
    from pathlib import Path
    from rich.table import Table

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
                import yaml
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
    from pathlib import Path

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
    from pathlib import Path
    import os

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
    import tempfile

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
    from pathlib import Path
    import os

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


@app.command()
def show(
    phase_name: str = typer.Argument(
        ...,
        help="Phase name (spec, plan, develop, review, pr)"
    ),
    content_type: Optional[str] = typer.Argument(
        None,
        help="Content type (default: output)"
    ),
    iteration: int = typer.Option(
        0,
        "--iteration", "-i",
        help="Iteration number (positive, 0=latest, negative=relative index)"
    ),
) -> None:
    """Display iteration file contents.

    Shows the content of files from different phases and iterations.

    \b
    Examples:
        cafe show spec
        cafe show spec context
        cafe show spec output -i 2
        cafe show spec context -i -1
        cafe show plan status -i -2
    """
    # Validate phase name
    if phase_name not in VALID_PHASES:
        console.print(f"[red]Error: Invalid phase '{phase_name}'[/red]")
        console.print(f"[dim]Valid phases: {', '.join(VALID_PHASES)}[/dim]")
        raise typer.Exit(1)

    # Set default content type
    if content_type is None:
        content_type = "output"

    # Validate content type
    if content_type not in VALID_CONTENT_TYPES:
        console.print(f"[red]Error: Invalid content type '{content_type}'[/red]")
        console.print(f"[dim]Valid types: {', '.join(VALID_CONTENT_TYPES)}[/dim]")
        raise typer.Exit(1)

    # Get current branch name (issue_name)
    try:
        git_ops = GitOperations()
        issue_name = git_ops.get_current_branch()
    except Exception as e:
        console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
        raise typer.Exit(1)

    # Build phase directory path
    cafe_dir = Path.cwd() / ".cafe"
    phase_dir = cafe_dir / "issues" / issue_name / phase_name

    # Check if phase directory exists
    if not phase_dir.exists():
        console.print(f"[red]Error: Phase directory not found: {phase_dir}[/red]")
        console.print(f"[dim]The '{phase_name}' phase has not been executed yet[/dim]")
        raise typer.Exit(1)

    try:
        # Resolve iteration number (only for non-status/iterations files)
        if content_type not in ["status", "iterations"]:
            resolved_iteration = _resolve_iteration_number(phase_dir, iteration, content_type)
            # Get file path
            file_path = _get_show_file_path(phase_dir, resolved_iteration, content_type)
        else:
            # status and iterations don't need iteration number
            file_path = _get_show_file_path(phase_dir, 0, content_type)
            resolved_iteration = None

        # Check if file exists
        if not file_path.exists():
            console.print(f"[red]Error: File not found: {file_path}[/red]")
            if resolved_iteration is not None:
                console.print(f"[dim]File '{content_type}' does not exist in iteration {resolved_iteration}[/dim]")
            raise typer.Exit(1)

        # Read and display file content
        try:
            content = file_path.read_text(encoding="utf-8")

            # Use syntax highlighting for JSON files
            if file_path.suffix == ".json":
                try:
                    import json
                    json_data = json.loads(content)
                    console.print_json(data=json_data)
                except json.JSONDecodeError:
                    # If JSON parsing fails, output raw content
                    console.print(content)
            else:
                # Output other files directly
                console.print(content)

        except UnicodeDecodeError:
            console.print(f"[red]Error: Failed to read file (not UTF-8 encoded)[/red]")
            raise typer.Exit(1)

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@app.command(name="chat", context_settings={"allow_extra_args": False, "ignore_unknown_options": False})
def chat_with_agent(
    ctx: typer.Context,
    role: str = typer.Argument(..., help="Role: pm, developer, or reviewer"),
) -> None:
    """Open interactive chat with specified role Agent

    This command allows you to quickly interact with an Agent of specified role,
    without manually looking up and entering session id.
    The system automatically infers the issue from current branch and loads corresponding session.

    \b
    Supported roles:
    - pm: Product Manager Agent
    - developer: Developer Agent
    - reviewer: Reviewer Agent

    \b
    Examples:
        cafe chat pm
        cafe chat developer
        cafe chat reviewer
    """
    # 1. Validate role parameter
    valid_roles = ["pm", "developer", "reviewer"]
    if role not in valid_roles:
        console.print(f"[red]Error: Invalid role '{role}'. Must be one of: {', '.join(valid_roles)}[/red]")
        raise typer.Exit(1)

    # 2. Get current branch as issue name
    issue_name = _get_and_validate_branch(ctx, "chat")

    # 3. Load configuration
    config_manager = ConfigManager()

    # 4. Get agent config for corresponding role from config
    agent_config = config_manager.get(f"agents.{role}", None)

    if agent_config is None:
        console.print(f"[red]Error: No agent configured for role '{role}'.[/red]")
        console.print(f"[yellow]Please configure an agent for '{role}' in .cafe/config.yaml[/yellow]")
        raise typer.Exit(1)

    agent_name = agent_config.get("name")
    agent_cli = agent_config.get("cli")
    agent_model = agent_config.get("model")

    if not agent_name or not agent_cli:
        console.print(f"[red]Error: Invalid agent configuration for role '{role}'.[/red]")
        console.print(f"[yellow]Please ensure 'name' and 'cli' are configured in .cafe/config.yaml[/yellow]")
        raise typer.Exit(1)

    # 5. Set up agent manager (automatically loads session)
    agent_manager = _setup_agents(config_manager, issue_name=issue_name)

    # 6. Get executor for this agent
    try:
        agent_executor = agent_manager.get_agent(agent_name)
    except Exception as e:
        console.print(f"[red]Error: Failed to get agent '{agent_name}': {e}[/red]")
        raise typer.Exit(1)

    # 7. Get session ID (if exists)
    session_id = agent_executor.config.session_id

    # 8. Build and execute interactive CLI command
    console.print(f"[bold blue]Opening interactive CLI for {role} ({agent_name})...[/bold blue]")
    console.print(f"[dim]Issue: {issue_name}[/dim]")
    console.print(f"[dim]CLI: {agent_cli}[/dim]")
    if session_id:
        console.print(f"[dim]Session: {session_id}[/dim]")
    console.print()

    # Build CLI command
    cli_command = [agent_cli]

    # Add session parameter (if exists)
    if session_id:
        if agent_cli == "claude":
            cli_command.extend(["--resume", session_id])
        elif agent_cli == "copilot":
            cli_command.extend(["--session", session_id])
        elif agent_cli == "gemini":
            cli_command.extend(["--session-id", session_id])
        elif agent_cli == "cursor-agent":
            cli_command.extend(["--session", session_id])

    # Add model parameter (if exists)
    if agent_model:
        if agent_cli == "claude":
            cli_command.extend(["--model", agent_model])
        elif agent_cli == "copilot":
            cli_command.extend(["--model", agent_model])
        elif agent_cli == "gemini":
            cli_command.extend(["--model", agent_model])

    # Execute interactive CLI
    try:
        result = subprocess.run(cli_command)
    except FileNotFoundError:
        console.print(f"[red]Error: CLI tool '{agent_cli}' not found.[/red]")
        console.print(f"[yellow]Please install '{agent_cli}' CLI tool first.[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: Failed to execute CLI: {e}[/red]")
        raise typer.Exit(1)

    # Exit normally, return CLI tool's exit code
    raise typer.Exit(result.returncode)


@app.command()
def test() -> None:
    """🧪 Simulate agent execution test (for reproducing contamination issues).

    Execute scripts/simulate_agent_test.sh to simulate agent behavior in worktree.
    This command will:
    1. Execute tests
    2. Attempt commit (triggers pre-commit hook)
    3. Check for contaminated commits
    """
    import subprocess
    from pathlib import Path

    # Find script file
    script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "simulate_agent_test.sh"

    if not script_path.exists():
        console.print(f"[red]Error: Script not found at {script_path}[/red]")
        raise typer.Exit(1)

    console.print("[bold blue]🤖 Simulating Agent execution test...[/bold blue]")
    console.print(f"[dim]Script: {script_path}[/dim]")
    console.print("")

    try:
        # Execute script (no cwd specified, use current directory)
        result = subprocess.run(
            ["bash", str(script_path)],
            # No cwd specified, execute from current directory (for testing worktree)
            # No env specified, inherit current environment (simulate agent behavior)
        )

        if result.returncode == 0:
            console.print("")
            console.print("[green]✅ Test completed, no contamination detected[/green]")
        else:
            console.print("")
            console.print("[red]❌ Contamination detected or test failed![/red]")
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
    import importlib.metadata
    from pathlib import Path

    # Find pyproject.toml (should be in project root)
    # Try from current file location
    project_root = Path(__file__).parent.parent.parent.parent
    pyproject_file = project_root / "pyproject.toml"

    if not pyproject_file.exists():
        # If not found, skip check (might be installed as package)
        return

    try:
        from packaging.requirements import Requirement

        with open(pyproject_file, "rb") as f:
            pyproject = tomllib.load(f)

        dependencies = pyproject.get("project", {}).get("dependencies", [])
        missing = []

        for dep in dependencies:
            # Parse dependency using packaging library
            req = Requirement(dep)
            package_name = req.name

            # Skip if the dependency's environment marker doesn't apply to current environment
            if req.marker and not req.marker.evaluate():
                continue

            try:
                importlib.metadata.version(package_name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(package_name)

        if missing:
            console.print(f"[red]Error: Missing required dependencies: {', '.join(missing)}[/red]")
            console.print("[yellow]Please run: pip install -e .[/yellow]")
            sys.exit(1)

    except Exception:
        # If check fails, continue anyway
        pass


@app.command()
def summary() -> None:
    """Display a comprehensive timeline of all workflow phases and iterations.

    Shows the start time, end time, duration, and current status for each phase
    and iteration in the current issue's workflow.

    \b
    Examples:
        cafe summary
    """
    from cafe.services.summary_service import SummaryService
    from cafe.services.timeline_builder import TimelineBuilder
    from cafe.services.summary_display import SummaryDisplay

    try:
        # Get current issue from git context
        service = SummaryService()
        issue_name = service.get_current_issue()

        # Load phase and iteration data
        phase_statuses = {}
        iteration_data = {}

        for phase_name in ["spec", "plan", "develop", "review", "pr"]:
            phase_status = service.load_phase_status(issue_name, phase_name)
            if phase_status:
                phase_statuses[phase_name] = phase_status

            iterations = service.load_iteration_statuses(issue_name, phase_name)
            if iterations:
                iteration_data[phase_name] = iterations

        # Build timeline
        builder = TimelineBuilder(issue_name)
        entries = builder.build_timeline_entries(phase_statuses, iteration_data)

        # Display timeline (keep times in UTC as they appear in data files)
        display = SummaryDisplay()
        output = display.render_vertical_timeline(entries)

        console.print(output)

    except Exception as e:
        console.print(f"[red]Error: Failed to display summary: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    main()
