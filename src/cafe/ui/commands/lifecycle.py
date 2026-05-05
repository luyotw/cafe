"""Lifecycle command implementations extracted from cli.py."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, List, Optional

import typer
import yaml

VALID_PHASES = ["spec", "plan", "develop", "review", "pr"]

console: Any = None
prompt_text: Any = None
prompt_list: Any = None
prompt_confirm: Any = None
GitOperations: Any = None
GitHubOps: Any = None
GitHubError: Any = Exception
TemplateManager: Any = None
Display: Any = None
ConfigManager: Any = None
prompt_for_input_method: Any = None
prompt_for_rigor: Any = None
select_template: Any = None
_ensure_default_content: Any = None
_resolve_iteration_index: Any = None


def set_runtime(
    *,
    console_obj: Any,
    prompt_text_fn: Any,
    prompt_list_fn: Any,
    prompt_confirm_fn: Any,
    git_operations_cls: Any,
    github_ops_cls: Any,
    github_error_cls: Any,
    template_manager_cls: Any,
    display_cls: Any,
    config_manager_cls: Any,
    prompt_for_input_method_fn: Any,
    prompt_for_rigor_fn: Any,
    select_template_fn: Any,
    ensure_default_content_fn: Any,
    resolve_iteration_index_fn: Any,
    valid_phases: List[str],
    path_cls: Any,
) -> None:
    """Update runtime-injected dependencies from cafe.ui.cli."""
    global console, prompt_text, prompt_list, prompt_confirm
    global GitOperations, GitHubOps, GitHubError
    global TemplateManager, Display, ConfigManager
    global prompt_for_input_method, prompt_for_rigor, select_template
    global _ensure_default_content, _resolve_iteration_index, VALID_PHASES, Path

    console = console_obj
    prompt_text = prompt_text_fn
    prompt_list = prompt_list_fn
    prompt_confirm = prompt_confirm_fn
    GitOperations = git_operations_cls
    GitHubOps = github_ops_cls
    GitHubError = github_error_cls
    TemplateManager = template_manager_cls
    Display = display_cls
    ConfigManager = config_manager_cls
    prompt_for_input_method = prompt_for_input_method_fn
    prompt_for_rigor = prompt_for_rigor_fn
    select_template = select_template_fn
    _ensure_default_content = ensure_default_content_fn
    _resolve_iteration_index = resolve_iteration_index_fn
    VALID_PHASES = list(valid_phases)
    Path = path_cls


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
    plan_template: Optional[str] = typer.Option(
        None,
        "--plan-template",
        help="Plan template name (default: default)",
    ),
    spec_template: Optional[str] = typer.Option(
        None,
        "--spec-template",
        help="Spec template name (default: auto)",
    ),
    auto_create_pr: bool = typer.Option(
        False,
        "--auto-create-pr/--no-auto-create-pr",
        help="Automatically create PR after development (default: False, GitHub repos only)",
    ),
    sync_spec_github: Optional[bool] = typer.Option(
        None,
        "--sync-spec-github/--no-sync-spec-github",
        help="Sync spec to GitHub issue when confirmed (default: auto-detect based on issue_id)",
    ),
    sync_plan_github: Optional[bool] = typer.Option(
        None,
        "--sync-plan-github/--no-sync-plan-github",
        help="Sync plan to GitHub issue when confirmed (default: auto-detect based on issue_id)",
    ),
    post_pr_todo_list: Optional[bool] = typer.Option(
        None,
        "--post-pr-todo-list/--no-post-pr-todo-list",
        help="Post organized PR comments as todo list to PR (default: True when auto-create PR is enabled)",
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
        cafe prepare fix-bug --no-interactive --input-method=manual --rigor=medium --plan-template=default
        cafe prepare issue-123 --no-interactive --input-method=github --issue-id=123 --rigor=high --spec-template=detailed
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

        # 1.1. Sync agents and templates at the beginning of prepare
        from cafe.ui.init_helpers import sync_agents, sync_templates
        cafe_dir = Path(".cafe")
        agent_success, agent_failed = sync_agents(cafe_dir)
        template_success, template_failed = sync_templates(cafe_dir)

        # Display sync summary
        if agent_success > 0 or template_success > 0:
            console.print(f"  [green]✓[/green] Updated .cafe directory with {agent_success} agent(s) and {template_success} template(s)")
        if agent_failed > 0 or template_failed > 0:
            console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {agent_failed + template_failed} file(s)")

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
            if plan_template is None:
                plan_template = "default"
            if spec_template is None:
                spec_template = "auto"

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

        # Validate base_branch != feature_branch
        if base_branch == issue_name:
            console.print(
                f"[bold red]Error: base_branch and feature_branch are both '{base_branch}'.[/bold red]"
            )
            console.print(
                "[yellow]This usually happens when you run 'cafe prepare' while already on the feature branch.[/yellow]"
            )
            console.print()
            console.print("[dim]To fix this, either:[/dim]")
            console.print(f"[dim]  1. Switch to the base branch first: git checkout main && cafe prepare {issue_name}[/dim]")
            console.print(f"[dim]  2. Specify the base branch explicitly: cafe prepare {issue_name} --base main[/dim]")
            raise typer.Exit(1)

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

        # 9.1. Validate plan template exists (only in non-interactive mode after templates are initialized)
        if not interactive and plan_template and plan_template != "auto":
            plan_template_manager = TemplateManager(template_type="plan")
            template_path = plan_template_manager.get_template_path(plan_template)
            if not template_path:
                console.print(f"[red]Error: Plan template '{plan_template}' not found[/red]")
                console.print()
                console.print("[yellow]Available plan templates:[/yellow]")
                available_templates = plan_template_manager.list_templates()
                if available_templates:
                    for name, source_type in available_templates:
                        source_label = " (system default)" if source_type == "system" else " (custom)"
                        console.print(f"  - {name}{source_label}")
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

            # Step 1: Prompt for input method and issue ID (only for GitHub repos)
            from cafe.utils.git_utils import is_github_repo

            if is_github_repo():
                input_method, issue_id = prompt_for_input_method(display, github_ops)
                spec_config["input_method"] = input_method
                if issue_id is not None:
                    spec_config["issue_id"] = str(issue_id)
            else:
                # Non-GitHub repo: use manual input only
                spec_config["input_method"] = "manual"
                issue_id = None

            # Step 2: Prompt for setup mode (after input method selection)
            console.print()
            setup_mode_choices = [
                "Quick setup (use recommended defaults)",
                "Custom configuration",
            ]
            setup_mode_choice = prompt_list(
                message="Choose setup mode:",
                choices=setup_mode_choices,
                default=setup_mode_choices[0],
            )
            
            use_quick_setup = setup_mode_choice.startswith("Quick setup")
            
            if use_quick_setup:
                # Quick setup: Apply default values without prompting
                # Default values
                spec_config["rigor"] = "medium"
                spec_config["template"] = "auto"
                plan_config["template"] = "auto"
                
                # Sync settings based on input method
                if issue_id is not None:
                    spec_config["sync_github"] = True  # GitHub Issue -> sync
                    plan_config["sync_github"] = True
                else:
                    spec_config["sync_github"] = False  # Manual input -> no sync
                    plan_config["sync_github"] = False
                
                # Auto create PR depends on whether it's a GitHub repo
                if is_github_repo():
                    pr_config["auto_create"] = True
                    # In Quick setup, auto_create is always True for GitHub repos,
                    # so always enable post_todo_list as well
                    pr_config["post_todo_list"] = True
                else:
                    pr_config["auto_create"] = False

                # Display default values summary
                console.print()
                console.print("[green]✓ Quick setup applied with recommended defaults:[/green]")
                console.print(f"  • Rigor level: {spec_config['rigor']}")
                console.print(f"  • Spec template: {spec_config['template']}")
                console.print(f"  • Plan template: {plan_config['template']}")
                console.print(f"  • Input method: {spec_config['input_method']}")
                if is_github_repo():
                    console.print(f"  • Sync to GitHub: {spec_config.get('sync_github', False)}")
                    console.print(f"  • Auto create PR: {pr_config.get('auto_create', False)}")
                    console.print(f"  • Post PR todo list: {pr_config.get('post_todo_list', False)}")
                console.print()
            else:
                # Custom configuration: Prompt for remaining settings
                
                # Prompt for sync settings (only when issue_id is present)
                if issue_id is not None:
                    console.print()
                    sync_spec = prompt_confirm(
                        "Sync spec to GitHub issue when confirmed?",
                        default=True
                    )
                    spec_config["sync_github"] = sync_spec

                    console.print()
                    sync_plan = prompt_confirm(
                        "Sync plan to GitHub issue when confirmed?",
                        default=True
                    )
                    plan_config["sync_github"] = sync_plan
                    console.print()

                # Prompt for rigor level
                rigor = prompt_for_rigor(display)
                spec_config["rigor"] = rigor

                # Prompt for spec template
                spec_template_manager = TemplateManager(template_type="spec")
                spec_templates_with_source = spec_template_manager.list_templates()
                spec_templates = [name for name, _ in spec_templates_with_source]

                if spec_templates:
                    console.print()
                    console.print("[bold cyan]Please select a spec template:[/bold cyan]")
                    spec_template_paths = {
                        name: spec_template_manager.get_template_path(name) for name in spec_templates
                    }
                    selected_spec_template = select_template(
                        spec_templates, spec_template_paths, spec_templates_with_source
                    )
                    if selected_spec_template:
                        spec_config["template"] = selected_spec_template

                # Prompt for plan template
                plan_template_manager = TemplateManager(template_type="plan")
                plan_templates_with_source = plan_template_manager.list_templates()
                plan_templates = [name for name, _ in plan_templates_with_source]

                if plan_templates:
                    console.print()
                    console.print("[bold cyan]Please select a plan template:[/bold cyan]")
                    plan_template_paths = {
                        name: plan_template_manager.get_template_path(name) for name in plan_templates
                    }
                    selected_plan_template = select_template(
                        plan_templates, plan_template_paths, plan_templates_with_source
                    )
                    if selected_plan_template:
                        plan_config["template"] = selected_plan_template
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

                # Prompt for post_todo_list only when auto_create is enabled
                if auto_create_pr_result:
                    console.print()
                    post_todo_list_result = prompt_confirm(
                        "Post organized PR comments as todo list to PR?",
                        default=True,
                    )
                    pr_config["post_todo_list"] = post_todo_list_result
        elif not interactive:
            # Explicit non-interactive mode (--no-interactive): use CLI parameters
            from cafe.utils.git_utils import is_github_repo

            # Spec config
            spec_config["input_method"] = input_method
            if input_method == "github" and issue_id is not None:
                spec_config["issue_id"] = str(issue_id)
            spec_config["rigor"] = rigor
            if spec_template:
                spec_config["template"] = spec_template
            if sync_spec_github is not None:
                spec_config["sync_github"] = sync_spec_github

            # Plan config
            if plan_template:
                plan_config["template"] = plan_template
            if sync_plan_github is not None:
                plan_config["sync_github"] = sync_plan_github

            # PR config (only for GitHub repos)
            if is_github_repo() and auto_create_pr:
                pr_config["auto_create"] = True
            if post_pr_todo_list is not None:
                pr_config["post_todo_list"] = post_pr_todo_list
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

        # Show hint about editing config or closing/reopening
        console.print("[cyan]💡 Tip:[/cyan]")
        console.print(f"  • Edit config: [bold]vim .cafe/issues/{issue_name}/issue.yaml[/bold]")
        console.print(f"  • Close and reopen: [bold]cafe close && cafe prepare[/bold]")
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


def _get_issue_archive_path(issue_name: str) -> Path:
    project_path = _get_project_path()
    home_dir = Path.home()
    return home_dir / ".cafe" / "projects" / project_path / "archived" / issue_name


def _backup_issue_directory(issue_dir: Path, issue_name: str) -> Path:
    archive_path = _get_issue_archive_path(issue_name)
    archive_base = archive_path.parent
    archive_base.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        shutil.rmtree(archive_path)
    shutil.copytree(issue_dir, archive_path)
    return archive_path


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
                console.print(f"  4. git branch -D {feature_branch}  # Force delete if needed")
                console.print(f"  5. cafe rm {issue_name}")
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
                console.print(f"  3. git branch -D {feature_branch}  # Force delete if needed")
                console.print(f"  4. cafe rm {issue_name}")
                console.print()
                raise typer.Exit(1)

            # Step 4: Move worktree config.yaml into issue dir before sync
            # so it gets archived and restore puts it back in issue dir (override)
            worktree_abs = Path(worktree_path).resolve()
            worktree_config = worktree_abs / ".cafe" / "config.yaml"
            worktree_issue_dir = worktree_abs / ".cafe" / "issues" / feature_branch
            if worktree_config.exists() and worktree_issue_dir.exists():
                shutil.move(str(worktree_config), str(worktree_issue_dir / "config.yaml"))

            # Step 5: Sync .cafe/issues/{issue_name}/ from worktree to repo root
            try:
                console.print("[dim]Syncing issue data from worktree to repo root...[/dim]")
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
                console.print(f"  2. git branch -D {feature_branch}  # Force delete if needed")
                console.print(f"  3. cafe rm {issue_name}")
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
                console.print(f"  1. cd {Path.cwd()}")
                console.print(f"  2. git branch -D {feature_branch}  # Force delete if needed")
                console.print(f"  3. cafe rm {issue_name}")
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
                console.print(f"  2. git branch -D {feature_branch}  # Force delete if needed")
                console.print(f"  3. cafe rm {issue_name}")
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
                console.print(f"  2. cafe rm {issue_name}")
                console.print()
                raise typer.Exit(1)

        # 6. Archive issue data to ~/.cafe/projects/<project-path>/archived/<issue-name>/
        try:
            console.print("[dim]Archiving issue data...[/dim]")

            # Get project path in ~/.claude/projects/ naming format
            archive_path = _get_issue_archive_path(issue_name)
            archive_base = archive_path.parent

            # Ensure archive directory exists
            archive_base.mkdir(parents=True, exist_ok=True)

            # Copy config.yaml into issue dir so it gets archived
            # (non-worktree uses cp to keep .cafe/config.yaml for other issues)
            issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name
            if issue_dir.exists() and not worktree_path:
                repo_config = Path.cwd() / ".cafe" / "config.yaml"
                if repo_config.exists() and not (issue_dir / "config.yaml").exists():
                    shutil.copy2(str(repo_config), str(issue_dir / "config.yaml"))

            # Move issue directory to archive
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

        # 7. Remember main repo root before potentially changing directory
        main_repo_root = Path.cwd().resolve()

        # Resolve worktree_path to absolute before any chdir
        if worktree_path:
            worktree_path = str(Path(worktree_path).resolve())

        # Navigate to worktree directory if it was created
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


def reset(
    phase: Optional[str] = typer.Argument(
        None,
        help="Phase name (spec, plan, develop, review, pr). If not provided, resets the last phase with iterations"
    ),
    iteration: int = typer.Option(
        0,
        "--iteration", "-i",
        help="Iteration number to keep (positive, 0=remove latest only, negative=relative)"
    ),
) -> None:
    """Remove iterations from a phase when agent behaves unexpectedly.

    Backs up the issue directory before removing iterations, then removes all iterations
    after the specified target iteration. Similar iteration number format as `cafe show`.

    \b
    Examples:
        cafe reset                   # Remove latest iteration from last phase
        cafe reset spec              # Remove latest iteration from spec
        cafe reset develop -i 2      # Keep only iteration_002, remove all after
        cafe reset plan -i -1        # Same as cafe reset plan (remove latest)
    """
    try:
        # 1. Get current branch name (issue_name)
        try:
            git_ops = GitOperations()
            issue_name = git_ops.get_current_branch()
        except Exception as e:
            console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
            raise typer.Exit(1)

        # 2. If phase not provided, find the last phase with iterations based on end_time or timestamp
        if phase is None:
            from cafe.services.summary_service import SummaryService
            from datetime import datetime

            service = SummaryService()
            latest_phase = None
            latest_time = None

            # Check all phases to find the one with the latest end_time (or timestamp if incomplete)
            for phase_name in VALID_PHASES:
                iterations = service.load_iteration_statuses(issue_name, phase_name)
                if not iterations:
                    continue

                for iteration_info in iterations:
                    # Prefer end_time, fallback to timestamp for incomplete iterations
                    time_str = iteration_info.get("end_time") or iteration_info.get("timestamp")
                    if time_str:
                        try:
                            time = datetime.fromisoformat(time_str)
                            if latest_time is None or time > latest_time:
                                latest_time = time
                                latest_phase = phase_name
                        except (ValueError, TypeError):
                            continue

            if latest_phase is None:
                console.print("[yellow]ℹ️  No phases with iterations found[/yellow]")
                raise typer.Exit(0)

            phase = latest_phase
            console.print(f"[dim]Auto-detected last phase: {phase}[/dim]")

        # 3. Validate phase name
        if phase not in VALID_PHASES:
            console.print(f"[red]Error: Invalid phase '{phase}'[/red]")
            console.print(f"[dim]Valid phases: {', '.join(VALID_PHASES)}[/dim]")
            raise typer.Exit(1)

        # 4. Verify phase directory exists
        phase_dir = Path.cwd() / ".cafe" / "issues" / issue_name / phase
        if not phase_dir.exists():
            console.print(f"[red]Error: Phase directory not found: {phase_dir}[/red]")
            raise typer.Exit(1)

        # 5. Get all iterations in phase
        all_iteration_dirs = sorted([d for d in phase_dir.glob("iteration_*") if d.is_dir()])
        if not all_iteration_dirs:
            console.print(f"[yellow]ℹ️  No iterations found in {phase} phase[/yellow]")
            raise typer.Exit(0)

        all_iteration_numbers = []
        for dir_path in all_iteration_dirs:
            try:
                num = int(dir_path.name.split("_")[1])
                all_iteration_numbers.append(num)
            except (IndexError, ValueError):
                continue

        if not all_iteration_numbers:
            console.print(f"[yellow]ℹ️  No valid iterations found in {phase} phase[/yellow]")
            raise typer.Exit(0)

        # 6. Resolve iteration number to target iteration using shared logic
        try:
            if iteration == 0:
                # Special case for reset: -i 0 means remove latest only
                # Resolve to latest, then set target to second-to-last
                latest = _resolve_iteration_index(all_iteration_numbers, 0)
                if len(all_iteration_numbers) > 1:
                    target_iteration = all_iteration_numbers[-2]
                else:
                    target_iteration = 0  # Will remove the only iteration
                to_remove = [i for i in all_iteration_numbers if i > target_iteration]
            else:
                # For positive and negative: the resolved iteration is what we keep
                target_iteration = _resolve_iteration_index(all_iteration_numbers, iteration)
                # Determine which iterations to remove (all after target)
                to_remove = [i for i in all_iteration_numbers if i > target_iteration]
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

        if not to_remove:
            console.print(f"[yellow]ℹ️  No iterations to remove[/yellow]")
            raise typer.Exit(0)

        # 7. Display confirmation prompt
        console.print()
        console.print(f"[yellow]⚠️  About to reset {phase} phase[/yellow]")
        console.print()

        console.print("[cyan]📋 Iterations to remove:[/cyan]")
        for iter_num in sorted(to_remove):
            console.print(f"  • iteration_{iter_num:03d}")
        console.print()

        # Get backup path
        project_path = _get_project_path()
        home_dir = Path.home()
        archive_path = home_dir / ".cafe" / "projects" / project_path / "archived" / issue_name

        console.print(f"[cyan]📁 Backup location: {archive_path}[/cyan]")
        console.print()

        # Get user confirmation
        confirm = prompt_confirm("Proceed with reset?", default=False)
        if not confirm:
            console.print()
            console.print("[yellow]❌ Reset cancelled. No changes made.[/yellow]")
            console.print()
            raise typer.Exit(0)

        # 8. Create backup of entire issue directory
        try:
            console.print("[dim]Backing up issue data...[/dim]")
            archive_base = archive_path.parent
            archive_base.mkdir(parents=True, exist_ok=True)

            issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name

            # Remove existing backup if present
            if archive_path.exists():
                shutil.rmtree(archive_path)

            # Backup issue directory
            shutil.copytree(issue_dir, archive_path)
            console.print(f"[green]✓ Backed up issue data to: {archive_path}[/green]")
        except Exception as e:
            console.print(f"[red]❌ Backup failed: {e}[/red]")
            console.print(f"[red]   Reset cancelled - no changes made[/red]")
            console.print()
            raise typer.Exit(1)

        # 9. Remove iterations
        try:
            console.print("[dim]Removing iterations...[/dim]")
            for iter_num in sorted(to_remove):
                iter_dir = phase_dir / f"iteration_{iter_num:03d}"
                if iter_dir.exists():
                    shutil.rmtree(iter_dir)

            console.print(f"[green]✓ Removed iterations: {', '.join([f'iteration_{i:03d}' for i in sorted(to_remove)])}[/green]")
        except Exception as e:
            console.print(f"[red]❌ Failed to remove iterations: {e}[/red]")
            console.print()
            raise typer.Exit(1)

        # 9. Update status.json and iterations.jsonl
        try:
            console.print("[dim]Updating phase status...[/dim]")
            status_file = phase_dir / "status.json"
            iterations_file = phase_dir / "iterations.jsonl"

            if target_iteration > 0:
                # Read target iteration's context.json to get status_code
                target_context_file = phase_dir / f"iteration_{target_iteration:03d}" / "context.json"
                target_status_code = None
                target_timestamp = None
                target_end_time = None

                if target_context_file.exists():
                    with open(target_context_file, "r", encoding="utf-8") as f:
                        target_context = json.load(f)
                        target_status_code = target_context.get("status_code")
                        target_timestamp = target_context.get("timestamp")
                        target_end_time = target_context.get("end_time")

                # Update status.json with target iteration's data
                status_data = {
                    "phase": phase,
                    "status": "completed",
                    "status_code": target_status_code,
                    "timestamp": target_timestamp or datetime.now().astimezone().isoformat(),
                    "iteration": target_iteration,
                    "message": f"Phase completed with {target_status_code}" if target_status_code else "Phase reset to this iteration",
                    "end_time": target_end_time or target_timestamp or datetime.now().astimezone().isoformat(),
                }

                with open(status_file, "w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=2, ensure_ascii=False)

                console.print(f"[green]✓ Updated {phase} phase status to iteration_{target_iteration:03d}[/green]")

                # Update iterations.jsonl to remove deleted iterations
                if iterations_file.exists():
                    iterations_data = []
                    content = iterations_file.read_text(encoding="utf-8").strip()
                    if content:
                        for line in content.split("\n"):
                            if line.strip():
                                iterations_data.append(json.loads(line))

                    kept_iterations = [rec for rec in iterations_data if rec.get("iteration", 0) <= target_iteration]
                    with open(iterations_file, "w", encoding="utf-8") as f:
                        for record in kept_iterations:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")

                    console.print(f"[green]✓ Updated iterations.jsonl[/green]")
            else:
                # No iterations left, delete status.json and iterations.jsonl
                if status_file.exists():
                    status_file.unlink()
                if iterations_file.exists():
                    iterations_file.unlink()
                console.print(f"[green]✓ Phase restarted (status.json and iterations.jsonl removed)[/green]")

            console.print("[green]✓ Status saved[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️  Failed to update status: {e}[/yellow]")

        # 10. Success message
        console.print()
        console.print(f"[green]✓ Successfully reset {phase} phase[/green]")
        console.print(f"  📁 Backup location: {archive_path}")
        console.print()

    except typer.Exit:
        raise
    except Exception as e:
        console.print()
        console.print(f"[red]Error during reset: {e}[/red]")
        raise typer.Exit(1)
