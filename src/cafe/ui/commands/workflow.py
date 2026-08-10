"""Workflow-related command implementations extracted from cli.py."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.issue_resolution import ActiveIssueResolutionError, resolve_active_issue
from cafe.core.playbook import resolve_step_behavior
from cafe.core.types import CriticalPhaseError
from cafe.core.workflow_models import StepExecutionResult, StepInterrupted
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.cli_shared import (
    VALID_CONTENT_TYPES,
    apply_alignment_decision_from_payload,
    get_show_file_path as _get_show_file_path,
    parse_alignment_decision_payload,
    resolve_iteration_number as _resolve_iteration_number,
)
from cafe.ui.human_tasks import apply_human_task_payload
from cafe.agents.executor import AgentExecutionError
from cafe.utils.config import ConfigError, validate_directories_exist

# Lazy access to GitOperations via cli for backward-compat test patching.
def _get_GitOperations():
    from cafe.ui.cli import GitOperations
    return GitOperations


# Lazy access to ConfigManager via cli for backward-compat test patching.
def _get_ConfigManager():
    from cafe.ui.cli import ConfigManager
    return ConfigManager

# Functions moved to cli_shared, accessed through cli module for
# backward-compatible test patching (tests patch ``cafe.ui.cli._func``).
# Using late imports to avoid circular-import issues at module load time.


def _build_workflow_pause_guidance(*a, **kw):
    from cafe.ui.cli import _build_workflow_pause_guidance as _fn
    return _fn(*a, **kw)


def _build_workflow_step_executor(*a, **kw):
    from cafe.ui.cli import _build_workflow_step_executor as _fn
    return _fn(*a, **kw)


def _check_agent_clis_available(*a, **kw):
    from cafe.ui.cli import _check_agent_clis_available as _fn
    return _fn(*a, **kw)


def _consume_pending_chat_handoff(*a, **kw):
    from cafe.ui.cli import _consume_pending_chat_handoff as _fn
    return _fn(*a, **kw)


def _find_external_resume_step(*a, **kw):
    from cafe.ui.cli import _find_external_resume_step as _fn
    return _fn(*a, **kw)


def _find_incomplete_workflow_step(*a, **kw):
    from cafe.ui.cli import _find_incomplete_workflow_step as _fn
    return _fn(*a, **kw)


def _handle_phase_exception(*a, **kw):
    from cafe.ui.cli import _handle_phase_exception as _fn
    return _fn(*a, **kw)


def _handle_user_phase(*a, **kw):
    from cafe.ui.cli import _handle_user_phase as _fn
    return _fn(*a, **kw)


def _load_issue_step_names(*a, **kw):
    from cafe.ui.cli import _load_issue_step_names as _fn
    return _fn(*a, **kw)


def _print_workflow_pause_guidance(*a, **kw):
    from cafe.ui.cli import _print_workflow_pause_guidance as _fn
    return _fn(*a, **kw)


def _resolve_selected_playbook(*a, **kw):
    from cafe.ui.cli import _resolve_selected_playbook as _fn
    return _fn(*a, **kw)


def _resolve_issue_playbook_name(*a, **kw):
    from cafe.ui.cli import _resolve_issue_playbook_name as _fn
    return _fn(*a, **kw)

console = Console()

_USER_INPUT_HELP = (
    "Initial workflow input, or answer to write when resuming from a user handoff"
)


def _normalize_cli_user_input(user_input: Any) -> Optional[str]:
    """Return stripped CLI user input, or None when unset or invoked outside Typer."""
    if not isinstance(user_input, str):
        return None
    stripped = user_input.strip()
    return stripped if stripped else None


def _build_initial_step_user_inputs(
    playbook_data: Dict[str, Any],
    user_input: Optional[str],
) -> Optional[Dict[str, str]]:
    """Map cold-start --user-input to the playbook entry point step."""
    normalized = _normalize_cli_user_input(user_input)
    if not normalized:
        return None
    entry_point = playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))
    return {str(entry_point): normalized}


def _resolve_initial_step_user_inputs(
    playbook_data: Dict[str, Any],
    user_input: Optional[str],
    start_step: Optional[str],
    resume_current_step: Optional[str],
) -> tuple[Optional[Dict[str, str]], Optional[str]]:
    """Decide where --user-input goes; return (step_user_inputs, remaining_user_input).

    With an explicit --start-step, the input belongs to that step and is consumed
    here. Otherwise, a blackboard parked at a user handoff defers the input to the
    user-handoff resume branch; a cold start maps it to the entry point.
    """
    if user_input and start_step and start_step in playbook_data["steps"]:
        return {str(start_step): user_input}, None
    if user_input and resume_current_step in {"user", "done"}:
        return None, user_input
    return _build_initial_step_user_inputs(playbook_data, user_input), user_input


def _validate_allowed_directories(config_manager: Any, add_dir: List[str]) -> None:
    """Validate config.yaml and CLI-provided allowed directories."""
    configured = config_manager.get_allowed_directories()
    if not isinstance(configured, list):
        configured = []
    cli_dirs = add_dir if isinstance(add_dir, list) else []
    requested = list(dict.fromkeys([*configured, *cli_dirs]))
    validate_directories_exist(requested, Path.cwd())


def set_runtime(runtime_globals: Dict[str, Any]) -> None:
    """No-op retained for backward compatibility.

    Runtime dependencies are now imported directly from ``cafe.ui.cli_shared``.
    """


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
        help=_USER_INPUT_HELP,
    ),
    timeout: int = typer.Option(
        0,
        "--timeout",
        "-t",
        help="Overall workflow timeout in seconds (0 = no timeout)",
    ),
    fallback_preset: Optional[str] = typer.Option(
        None,
        "--fallback-preset",
        help="Crew preset to switch to when primary CLI is rate-limited, missing, unavailable, or has a bad model",
    ),
    add_dir: List[str] = typer.Option(
        [],
        "--add-dir",
        help="Additional allowed directories (can be specified multiple times)",
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
    add_dir_values = add_dir if isinstance(add_dir, list) else []
    config_manager = _get_ConfigManager()(Path(config_file).parent)
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

    try:
        _validate_allowed_directories(config_manager, add_dir_values)
    except ConfigError as e:
        console.print(f"[red]Error: {e}[/red]")
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
    if fallback_preset:
        cmd.extend(["--fallback-preset", fallback_preset])
    for directory in add_dir_values:
        cmd.extend(["--add-dir", directory])

    # Execute the command
    timeout_sec = timeout if timeout > 0 else None
    try:
        result = subprocess.run(cmd, check=False, timeout=timeout_sec)
        if result.returncode != 0:
            # Error already printed by spec phase command, just exit
            raise typer.Exit(result.returncode)
    except subprocess.TimeoutExpired:
        console.print(f"[red]Workflow timed out after {timeout}s[/red]")
        console.print("[dim]The workflow was interrupted. Run 'cafe make' again to resume.[/dim]")
        raise typer.Exit(2)
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
        git_ops = _get_GitOperations()()
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
            if content_type == "status":
                from cafe.services.summary_service import SummaryService

                status = SummaryService(issues_root=cafe_dir / "issues").load_phase_status(issue_name, phase_name)
                if status:
                    console.print_json(data=status)
                    return
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


def status() -> None:
    """Display a comprehensive timeline of all workflow phases and iterations.

    Shows the start time, end time, duration, and current status for each phase
    and iteration in the current issue's workflow.

    \b
    Examples:
        cafe status
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

        context_packets = display.format_context_packets(service.load_context_packets(issue_name))
        if context_packets:
            console.print(context_packets)

        # Display aggregated model token usage summary
        display.render_model_summary_table(entries)

    except Exception as e:
        console.print(f"[red]Error: Failed to display summary: {e}[/red]")
        raise typer.Exit(1)


