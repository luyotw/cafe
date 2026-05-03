"""Workflow-related command implementations extracted from cli.py."""

from __future__ import annotations

from typing import Any, Dict

import typer


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """Inject runtime symbols from cafe.ui.cli into this module."""
    for key, value in runtime_globals.items():
        if key.startswith("__") or key == "set_runtime":
            continue
        globals()[key] = value


def make(
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        "-u",
        help="Initial requirements to pass into the first spec step",
    ),
) -> None:
    """🚀 Check environment and execute complete development workflow.

    \b
    This command will:
    1. Check if all configured agent CLI tools are installed
    2. If environment check passes, execute `cafe workflow --execute` to start automated workflow

    Please run `cafe prepare` first to initialize issue environment.

    \b
    Examples:
        cafe make
        cafe make --config /path/to/config.yaml
        cafe make --user-input "As a user, I want to export CSV reports."
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
        console.print("[dim]  • codex: https://developers.openai.com/codex/cli/reference[/dim]")
        console.print(
            "[dim]  • copilot: https://docs.github.com/en/copilot/using-github-copilot/using-github-copilot-in-the-command-line[/dim]"
        )
        raise typer.Exit(1)

    # All CLIs available, execute cafe workflow --execute
    console.print("[green]✓ All agent CLI tools are installed[/green]")
    console.print()
    console.print("[bold cyan]🚀 Starting automated workflow...[/bold cyan]")
    console.print()

    # Build command
    cmd = [sys.executable, "-m", "cafe.ui.cli", "workflow", "--execute"]
    if user_input:
        cmd.extend(["--user-input", user_input])

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            # Error already printed by spec phase command, just exit
            raise typer.Exit(result.returncode)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error executing workflow: {e}[/red]")
        raise typer.Exit(1)


def show(
    phase_name: str = typer.Argument(
        ...,
        help="Playbook step name"
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
    # Get current branch name (issue_name)
    try:
        git_ops = GitOperations()
        issue_name = git_ops.get_current_branch()
    except Exception as e:
        console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
        raise typer.Exit(1)

    valid_phases = _load_issue_step_names(issue_name)

    # Validate phase name
    if phase_name not in valid_phases:
        console.print(f"[red]Error: Invalid phase '{phase_name}'[/red]")
        console.print(f"[dim]Valid phases: {', '.join(valid_phases)}[/dim]")
        raise typer.Exit(1)

    # Set default content type
    if content_type is None:
        content_type = "output"

    # Validate content type
    if content_type not in VALID_CONTENT_TYPES:
        console.print(f"[red]Error: Invalid content type '{content_type}'[/red]")
        console.print(f"[dim]Valid types: {', '.join(VALID_CONTENT_TYPES)}[/dim]")
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
            # Special error message for user_input content type
            if content_type == "user_input":
                console.print("[red]No user input markdown file found for this iteration.[/red]")
            else:
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
            elif content_type in ("checklist", "output"):
                # For checklist and output, output raw content without Rich formatting
                # Rich treats [x] as special markup and removes it
                print(content)
            else:
                # Output other files directly
                console.print(content)

        except UnicodeDecodeError:
            console.print(f"[red]Error: Failed to read file (not UTF-8 encoded)[/red]")
            raise typer.Exit(1)

    except ValueError as e:
        # Special error message for user_input content type
        if content_type == "user_input":
            console.print("[red]No user input markdown file found for this iteration.[/red]")
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


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
        phase_names = _load_issue_step_names(issue_name)

        # Load phase and iteration data
        phase_statuses = {}
        iteration_data = {}

        for phase_name in phase_names:
            phase_status = service.load_phase_status(issue_name, phase_name)
            if phase_status:
                phase_statuses[phase_name] = phase_status

            iterations = service.load_iteration_statuses(issue_name, phase_name)
            if iterations:
                iteration_data[phase_name] = iterations

        # Build timeline
        builder = TimelineBuilder(issue_name, phase_names=phase_names)
        entries = builder.build_timeline_entries(phase_statuses, iteration_data)

        # Display as table
        display = SummaryDisplay()
        display.render_table(entries)

        # Display aggregated model token usage summary
        display.render_model_summary_table(entries)

    except Exception as e:
        console.print(f"[red]Error: Failed to display summary: {e}[/red]")
        raise typer.Exit(1)


def workflow(
    playbook: Optional[str] = typer.Option(None, "--playbook", help="Playbook name"),
    issue: Optional[str] = typer.Option(None, "--issue", help="Issue directory name"),
    start_step: Optional[str] = typer.Option(None, "--start-step", help="Start execution from a specific step"),
    single_step: bool = typer.Option(False, "--single-step", help="Run only one playbook step"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Run with built-in dry executor"),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        "-u",
        help="Initial requirements to pass into the first spec step",
    ),
) -> None:
    """Run playbook workflow using the new generic runner."""
    try:
        def _predict_next_iteration(issue_root: Path, step_name: str) -> int:
            step_dir = issue_root / step_name
            existing = sorted(step_dir.glob("iteration_*/context.json"))
            if not existing:
                return 1
            count = len(existing)
            try:
                import json as _json
                last_data = _json.loads(existing[-1].read_text(encoding="utf-8"))
                if not last_data.get("status_code"):
                    return last_data.get("iteration", count)
            except Exception:
                return count
            return count + 1

        git = GitOperations()
        issue_name = issue or git.get_current_branch()
        issue_dir = Path(".cafe/issues") / issue_name
        selected_playbook = _resolve_selected_playbook(playbook)
        config_manager = ConfigManager(".cafe")
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()

        playbook_loader = PlaybookLoader()
        playbook_data = playbook_loader.load(selected_playbook)
        interactive = sys.stdin.isatty() or os.getenv("CAFE_FORCE_INTERACTIVE") == "1"
        generic_phase = GenericPhase(SkillLoader())

        def dry_executor(step_name: str, step_def: Dict, blackboard_state: object) -> StepExecutionResult:
            output_key = step_def.get("output_artifact", step_name)
            output_path = issue_dir / step_name / "output.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"# {step_name}\n\nDry-run output\n", encoding="utf-8")
            if step_name == "pr":
                store = BlackboardStore(issue_dir)
                blackboard = store.load_or_create(
                    str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
                    playbook_id=str(playbook_data["playbook"]["id"]),
                )
                store.update_handoff_contract(
                    blackboard,
                    from_step="pr",
                    to_owner=HandoffOwner.DONE,
                    to_step="done",
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    source="workflow.dry_run",
                )
                return StepExecutionResult(
                    response="dry run",
                    artifacts={str(output_key): str(output_path)},
                    events=[{"type": "pr_synced", "url": "https://example.com/dry-run-pr"}],
                )
            return StepExecutionResult(
                response="dry-run",
                artifacts={str(output_key): str(output_path)},
                status_code="CAFE_CONFIRMED",
            )
        step_executor = None if dry_run else _build_workflow_step_executor(
            config_manager=config_manager,
            issue_dir=issue_dir,
            issue_name=issue_name,
            playbook_data=playbook_data,
            generic_phase=generic_phase,
            step_user_inputs={"spec": user_input} if user_input else None,
            interactive=interactive,
        )

        def wrapped_executor(step_name: str, step_def: Dict, blackboard_state: object) -> Any:
            iteration = _predict_next_iteration(issue_dir, step_name)
            console.print(f"[dim]Executing[/dim] step={step_name} iteration={iteration:03d}")
            if dry_run:
                return dry_executor(step_name, step_def, blackboard_state)
            assert step_executor is not None
            result = step_executor.execute_step(step_name, step_def, blackboard_state)
            if isinstance(result, StepExecutionResult):
                for event in result.events:
                    if not isinstance(event, dict) or event.get("type") != "pr_synced":
                        continue
                    pr_url = str(event.get("url", "")).strip()
                    if pr_url:
                        console.print(f"[green]PR synced[/green]")
                        console.print(f"  URL: {pr_url}")
            return result

        pending_start_step = start_step
        while True:
            if dry_run:
                pending_start_step = pending_start_step or str(
                    playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))
                )
            else:
                pending_start_step = _consume_pending_chat_handoff(
                    issue_dir=issue_dir,
                    playbook_data=playbook_data,
                    requested_start_step=pending_start_step,
                )
            if pending_start_step is not None and pending_start_step not in playbook_data["steps"] and pending_start_step not in {"user", "done"}:
                raise ValueError(f"Unknown playbook step '{pending_start_step}'")

            blackboard = BlackboardStore(issue_dir).load_or_create(
                str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
                playbook_id=str(playbook_data["playbook"]["id"]),
            )

            active_step = pending_start_step or blackboard.current_step
            if not dry_run and active_step in {"user", "done"}:
                incomplete_step = _find_incomplete_workflow_step(
                    issue_dir=issue_dir,
                    playbook_data=playbook_data,
                )
                if incomplete_step is not None:
                    pending_start_step = incomplete_step
                    store = BlackboardStore(issue_dir)
                    store.set_current_step(blackboard, incomplete_step)
                    store.update_handoff_contract(
                        blackboard,
                        from_step=incomplete_step,
                        to_owner=HandoffOwner.AGENT,
                        to_step=incomplete_step,
                        intent=HandoffIntent.AWAIT_AGENT,
                        source="workflow.resume_incomplete",
                    )
                    console.print(
                        f"[yellow]Resuming unfinished iteration[/yellow] step={incomplete_step}"
                    )
                    continue
                external_step = _find_external_resume_step(
                    issue_dir=issue_dir,
                    playbook_data=playbook_data,
                    git_ops=git,
                )
                if external_step is not None:
                    pending_start_step = external_step
                    store = BlackboardStore(issue_dir)
                    store.set_current_step(blackboard, external_step)
                    store.update_handoff_contract(
                        blackboard,
                        from_step=external_step,
                        to_owner=HandoffOwner.AGENT,
                        to_step=external_step,
                        intent=HandoffIntent.AWAIT_AGENT,
                        source="workflow.resume_external_feedback",
                    )
                    console.print(
                        f"[yellow]Detected external workflow feedback[/yellow] step={external_step}"
                    )
                    continue
            if not dry_run and active_step in {"user", "done"}:
                if not interactive:
                    if active_step == "done":
                        console.print("[green]Workflow already completed[/green] step=done")
                    console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
                    return
                user_selected_step = _handle_user_phase(
                    issue_name=issue_name,
                    issue_dir=issue_dir,
                    playbook_data=playbook_data,
                    blackboard=blackboard,
                    phase_name=active_step,
                )
                if not user_selected_step:
                    return
                pending_start_step = user_selected_step
                continue

            effective_start_step = active_step
            console.print(
                f"[dim]Workflow context[/dim] playbook={playbook_data['playbook']['id']} step={effective_start_step}"
            )

            runner = BlackboardWorkflowRuntime(
                issue_dir=issue_dir,
                playbook=playbook_data,
                executor=wrapped_executor,
            )
            result = runner.run(start_step=effective_start_step, single_step=single_step)
            latest_blackboard = BlackboardStore(issue_dir).load_or_create(
                str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
                playbook_id=str(playbook_data["playbook"]["id"]),
            )
            if (
                not single_step
                and latest_blackboard.current_step == "pr"
                and effective_start_step != "pr"
            ):
                pending_start_step = "pr"
                continue
            if interactive and not dry_run and not single_step and latest_blackboard.current_step == "user":
                pending_start_step = "user"
                continue
            if not interactive and not dry_run and not single_step and latest_blackboard.current_step == "user":
                console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
                return
            if result.completed:
                console.print(
                    f"[green]Workflow completed[/green] step={result.final_step} status={result.final_status_code} next={latest_blackboard.current_step}"
                )
            else:
                console.print(
                    f"[yellow]Workflow paused[/yellow] step={result.final_step} status={result.final_status_code} next={latest_blackboard.current_step}"
                )
                console.print(
                    f"[dim]{_build_workflow_pause_guidance(blackboard=latest_blackboard, final_status_code=result.final_status_code)}[/dim]"
                )
                _print_workflow_pause_guidance(
                    step_name=result.final_step,
                    status_code=result.final_status_code,
                )
                if (
                    interactive
                    and not dry_run
                    and not single_step
                    and result.final_status_code in {"NO_BATON_TRANSITION", "NO_STATUS_TRANSITION"}
                ):
                    recovery_step = _handle_user_phase(
                        issue_name=issue_name,
                        issue_dir=issue_dir,
                        playbook_data=playbook_data,
                        blackboard=latest_blackboard,
                        phase_name=result.final_step,
                    )
                    if recovery_step:
                        pending_start_step = recovery_step
                        continue
            return
    except CriticalPhaseError as e:
        _handle_phase_exception(e, "workflow")
    except Exception as e:
        console.print(f"[red]Error: workflow run failed: {e}[/red]")
        raise typer.Exit(1)
