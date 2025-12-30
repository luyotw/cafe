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
from cafe.ui.commands import init_commands, show_commands
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
def version() -> None:
    """Show CAFE version."""
    console.print("CAFE version 0.1.0")


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
def restore(
    issue_name: str = typer.Argument(..., help="Issue name to restore")
) -> None:
    """Restore archived issue from backup.

    This command restores an archived issue from ~/.cafe/projects/<project-path>/archived/<issue-name>/
    back to .cafe/issues/<issue-name>/.

    It performs the following checks:
    1. Verifies backup exists
    2. Checks current branch matches the issue's feature_branch
    3. For worktree mode, checks current directory matches worktree_path
    4. Prompts user for confirmation
    5. Restores all files from backup

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

        # 4. Initialize Git operations and check current branch
        try:
            git_ops = GitOperations()
        except Exception as e:
            console.print(f"[red]Error: Not a git repository. {e}[/red]")
            raise typer.Exit(1)

        current_branch = git_ops.get_current_branch()
        if not current_branch:
            console.print("[red]Error: Not on a valid branch (detached HEAD?).[/red]")
            raise typer.Exit(1)

        # 5. Check if current branch matches feature_branch
        if current_branch != feature_branch:
            console.print()
            console.print("[red]❌ Error: Branch mismatch[/red]")
            console.print(f"   Current branch: {current_branch}")
            console.print(f"   Expected branch (from issue.yaml): {feature_branch}")
            console.print()
            console.print("[yellow]Please switch to the correct branch first:[/yellow]")
            console.print(f"   [bold]git checkout {feature_branch}[/bold]")
            console.print()
            raise typer.Exit(1)

        # 6. For worktree mode, check if we're in the correct worktree directory
        # 檢查方式：比較當前目錄是否在預期的 worktree 路徑下
        if worktree_path:
            current_path = Path.cwd().resolve()
            expected_worktree = Path(worktree_path).resolve()

            # 檢查當前路徑是否在 worktree 路徑下
            # 使用 is_relative_to() 或手動檢查路徑前綴
            try:
                # Python 3.9+ 有 is_relative_to()
                is_in_worktree = current_path.is_relative_to(expected_worktree)
            except AttributeError:
                # Python 3.8 fallback: 檢查是否有共同前綴
                try:
                    current_path.relative_to(expected_worktree)
                    is_in_worktree = True
                except ValueError:
                    is_in_worktree = False

            if not is_in_worktree:
                console.print()
                console.print("[red]❌ Error: Worktree path mismatch[/red]")
                console.print(f"   Current path: {Path.cwd()}")
                console.print(f"   Expected worktree path (from issue.yaml): {worktree_path}")
                console.print()
                console.print("[yellow]Please change to the correct worktree directory:[/yellow]")
                console.print(f"   [bold]cd {worktree_path}[/bold]")
                console.print()
                raise typer.Exit(1)

        # 7. Prompt user for confirmation
        console.print("[yellow]⚠️  Warning: This will restore the issue from backup.[/yellow]")
        console.print("[yellow]   Any current changes in .cafe/issues/{} will be overwritten.[/yellow]".format(issue_name))
        console.print()

        # 使用 typer.confirm 進行確認
        confirmed = typer.confirm("Do you want to continue?", default=False)
        if not confirmed:
            console.print()
            console.print("[yellow]Restore cancelled.[/yellow]")
            console.print()
            raise typer.Exit(1)

        # 8. Perform the restore operation
        console.print()
        console.print("[dim]Restoring issue data...[/dim]")

        # 目標路徑
        issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name

        # 如果目標路徑已存在，先刪除
        if issue_dir.exists():
            console.print(f"[dim]Removing existing issue directory...[/dim]")
            shutil.rmtree(issue_dir)

        # 從備份複製資料
        console.print(f"[dim]Copying data from backup...[/dim]")
        shutil.copytree(archive_path, issue_dir)

        # 9. Display success message
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

    Examples:
        # Generate spec through conversation (uses current branch)
        cafe spec

        # Auto mode: automatically continue iterations until CAFE_CONFIRMED
        cafe spec --auto

        # Create new GitHub issue with spec
        cafe spec -m github

        # Update existing GitHub issue
        cafe spec -m github -i 123

        # Use custom PM agent
        cafe spec --pm CustomPM

        # Specify rigor level
        cafe spec --rigor low

        # Edit latest spec file
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

    Examples:
        # Analyze spec and create plan (uses current branch)
        cafe plan

        # Auto mode: automatically continue iterations until CAFE_CONFIRMED
        cafe plan --auto

        # Analyze GitHub issue and create plan
        cafe plan -m github -i 123

        # Use custom developer agent
        cafe plan --dev CustomDev

        # Edit latest plan file
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

    Examples:
        # Execute development (uses current branch)
        cafe develop

        # Use custom developer agent
        cafe develop --dev CustomDev

        # Fetch unresolved PR comments to guide development
        cafe develop --pr-number 123

        # Non-interactive mode with permission approval
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

    Examples:
        # Review entire feature branch (uses current branch)
        cafe review

        # Review specific commit
        cafe review --commit abc123

        # Use custom reviewer agent
        cafe review --reviewer CustomReviewer

        # Force re-review even if already completed
        cafe review --force

        # Edit latest review file
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
                    # Read max_review_iterations from issue config
                    import yaml

                    issue_config_file = Path(f".cafe/issues/{issue_name}/issue.yaml")
                    max_iterations = 5  # Default
                    if issue_config_file.exists():
                        with open(issue_config_file, "r") as f:
                            issue_config = yaml.safe_load(f)
                            max_iterations = issue_config.get("auto", {}).get(
                                "max_review_iterations", 5
                            )

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
                        console.print(
                            f"[dim]  • Or modify .cafe/issues/{issue_name}/issue.yaml[/dim]"
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
        # Check which phases exist
        phases = []
        for phase in ["spec", "plan", "develop", "review", "pr"]:
            phase_dir = issue / phase
            if phase_dir.exists():
                phases.append(phase)

        phases_str = ", ".join(phases) if phases else "empty"

        # Get worktree path from issue.yaml
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
                # 若讀取失敗，保持預設值 "-"
                pass

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

    This command will:
    1. Check if all configured agent CLI tools are installed
    2. If environment check passes, execute `cafe spec --auto` to start automated workflow

    Please run `cafe prepare` first to initialize issue environment.

    Examples:
        # Execute with default configuration
        cafe make

        # Use custom config file
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


# Register commands from modules
app.command()(init_commands.init)
app.command()(init_commands.prepare)
app.command()(init_commands.close)
app.command()(show_commands.show)


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
            console.print("[yellow]Please run: pip install -e .[/yellow]")
            sys.exit(1)

    except Exception:
        # If check fails, continue anyway
        pass


if __name__ == "__main__":
    main()