def summary() -> None:
    """Backward-compatible alias for the previous `cafe summary` command."""
    status()


def _is_baton_contract_error(error: Exception) -> bool:
    message = str(error)
    return "Invalid baton contract payload" in message or "Baton file is empty" in message


def _print_baton_contract_recovery_guidance(
    *,
    issue_dir: Optional[Path],
    playbook_name: Optional[str],
) -> None:
    baton_path = (
        issue_dir / "next_step.txt"
        if issue_dir is not None
        else Path(".cafe/issues/<issue>/next_step.txt")
    )
    playbook_arg = f" --playbook {playbook_name}" if playbook_name else ""
    console.print(
        f"[yellow]Workflow baton file is not a valid handoff contract: {baton_path}[/yellow]"
    )
    console.print(
        "[dim]If you know the step to resume, run "
        f"`cafe workflow{playbook_arg} --execute --start-step <step>`; "
        "the command will rebuild the baton for that step.[/dim]"
    )
    console.print(
        "[dim]If you are not sure, inspect `blackboard.json` for `current_step` first.[/dim]"
    )


def _reset_baton_for_explicit_start_step(
    *,
    issue_dir: Path,
    blackboard: object,
    active_step: str,
) -> None:
    """Make an explicit --start-step runnable even when the persisted baton is stale."""
    store = BlackboardStore(issue_dir)
    store.set_current_step(blackboard, active_step)
    store.set_handoff_summary(
        blackboard,
        (
            f"Explicit workflow start requested for {active_step}; "
            "the prior handoff is superseded."
        ),
    )
    store.update_handoff_contract(
        blackboard,
        from_step=active_step,
        to_owner=HandoffOwner.AGENT,
        to_step=active_step,
        intent=HandoffIntent.AWAIT_AGENT,
        source="workflow.start_step",
    )


