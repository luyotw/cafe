"""初始化命令 - init, prepare, close"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from cafe.core.git import GitOperations
from cafe.ui import init_helpers
from cafe.ui.init_helpers import check_available_clis, list_available_agents
from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_text
from cafe.utils.config import ConfigManager
from cafe.utils.git_utils import is_branch_initialized
from cafe.utils.github import GitHubError, GitHubOps

console = Console()

app = typer.Typer()


@app.command()
def init() -> None:
    """Initialize CAFE configuration for the project.

    Creates .cafe/config.yaml and copies default agents and templates.
    """
    try:
        config_manager = ConfigManager()

        # 1. Check if config already exists
        if config_manager.config_file.exists():
            console.print("[yellow]Configuration already exists. To modify, use the `cafe config` commands.[/yellow]")
            console.print("[yellow]See `cafe config --help` for details.[/yellow]")
            raise typer.Exit(1)

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
                message=f"Please select an AI agent for {role_display}",
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
                message=f"Please select an agent for {role_display}",
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


def _ensure_default_content(cafe_dir: Path) -> None:
    """Ensure .cafe/templates and .cafe/agents exist, copy from package data directory if not.

    Args:
        cafe_dir: Path to .cafe directory
    """
    # Check if agents and templates directories exist
    agents_dir = cafe_dir / "agents"
    templates_dir = cafe_dir / "templates"

    if not agents_dir.exists() or not templates_dir.exists():
        try:
            # Get package data directory
            import cafe

            package_dir = Path(cafe.__file__).parent
            agents_source = package_dir / "data" / "agents"
            templates_source = package_dir / "data" / "templates"

            console.print("[cyan]Setting up default content...[/cyan]")

            if not agents_dir.exists():
                init_helpers.copy_data_directory(str(agents_source), str(agents_dir))
                console.print("[green]✓ Copied default agents[/green]")

            if not templates_dir.exists():
                init_helpers.copy_data_directory(str(templates_source), str(templates_dir))
                console.print("[green]✓ Copied default templates[/green]")

        except Exception as e:
            console.print(f"[yellow]Warning: Could not copy default content - {e}[/yellow]")


@app.command()
def prepare(
    issue_name: Optional[str] = typer.Argument(None, help="Issue name or number (e.g., 'issue123' or '123')"),
    base_branch: str = typer.Option("develop", "--base", "-b", help="Base branch to create from"),
    worktree: bool = typer.Option(False, "--worktree", "-w", help="Use Git worktree instead of branch checkout"),
    worktree_path: Optional[str] = typer.Option(None, "--worktree-path", help="Path for Git worktree (only with --worktree)"),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Non-interactive mode, requires issue name"),
    input_method: Optional[str] = typer.Option(None, "--input", "-i", help="Input method: local or github (non-interactive mode only)"),
    issue_id: Optional[str] = typer.Option(None, "--issue-id", help="GitHub issue ID (required when using github input method)"),
    skip_uncommitted_check: bool = typer.Option(False, "--skip-uncommitted-check", help="Skip check for uncommitted changes"),
) -> None:
    """Prepare working environment for a new issue.

    Creates a new branch or worktree and sets up issue directory structure.

    Examples:
        cafe prepare                              # Interactive mode
        cafe prepare issue123                     # Create branch issue123
        cafe prepare 123                          # Create branch issue123
        cafe prepare --worktree                   # Interactive mode with worktree
        cafe prepare --worktree --worktree-path ../cafe-issue123  # Specify worktree path
        cafe prepare --no-interactive --input local issue123      # Non-interactive local mode
        cafe prepare --no-interactive --input github --issue-id 42 # Non-interactive GitHub mode
    """
    try:
        git_ops = GitOperations()

        # Non-interactive mode validation
        if no_interactive:
            if not issue_name:
                console.print("[red]Error: Issue name is required in non-interactive mode[/red]")
                raise typer.Exit(1)

            if not input_method:
                console.print("[red]Error: --input is required in non-interactive mode (local or github)[/red]")
                raise typer.Exit(1)

            if input_method not in ["local", "github"]:
                console.print("[red]Error: --input must be either 'local' or 'github'[/red]")
                raise typer.Exit(1)

            if input_method == "github" and not issue_id:
                console.print("[red]Error: --issue-id is required when using github input method[/red]")
                raise typer.Exit(1)

        # Interactive mode issue name input
        if not no_interactive and not issue_name:
            issue_name_input = prompt_text(
                message="Enter issue name or number (e.g., 'issue123' or '123'):",
                default="",
            )
            if not issue_name_input:
                console.print("[yellow]Issue name is required[/yellow]")
                raise typer.Exit(1)
            issue_name = issue_name_input

        # Normalize issue name (add 'issue' prefix if only number provided)
        if issue_name.isdigit():
            issue_name = f"issue{issue_name}"

        # Check if we're in a git repository
        if not git_ops.is_git_repo():
            console.print("[red]Error: Not a git repository. Please run this command from the project root.[/red]")
            raise typer.Exit(1)

        # Check for uncommitted changes (unless skipped)
        if not skip_uncommitted_check and git_ops.has_uncommitted_changes():
            console.print("[yellow]⚠️  You have uncommitted changes in your working directory.[/yellow]")
            should_continue = prompt_confirm("Do you want to continue anyway?", default=False)
            if not should_continue:
                console.print("[yellow]Cancelled. Please commit or stash your changes first.[/yellow]")
                raise typer.Exit(1)

        # Check if branch already exists
        if git_ops.branch_exists(issue_name):
            console.print(f"[yellow]Branch '{issue_name}' already exists.[/yellow]")
            console.print("[yellow]Please use a different issue name or delete the existing branch first.[/yellow]")
            raise typer.Exit(1)

        # Interactive worktree mode prompts
        if not no_interactive and not worktree:
            use_worktree = prompt_confirm("Do you want to use Git worktree?", default=False)
            if use_worktree:
                worktree = True

        # Prompt for worktree path if using worktree but path not provided
        if worktree and not worktree_path:
            # Suggest default path
            default_path = f"../.cafe/worktrees/{issue_name}"
            if not no_interactive:
                worktree_path_input = prompt_text(
                    message=f"Enter worktree path:",
                    default=default_path,
                )
                worktree_path = worktree_path_input if worktree_path_input else default_path
            else:
                # In non-interactive mode, use default path
                worktree_path = default_path

        # Prompt for PR auto-creation only in interactive mode
        pr_auto_create = None
        if not no_interactive and worktree:
            pr_auto_create = prompt_confirm(
                "Automatically create pull request when using 'cafe pr' command?",
                default=True
            )

        # Create branch or worktree
        if worktree:
            console.print(f"[cyan]Creating Git worktree at: {worktree_path}[/cyan]")
            git_ops.create_worktree(worktree_path, issue_name, base_branch)
            console.print(f"[green]✓ Created worktree '{issue_name}' from '{base_branch}'[/green]")

            # Change to worktree directory
            os.chdir(worktree_path)
            console.print(f"[green]✓ Changed to worktree directory: {worktree_path}[/green]")

            # Create .cafe directory structure in worktree (not symlink)
            cafe_dir = Path(worktree_path) / ".cafe"
            cafe_dir.mkdir(exist_ok=True)

            # Copy agents and templates to worktree
            _ensure_default_content(cafe_dir)

        else:
            console.print(f"[cyan]Creating branch: {issue_name}[/cyan]")
            git_ops.create_branch(issue_name, base_branch)
            console.print(f"[green]✓ Created branch '{issue_name}' from '{base_branch}'[/green]")

            # Checkout to new branch
            git_ops.checkout(issue_name)
            console.print(f"[green]✓ Checked out to branch '{issue_name}'[/green]")

        # Create issue directory structure
        cafe_dir = Path.cwd() / ".cafe"
        issue_dir = cafe_dir / "issues" / issue_name
        issue_dir.mkdir(parents=True, exist_ok=True)

        # Create phase directories
        for phase in ["spec", "plan", "develop", "review", "pr"]:
            (issue_dir / phase).mkdir(exist_ok=True)

        # Create config file for this issue
        config_file = issue_dir / "config.yaml"
        issue_config = {
            "issue_name": issue_name,
            "base_branch": base_branch,
        }

        if worktree:
            issue_config["worktree_path"] = worktree_path

        # Save PR auto-create configuration if in interactive mode
        if pr_auto_create is not None:
            issue_config["pr_auto_create"] = pr_auto_create

        with open(config_file, "w") as f:
            yaml.dump(issue_config, f, default_flow_style=False)

        console.print(f"[green]✓ Created issue directory structure at {issue_dir}[/green]")

        # Success message
        console.print("\n[bold green]✨ Environment prepared successfully![/bold green]\n")
        if worktree:
            console.print(f"Working directory: {worktree_path}")
        console.print(f"Branch: {issue_name}")
        console.print(f"Base branch: {base_branch}")
        console.print(f"Issue directory: {issue_dir}")
        console.print("\n[cyan]You can now start working on the issue with `cafe spec` command.[/cyan]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def _get_project_path() -> str:
    """Get the project path from git repository root.

    Returns:
        Project path (repository name)

    Raises:
        typer.Exit: If not in a git repository or cannot determine repository name
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        repo_root = result.stdout.strip()
        return Path(repo_root).name
    except subprocess.CalledProcessError:
        console.print("[red]Error: Not in a git repository[/red]")
        raise typer.Exit(1)


