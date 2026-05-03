"""Legacy phase command wrappers extracted from cli.py."""

from __future__ import annotations

from typing import Any, Dict

import typer


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """Inject runtime symbols from cafe.ui.cli into this module."""
    for key, value in runtime_globals.items():
        if key.startswith("__") or key == "set_runtime":
            continue
        globals()[key] = value


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
    template: Optional[str] = typer.Option(
        None,
        "--template",
        help="Spec template name (default: auto, reads from issue.yaml if present)",
    ),
    sync_github: Optional[bool] = typer.Option(
        None,
        "--sync-github/--no-sync-github",
        help="Sync spec to GitHub issue when confirmed (default: auto-detect based on issue_id)",
    ),
) -> None:
    """Legacy wrapper for the specification step.

    Prefer `cafe make --user-input ...` or
    `cafe workflow --start-step spec --execute --user-input ...`.
    Use `cafe edit spec` to open the latest spec artifact.

    \b
    Examples:
        cafe make --user-input "Add CSV export"
        cafe workflow --start-step spec --execute --user-input "Add CSV export"
        cafe edit spec
    """
    # Handle edit action
    if action == "edit":
        try:
            _print_legacy_phase_command_notice(
                phase_name="spec edit",
                preferred_command="cafe edit spec",
            )
            _edit_latest_phase_artifact(
                ctx=ctx,
                phase_name="spec",
                missing_hint="Run 'cafe make --user-input ...' or 'cafe workflow --start-step spec --execute --user-input ...' first.",
            )
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "spec")
        _print_legacy_phase_command_notice(
            phase_name="spec",
            preferred_command="cafe make --user-input '...'",
        )

        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()
        is_interactive = (interactive and sys.stdin.isatty()) or os.getenv("CAFE_FORCE_INTERACTIVE") == "1"
        if auto and not is_interactive:
            console.print("[red]Error: --auto can only be used in interactive mode[/red]")
            raise typer.Exit(1)
        _reject_unsupported_phase_options(
            "spec",
            {
                "issue": issue_id is not None,
                "fetch-issue": fetch_issue_id is not None,
                "rigor": rigor is not None,
                "template": template is not None,
                "sync-github": sync_github is not None,
            },
        )

        current_input = user_input
        if is_interactive and not current_input:
            current_input = prompt_multiline("Requirements:").strip()
        if not is_interactive and not current_input:
            console.print("[red]Error: --user-input is required when using --no-interactive[/red]")
            raise typer.Exit(1)

        alias_result = _run_iterative_alias_step(
            issue_name=issue_name,
            step_name="spec",
            config_manager=config_manager,
            interactive=is_interactive,
            auto=auto,
            continuation_statuses=["CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"],
            role_agent_map_override={"pm": pm_agent} if pm_agent else None,
            user_input=current_input,
            show_prompt=show_prompt,
            clarification_prompt="Additional details:",
        )
        status_code = _alias_status(alias_result)
        console.print()
        if _alias_is_confirmed_transition(alias_result, "plan"):
            console.print("[bold green]✅ Spec clarification completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            if auto:
                _execute_next_phase_auto("plan", issue_name)
            else:
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        elif _alias_confirm_output_pause(alias_result):
            console.print("[bold green]✅ Spec draft completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Please review the spec, then continue with:[/dim] [bold]cafe make[/bold]")
        elif _alias_needs_clarification(alias_result):
            console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Add clarification and continue with:[/dim] [bold]cafe make[/bold]")
        else:
            console.print(f"[bold yellow]Status: {status_code}[/bold yellow]")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "spec")