def _print_workflow_event_display(event: Any) -> None:
    """Render generic user-facing event display without coupling to event type."""
    if not isinstance(event, dict):
        return

    display = event.get("display")
    style = None
    lines: List[str] = []
    if isinstance(display, str):
        lines = display.splitlines()
    elif isinstance(display, dict):
        raw_style = display.get("style")
        style = raw_style if isinstance(raw_style, str) and raw_style else None
        raw_lines = display.get("lines")
        if isinstance(raw_lines, list):
            lines = [str(line) for line in raw_lines]
        else:
            raw_message = display.get("message")
            if isinstance(raw_message, str):
                lines = raw_message.splitlines()

    raw_message = event.get("display_message")
    if not lines and isinstance(raw_message, str):
        lines = raw_message.splitlines()

    for index, line in enumerate(lines):
        console.print(line, style=style if index == 0 else None)


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
        help=_USER_INPUT_HELP,
    ),
    fallback_preset: Optional[str] = typer.Option(
        None,
        "--fallback-preset",
        help="Crew preset to switch to when primary CLI is rate-limited, missing, unavailable, or has a bad model",
    ),
    add_dir: List[str] = typer.Option(
        [],
        "--add-dir",
        help="Additional allowed directories (can be specified multiple times)",
    ),
) -> None:
    """Run playbook workflow using the new generic runner."""
    user_input = _normalize_cli_user_input(user_input)
    try:
        def _predict_next_iteration(issue_root: Path, step_name: str) -> int:
            step_dir = issue_root / step_name
            iter_dirs = sorted(d for d in step_dir.glob("iteration_*") if d.is_dir())
            existing = [
                d for d in iter_dirs
                if (d / "iteration.json").exists() or (d / "context.json").exists()
            ]
            if not existing:
                return 1
            count = len(existing)
            try:
                import json as _json
                last_dir = existing[-1]
                last_file = (
                    last_dir / "iteration.json"
                    if (last_dir / "iteration.json").exists()
                    else last_dir / "context.json"
                )
                last_data = _json.loads(last_file.read_text(encoding="utf-8"))
                if not last_data.get("status_code"):
                    return last_data.get("iteration", count)
            except Exception:
                return count
            return count + 1

        git = _get_GitOperations()()
        cafe_dir = Path(".cafe")
        try:
            resolved = resolve_active_issue(
                cafe_dir=cafe_dir,
                git_ops=git,
                explicit_issue=issue,
            )
        except ActiveIssueResolutionError as exc:
            console.print(f"[red]Error: {exc.message}[/red]")
            console.print(f"[dim]{exc.guidance}[/dim]")
            raise typer.Exit(1)
        issue_name = resolved.issue_name
        issue_dir = cafe_dir / "issues" / issue_name
        # An explicit flag starts a requested playbook; otherwise an existing
        # issue must resume the playbook persisted in its own workflow state.
        has_issue_workflow = (issue_dir / "blackboard.json").exists() or (
            issue_dir / "issue.yaml"
        ).exists()
        selected_playbook = (
            _resolve_selected_playbook(playbook)
            if playbook
            else (
                _resolve_issue_playbook_name(issue_name)
                if has_issue_workflow
                else _resolve_selected_playbook(None)
            )
        )
        config_manager = _get_ConfigManager()(".cafe")
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()
        add_dir_values = add_dir if isinstance(add_dir, list) else []
        try:
            _validate_allowed_directories(config_manager, add_dir_values)
        except ConfigError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

        playbook_loader = PlaybookLoader()
        playbook_data = playbook_loader.load(selected_playbook)
        interactive = sys.stdin.isatty() or os.getenv("CAFE_FORCE_INTERACTIVE") == "1"
        generic_phase = GenericPhase(SkillLoader())

        def dry_executor(
            step_name: str,
            step_def: Dict,
            blackboard_state: object,
            extra_prompt: Optional[str] = None,
            same_invocation_retry: bool = False,
        ) -> StepExecutionResult:
            output_key = step_def.get("output_artifact", step_name)
            output_path = issue_dir / step_name / "output.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"# {step_name}\n\nDry-run output\n", encoding="utf-8")
            if resolve_step_behavior(playbook_data, step_name).publish_confirmation:
                store = BlackboardStore(issue_dir)
                blackboard = store.load_or_create(
                    str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
                    playbook_id=str(playbook_data["playbook"]["id"]),
                )
                store.update_handoff_contract(
                    blackboard,
                    from_step=step_name,
                    to_owner=HandoffOwner.DONE,
                    to_step="done",
                    intent=HandoffIntent.WORKFLOW_COMPLETE,
                    source="workflow.dry_run",
                )
                return StepExecutionResult(
                    response="dry run",
                    artifacts={str(output_key): str(output_path)},
                    events=[
                        {
                            "type": "pr_synced",
                            "url": "https://example.com/dry-run-pr",
                            "display": {
                                "style": "green",
                                "lines": ["PR synced", "  URL: https://example.com/dry-run-pr"],
                            },
                        }
                    ],
                )
            return StepExecutionResult(
                response="dry-run",
                artifacts={str(output_key): str(output_path)},
                status_code="confirmed",
            )
        entry_point = str(
            playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))
        )
        resume_blackboard = BlackboardStore(issue_dir).load_or_create(
            entry_point,
            playbook_id=str(playbook_data["playbook"]["id"]),
        )
        initial_step_user_inputs, user_input = _resolve_initial_step_user_inputs(
            playbook_data,
            user_input,
            start_step,
            resume_blackboard.current_step,
        )

        # Mutable holder so wrapped_executor can swap executors on fallback
        _executor_holder: Dict[str, Any] = {
            "executor": None if dry_run else _build_workflow_step_executor(
                config_manager=config_manager,
                issue_dir=issue_dir,
                issue_name=issue_name,
                playbook_data=playbook_data,
                generic_phase=generic_phase,
                step_user_inputs=initial_step_user_inputs,
                interactive=interactive,
                extra_allowed_directories=add_dir_values,
            ),
            "fallback_applied": False,
        }

        def _apply_fallback_preset_and_rebuild() -> None:
            """Apply fallback preset and rebuild the step executor in-place."""
            from cafe.utils.preset import PresetManager, PresetNotFoundError
            assert fallback_preset is not None
            try:
                PresetManager().apply(fallback_preset)
                console.print(f"[yellow]⚡ Switched to fallback preset '{fallback_preset}' — remaining steps will use this crew.[/yellow]")
            except PresetNotFoundError as e:
                console.print(f"[red]Fallback preset error: {e}[/red]")
                raise
            _executor_holder["executor"] = _build_workflow_step_executor(
                config_manager=config_manager,
                issue_dir=issue_dir,
                issue_name=issue_name,
                playbook_data=playbook_data,
                generic_phase=generic_phase,
                step_user_inputs=initial_step_user_inputs,
                interactive=interactive,
                extra_allowed_directories=add_dir_values,
            )
            _executor_holder["fallback_applied"] = True

        def wrapped_executor(
            step_name: str,
            step_def: Dict,
            blackboard_state: object,
            extra_prompt: Optional[str] = None,
            same_invocation_retry: bool = False,
        ) -> Any:
            iteration = _predict_next_iteration(issue_dir, step_name)
            console.print(f"[dim]Executing[/dim] step={step_name} iteration={iteration:03d}")
            if dry_run:
                return dry_executor(
                    step_name,
                    step_def,
                    blackboard_state,
                    extra_prompt=extra_prompt,
                    same_invocation_retry=same_invocation_retry,
                )
            step_role = step_def.get("role") if isinstance(step_def, dict) else None
            missing_clis = _check_agent_clis_available(
                config_manager,
                active_step=step_name,
                active_role=step_role if isinstance(step_role, str) else None,
            )
            if missing_clis:
                console.print(
                    f"[red]Error: No executable CLI candidates for step={step_name} field=clis[/red]"
                )
                for cli in missing_clis:
                    console.print(f"  [red]✗[/red] {cli}")
                raise typer.Exit(1)
            step_executor = _executor_holder["executor"]
            assert step_executor is not None
            execute_kwargs = {"extra_prompt": extra_prompt}
            execute_signature = inspect.signature(step_executor.execute_step)
            if "same_invocation_retry" in execute_signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in execute_signature.parameters.values()
            ):
                execute_kwargs["same_invocation_retry"] = same_invocation_retry
            try:
                result = step_executor.execute_step(
                    step_name,
                    step_def,
                    blackboard_state,
                    **execute_kwargs,
                )
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not _executor_holder["fallback_applied"]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found", "cli_unavailable", "model_not_found")
                ):
                    _apply_fallback_preset_and_rebuild()
                    fallback_executor = _executor_holder["executor"]
                    fallback_kwargs = {"extra_prompt": extra_prompt}
                    fallback_signature = inspect.signature(fallback_executor.execute_step)
                    if "same_invocation_retry" in fallback_signature.parameters or any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in fallback_signature.parameters.values()
                    ):
                        fallback_kwargs["same_invocation_retry"] = same_invocation_retry
                    result = fallback_executor.execute_step(
                        step_name,
                        step_def,
                        blackboard_state,
                        **fallback_kwargs,
                    )
                else:
                    raise
            if isinstance(result, StepExecutionResult):
                for event in result.events:
                    _print_workflow_event_display(event)
            return result

        pending_start_step = start_step
        explicit_start_step_pending = start_step is not None
        while True:
            has_explicit_start_step = explicit_start_step_pending
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
            if not dry_run and has_explicit_start_step:
                if active_step not in {"user", "done"}:
                    _reset_baton_for_explicit_start_step(
                        issue_dir=issue_dir,
                        blackboard=blackboard,
                        active_step=active_step,
                    )
                explicit_start_step_pending = False
            if not dry_run and active_step in {"user", "done"}:
                handoff_contract = getattr(blackboard, "handoff_contract", None)
                waiting_for_alignment = (
                    handoff_contract is not None
                    and handoff_contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT
                )
                incomplete_step = None
                if not waiting_for_alignment:
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
                external_step = None
                if not waiting_for_alignment:
                    external_step = _find_external_resume_step(
                        issue_dir=issue_dir,
                        playbook_data=playbook_data,
                        git_ops=git,
                    )
                if external_step is not None:
                    pending_start_step = external_step
                    store = BlackboardStore(issue_dir)
                    store.set_current_step(blackboard, external_step)
                    store.set_handoff_summary(
                        blackboard,
                        "Unresolved PR discussion detected while the workflow was paused; resuming the PR step.",
                    )
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
                if active_step == "done" and not interactive:
                    console.print("[green]Workflow already completed[/green] step=done")
                    console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
                    return
                # active_step in {"user", "done"} (done only reaches here in interactive mode)
                if not interactive:
                    if user_input and user_input.strip():
                        step_keys = list(playbook_data.get("steps", {}).keys())
                        contract = BlackboardStore(issue_dir).load_handoff_contract(
                            blackboard,
                            allowed_steps=step_keys,
                        )
                        from_step = getattr(contract, "from_step", None) or blackboard.current_step
                        if contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT:
                            decision_payload = parse_alignment_decision_payload(user_input)
                            if decision_payload is None:
                                console.print(
                                    "[yellow]Workflow is waiting for an explicit alignment decision payload.[/yellow]"
                                )
                                console.print(
                                    "[dim]Use JSON such as {\"decision\":\"approve\"}, "
                                    "{\"decision\":\"narrow\",\"correction\":\"...\"}, "
                                    "or {\"decision\":\"update_strategic_documents_first\"}.[/dim]"
                                )
                                return
                            selected_step = apply_alignment_decision_from_payload(
                                issue_dir=issue_dir,
                                playbook_data=playbook_data,
                                blackboard=blackboard,
                                payload=decision_payload,
                            )
                            user_input = None
                            if selected_step:
                                pending_start_step = selected_step
                                continue
                            return
                        if contract.intent in {
                            HandoffIntent.CONFIRM_OUTPUT,
                            HandoffIntent.NEED_CLARIFICATION,
                            HandoffIntent.NO_CHANGES_NEEDED,
                        }:
                            result = apply_human_task_payload(
                                issue_dir=issue_dir,
                                playbook_data=playbook_data,
                                blackboard=blackboard,
                                from_step=from_step,
                                trigger=contract.intent.value,
                                raw_payload=user_input,
                                source="command",
                            )
                            if result.rejection is not None:
                                console.print(f"[yellow]{result.rejection.message}[/yellow]")
                                console.print(
                                    f"[dim]{result.rejection.correction_guidance}[/dim]"
                                )
                                return
                            user_input = None
                            pending_start_step = result.target
                            continue
                        store = BlackboardStore(issue_dir)
                        from_step_dir = issue_dir / from_step
                        iteration_dirs = sorted(from_step_dir.glob("iteration_*")) if from_step_dir.exists() else []
                        next_iteration_num = len(iteration_dirs) + 1
                        next_iteration_dir = from_step_dir / f"iteration_{next_iteration_num:03d}"
                        next_iteration_dir.mkdir(parents=True, exist_ok=True)
                        (next_iteration_dir / "user_input.md").write_text(user_input, encoding="utf-8")
                        store.set_current_step(blackboard, from_step)
                        store.set_handoff_summary(
                            blackboard,
                            f"User input provided via --user-input, resuming {from_step}",
                        )
                        store.update_handoff_contract(
                            blackboard,
                            from_step=from_step,
                            to_owner=HandoffOwner.AGENT,
                            to_step=from_step,
                            intent=HandoffIntent.AWAIT_AGENT,
                            source="workflow.user_input_flag",
                        )
                        console.print(
                            f"[dim]Resuming[/dim] {from_step} with --user-input"
                        )
                        # Consume user_input so it is not replayed on
                        # subsequent user handoffs for different steps.
                        user_input = None
                        pending_start_step = from_step
                        continue
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
            result = runner.run(start_step=pending_start_step, single_step=single_step)
            latest_blackboard = BlackboardStore(issue_dir).load_or_create(
                str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
                playbook_id=str(playbook_data["playbook"]["id"]),
            )
            if (
                not single_step
                and result.final_status_code != "BATON_POSITION_REALIGNED"
                and latest_blackboard.current_step in playbook_data["steps"]
                and resolve_step_behavior(
                    playbook_data, latest_blackboard.current_step
                ).publish_confirmation
                and effective_start_step != latest_blackboard.current_step
            ):
                pending_start_step = latest_blackboard.current_step
                continue
            if interactive and not dry_run and not single_step and latest_blackboard.current_step == "user":
                pending_start_step = "user"
                continue
            if not interactive and not dry_run and not single_step and latest_blackboard.current_step == "user":
                if user_input and user_input.strip():
                    pending_start_step = "user"
                    continue
                console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
                return
            if result.completed:
                console.print(
                    f"[green]Workflow completed[/green] step={result.final_step} status={result.final_status_code} next={latest_blackboard.current_step}"
                )
            elif result.final_status_code.startswith("INTERRUPTED"):
                reason = result.final_status_code.split(":", 1)[1] if ":" in result.final_status_code else "interrupted"
                console.print(
                    f"[yellow]Workflow interrupted[/yellow] step={result.final_step} reason={reason}"
                )
                if result.detail:
                    console.print(f"[red]{result.detail.rstrip()}[/red]")
                if reason.startswith("agent_"):
                    console.print(
                        "[dim]Agent execution failed. Switch to a different CLI in your config, then run 'cafe make' again.[/dim]"
                    )
                elif reason == "publish_error":
                    console.print(
                        "[dim]PR publish failed after the agent completed. Fix the publish error, then run 'cafe make' again.[/dim]"
                    )
                else:
                    console.print(
                        "[dim]Run 'cafe make' again to resume from this step.[/dim]"
                    )
                return
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
        if _is_baton_contract_error(e):
            _print_baton_contract_recovery_guidance(
                issue_dir=locals().get("issue_dir"),
                playbook_name=locals().get("selected_playbook"),
            )
        console.print(f"[red]Error: workflow run failed: {e}[/red]")
        raise typer.Exit(1)
