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
from cafe.phases.pr_phase import PRPhase, pr as pr_command
from cafe.phases.review_phase import ReviewPhase, review as review_command
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


# Register commands from modules - using decorator pattern in modules
# Extract commands from module apps and register to main app
def _register_commands_from_app(source_app: typer.Typer) -> None:
    """Helper to register commands from a Typer app to the main app."""
    for cmd_info in source_app.registered_commands:
        # Re-register the command callback to the main app with same metadata
        cmd_decorator = app.command(
            name=cmd_info.name,
            help=cmd_info.help,
            epilog=cmd_info.epilog,
            short_help=cmd_info.short_help,
            hidden=cmd_info.hidden,
            deprecated=cmd_info.deprecated,
        )
        cmd_decorator(cmd_info.callback)

_register_commands_from_app(config_commands.app)
_register_commands_from_app(init_commands.app)
_register_commands_from_app(issue_commands.app)
_register_commands_from_app(show_commands.app)
_register_commands_from_app(resource_commands.app)

# Register resource agent subgroup
app.add_typer(resource_commands.agent_app, name="agent")

# Register phase commands
app.command()(spec_command)
app.command()(plan_command)
app.command()(develop_command)
app.command()(review_command)
app.command()(pr_command)


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