def plan(
    ctx: typer.Context,
    action: Optional[str] = typer.Argument(None, help="Action: edit (to edit latest plan file)"),
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
    sync_github: Optional[bool] = typer.Option(
        None,
        "--sync-github/--no-sync-github",
        help="Sync plan to GitHub issue when confirmed (default: auto-detect based on issue_id)",
    ),
) -> None:
    """Legacy wrapper for the planning step.

    Prefer `cafe make` to continue the workflow.
    Use `cafe edit plan` to edit the latest plan artifact.

    \b
    Examples:
        cafe make --user-input "Draft the implementation approach"
        cafe make
        cafe edit plan
    """
    # Handle edit action
    if action == "edit":
        try:
            _print_legacy_phase_command_notice(
                phase_name="plan edit",
                preferred_command="cafe edit plan",
            )
            _edit_latest_phase_artifact(
                ctx=ctx,
                phase_name="plan",
                missing_hint="Run 'cafe make' first.",
            )
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "plan")
        _print_legacy_phase_command_notice(
            phase_name="plan",
            preferred_command="cafe make",
        )

        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()

        # Check if spec file exists (use latest versioned file)
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make --user-input ...' first.[/dim]")
            raise typer.Exit(1)
        is_interactive = (interactive and sys.stdin.isatty()) or os.getenv("CAFE_FORCE_INTERACTIVE") == "1"
        if auto and not is_interactive:
            console.print("[red]Error: --auto can only be used in interactive mode[/red]")
            raise typer.Exit(1)
        _reject_unsupported_phase_options(
            "plan",
            {
                "issue": issue_id is not None,
                "sync-github": sync_github is not None,
            },
        )

        if issue_id:
            console.print(f"GitHub Issue: #{issue_id}")

        alias_result = _run_iterative_alias_step(
            issue_name=issue_name,
            step_name="plan",
            config_manager=config_manager,
            interactive=is_interactive,
            auto=auto,
            continuation_statuses=["CAFE_NEED_CLARIFICATION", "CAFE_READY_FOR_REVIEW"],
            role_agent_map_override={"developer": dev_agent} if dev_agent else None,
            show_prompt=show_prompt,
            clarification_prompt="Additional planning details:",
        )
        status_code = _alias_status(alias_result)
        console.print()
        if _alias_needs_clarification(alias_result):
            console.print("[bold yellow]💬 Agent needs clarification[/bold yellow]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Add planning details and continue with:[/dim] [bold]cafe make[/bold]")
        elif _alias_confirm_output_pause(alias_result):
            console.print("[bold yellow]📋 Plan ready for review[/bold yellow]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Review the plan, then continue with:[/dim] [bold]cafe make[/bold]")
        elif _alias_is_confirmed_transition(alias_result, "develop"):
            console.print("[bold green]✅ Implementation plan completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            if auto:
                _execute_next_phase_auto("develop", issue_name)
            else:
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        else:
            console.print(f"[bold yellow]Status: {status_code}[/bold yellow]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "plan")


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
        help="Auto mode: continue iterations automatically within the legacy wrapper",
    ),
) -> None:
    """Legacy wrapper for the development step.

    Prefer `cafe make` to continue the workflow.

    \b
    Examples:
        cafe make
        cafe make --user-input "Please be careful"
        cafe edit develop
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "develop")
        _print_legacy_phase_command_notice(
            phase_name="develop",
            preferred_command="cafe make",
        )

        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make --user-input ...' first.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make' first.[/dim]")
            raise typer.Exit(1)
        _reject_unsupported_phase_options(
            "develop",
            {
                "mode": mode != "local",
                "issue": issue_id is not None,
                "approve-denied-tools": approve_denied_tools is not None,
                "pr-number": pr_number is not None,
            },
        )

        alias_result = _execute_single_step_alias(
            issue_name=issue_name,
            step_name="develop",
            config_manager=config_manager,
            role_agent_map_override={"developer": dev_agent} if dev_agent else None,
            user_input=user_input,
            show_prompt=show_prompt,
        )
        status_code = _alias_status(alias_result)
        console.print()
        resolved_next_step = _alias_next_step(alias_result)
        if not resolved_next_step:
            if status_code == "CAFE_CONFIRMED_SKIP_REVIEW":
                resolved_next_step = "pr"
            elif status_code == "CAFE_CONFIRMED":
                resolved_next_step = "review"

        if resolved_next_step in {"review", "pr"}:
            console.print("[bold green]✅ Development completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            if auto:
                _execute_next_phase_auto(
                    resolved_next_step,
                    issue_name,
                )
            else:
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        elif _alias_needs_clarification(alias_result) or _alias_needs_permission(alias_result):
            if auto:
                _execute_next_phase_auto("develop", issue_name)
            else:
                console.print(f"[yellow]⏸️  Development paused: {status_code}[/yellow]")
                console.print("[dim]Resume with:[/dim] [bold]cafe make[/bold]")
        else:
            console.print(f"[bold red]❌ Development failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "develop")


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
    """Legacy wrapper for the review step.

    Prefer `cafe make` to continue the workflow.

    \b
    Examples:
        cafe make
        cafe edit review
    """
    # Handle edit action
    if action == "edit":
        try:
            _print_legacy_phase_command_notice(
                phase_name="review edit",
                preferred_command="cafe edit review",
            )
            _edit_latest_phase_artifact(
                ctx=ctx,
                phase_name="review",
                missing_hint="Run 'cafe make' first.",
            )
            return

        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "review")
        _print_legacy_phase_command_notice(
            phase_name="review",
            preferred_command="cafe make",
        )

        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make --user-input ...' first.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make' first.[/dim]")
            raise typer.Exit(1)
        _reject_unsupported_phase_options(
            "review",
            {
                "mode": mode != "local",
                "issue": issue_id is not None,
                "commit": commit is not None,
                "base": base_branch != "main",
                "pr-number": pr_number is not None,
                "force": force,
            },
        )

        alias_result = _execute_single_step_alias(
            issue_name=issue_name,
            step_name="review",
            config_manager=config_manager,
            role_agent_map_override={"reviewer": reviewer_agent} if reviewer_agent else None,
            show_prompt=show_prompt,
        )
        status_code = _alias_status(alias_result)
        console.print()
        if _alias_is_confirmed_transition(alias_result, "pr"):
            console.print("[bold green]✅ Code review passed![/bold green]")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            if auto:
                _execute_next_phase_auto("pr", issue_name)
            else:
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        elif _alias_targets(alias_result, "develop") or status_code == "CAFE_NEEDS_CHANGES":
            console.print(f"[bold yellow]📝 Code review completed with status: {status_code}[/bold yellow]")
            if alias_result.get("output_file"):
                console.print(f"[dim]Review feedback saved to:[/dim] [dim]{alias_result['output_file']}[/dim]")
            console.print()
            if auto:
                max_iterations_value = config_manager.get("auto.max_review_iterations", 5)
                try:
                    max_iterations = int(max_iterations_value)
                except (ValueError, TypeError):
                    max_iterations = 5
                current_iteration = _get_latest_review_iteration(issue_name)
                if current_iteration >= max_iterations:
                    console.print(f"[bold yellow]⚠️  Review loop limit reached ({max_iterations} times)[/bold yellow]")
                    console.print("[dim]You can:[/dim]")
                    console.print("[dim]  • Continue with: [bold]cafe make[/bold][/dim]")
                    console.print("[dim]  • Adjust limit: [bold]cafe config set auto.max_review_iterations 10[/bold][/dim]")
                else:
                    console.print(f"[dim]Review iteration: {current_iteration}/{max_iterations}[/dim]")
                    _execute_next_phase_auto("develop", issue_name)
            else:
                console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        elif _alias_needs_clarification(alias_result):
            console.print("[bold yellow]💬 Review needs clarification[/bold yellow]")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print("[dim]Resume with:[/dim] [bold]cafe make[/bold]")
        else:
            console.print(f"[bold red]❌ Review failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "review")


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
    post_todo_list: Optional[bool] = typer.Option(
        None,
        "--post-todo-list/--no-post-todo-list",
        help="Post organized todo list as PR comment (default: auto-detect from config)",
    ),
) -> None:
    """Legacy wrapper for the PR step.

    Prefer `cafe make` to continue the workflow.

    \b
    Examples:
        cafe make
        cafe edit pr
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "pr")
        _print_legacy_phase_command_notice(
            phase_name="pr",
            preferred_command="cafe make",
        )

        # Get latest versioned files
        spec_file_path = _get_latest_versioned_file("spec", issue_name)
        if spec_file_path is None:
            console.print(f"[red]Error: No spec file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make --user-input ...' first.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print("[dim]Hint: Run 'cafe make' first.[/dim]")
            raise typer.Exit(1)

        # Initialize components
        config_dir = (
            str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"
        )
        config_manager = ConfigManager(config_dir)
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()
        _reject_unsupported_phase_options(
            "pr",
            {
                "draft": draft is not None,
                "title": title is not None,
                "body": body is not None,
                "update": update,
                "force": force,
                "auto": auto,
                "base": base != "main",
                "post-todo-list": post_todo_list is not None,
            },
        )

        dev_agent = config_manager.get("agents.developer.name", "David")
        alias_result = _execute_single_step_alias(
            issue_name=issue_name,
            step_name="pr",
            config_manager=config_manager,
            role_agent_map_override={"developer": dev_agent} if dev_agent else None,
            show_prompt=False,
        )
        status_code = _alias_status(alias_result)
        console.print()
        if _alias_is_done(alias_result) or status_code == "CAFE_CONFIRMED":
            console.print("[bold green]✅ PR content completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Next step:[/dim] [bold]Review and submit the PR[/bold]")
        elif _alias_targets(alias_result, "develop") or status_code == "CAFE_NEEDS_CHANGES":
            console.print(f"[bold yellow]PR step completed with status: {status_code}[/bold yellow]")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]Continue the workflow with:[/dim] [bold]cafe make[/bold]")
        else:
            console.print(f"[bold red]❌ PR failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "pr")