@app.command()
def close() -> None:
    """Close current issue and cleanup.

    Merges changes back to base branch and removes the issue branch or worktree.
    """
    try:
        git_ops = GitOperations()

        # Get current branch
        current_branch = git_ops.get_current_branch()

        # Check if this is an initialized branch
        if not is_branch_initialized(current_branch):
            console.print(
                f"[yellow]Current branch '{current_branch}' is not an initialized issue branch.[/yellow]"
            )
            console.print("[yellow]Use `cafe prepare` to initialize a new issue first.[/yellow]")
            raise typer.Exit(1)

        # Load issue config
        cafe_dir = Path.cwd() / ".cafe"
        config_file = cafe_dir / "issues" / current_branch / "config.yaml"

        base_branch = "develop"  # default
        worktree_path = None
        pr_auto_create = True  # default to True for backward compatibility

        if config_file.exists():
            with open(config_file) as f:
                issue_config = yaml.safe_load(f)
                base_branch = issue_config.get("base_branch", "develop")
                worktree_path = issue_config.get("worktree_path")
                pr_auto_create = issue_config.get("pr_auto_create", True)

        # Check if there's an open PR
        try:
            github_ops = GitHubOps()
            pr_state = github_ops.get_pr_state(current_branch)

            if pr_state in ["open", "draft"]:
                console.print(f"[yellow]⚠️  Pull request for branch '{current_branch}' is still {pr_state}.[/yellow]")
                console.print("[yellow]Please merge or close the pull request before closing the issue.[/yellow]")
                raise typer.Exit(1)
        except GitHubError:
            # If GitHub is not available or there's no PR, continue with close
            pass

        console.print(f"[cyan]Closing issue: {current_branch}[/cyan]")

        # Sync issue data back to repository root if using worktree
        if worktree_path:
            worktree_issue_dir = Path.cwd() / ".cafe" / "issues" / current_branch
            repo_root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            repo_root = Path(repo_root_result.stdout.strip())
            repo_root_issue_dir = repo_root / ".cafe" / "issues" / current_branch

            if worktree_issue_dir.exists():
                # Create parent directory if needed
                repo_root_issue_dir.parent.mkdir(parents=True, exist_ok=True)

                # Copy issue data back to repository root
                if repo_root_issue_dir.exists():
                    shutil.rmtree(repo_root_issue_dir)
                shutil.copytree(worktree_issue_dir, repo_root_issue_dir)
                console.print(f"[green]✓ Synced issue data to repository root[/green]")

        # Archive issue directory
        issue_dir = cafe_dir / "issues" / current_branch
        if issue_dir.exists():
            archive_dir = cafe_dir / "archive" / current_branch
            archive_dir.parent.mkdir(parents=True, exist_ok=True)

            if archive_dir.exists():
                shutil.rmtree(archive_dir)

            shutil.move(str(issue_dir), str(archive_dir))
            console.print(f"[green]✓ Archived issue directory to {archive_dir}[/green]")

        if worktree_path:
            # Worktree mode: Remove worktree
            console.print(f"[cyan]Removing worktree: {worktree_path}[/cyan]")

            # Remove worktree
            git_ops.remove_worktree(worktree_path)
            console.print(f"[green]✓ Removed worktree[/green]")

            # Delete branch
            git_ops.delete_branch(current_branch, force=True)
            console.print(f"[green]✓ Deleted branch '{current_branch}'[/green]")

            console.print("\n[bold green]✨ Issue closed successfully![/bold green]")
            console.print(f"[yellow]Note: You are still in the worktree directory: {Path.cwd()}[/yellow]")
            console.print("[yellow]Please manually navigate back to your repository root.[/yellow]")

        else:
            # Normal branch mode
            # Determine the merge/pull strategy based on pr_auto_create
            if pr_auto_create:
                # When PR was auto-created (or would be), use git pull to sync
                console.print(f"[cyan]Syncing with remote {base_branch}...[/cyan]")
                git_ops.checkout(base_branch)
                git_ops.pull(base_branch)
            else:
                # When PR was not auto-created, use git merge for local-only changes
                console.print(f"[cyan]Merging {current_branch} into {base_branch}...[/cyan]")
                git_ops.checkout(base_branch)
                git_ops.merge(current_branch)

            console.print(f"[green]✓ Switched to {base_branch}[/green]")

            # Delete the issue branch
            git_ops.delete_branch(current_branch)
            console.print(f"[green]✓ Deleted branch '{current_branch}'[/green]")

            console.print("\n[bold green]✨ Issue closed successfully![/bold green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
