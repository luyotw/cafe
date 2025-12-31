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
from cafe.phases.develop_phase import DevelopPhase, develop as develop_command
from cafe.phases.plan_phase import PlanPhase, plan as plan_command
from cafe.phases.pr_phase import PRPhase
from cafe.phases.review_phase import ReviewPhase
from cafe.phases.spec_phase import SpecPhase, spec as spec_command
from cafe.templates.manager import TemplateManager
from cafe.ui import init_helpers
from cafe.ui.commands import config_commands, init_commands, issue_commands, resource_commands, show_commands
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


# Add "dev" as an alias for "develop"
# Use the same function with different name to ensure parameter sync
app.command(name="dev", hidden=False)(develop_command)


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
app.command()(config_commands.config)
app.command()(init_commands.init)
app.command()(init_commands.prepare)
app.command()(init_commands.close)
app.command(name="ls")(issue_commands.list_issues)
app.command(name="rm")(issue_commands.remove_issue)
app.command()(resource_commands.template)
app.add_typer(resource_commands.agent_app, name="agent")
app.command()(show_commands.show)
app.command()(spec_command)
app.command()(plan_command)
app.command()(develop_command)


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
