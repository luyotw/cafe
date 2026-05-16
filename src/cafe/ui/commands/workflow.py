"""Workflow-related command implementations extracted from cli.py."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.core.types import CriticalPhaseError
from cafe.core.workflow_models import StepExecutionResult, StepInterrupted
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.cli_shared import (
    VALID_CONTENT_TYPES,
    get_show_file_path as _get_show_file_path,
    resolve_iteration_number as _resolve_iteration_number,
)
from cafe.agents.executor import AgentExecutionError
from cafe.utils.config import ConfigError

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

console = Console()


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
        help="Initial requirements to pass into the first spec step",
    ),
    auto_advance: bool = typer.Option(
        False,
        "--auto-advance",
        help="Auto-confirm user handoffs and advance without interactive prompts",
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
        help="Crew preset to switch to when primary CLI hits rate_limit or cli_not_found",
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

    # All CLIs available, execute cafe workflow --execute
    console.print("[green]✓ All agent CLI tools are installed[/green]")
    console.print()
    console.print("[bold cyan]🚀 Starting automated workflow...[/bold cyan]")
    console.print()

    # Build command
    cmd = [sys.executable, "-m", "cafe.ui.cli", "workflow", "--execute"]
    if user_input:
        cmd.extend(["--user-input", user_input])
    if auto_advance:
        cmd.append("--auto-advance")
    if fallback_preset:
        cmd.extend(["--fallback-preset", fallback_preset])

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
    store.update_handoff_contract(
        blackboard,
        from_step=active_step,
        to_owner=HandoffOwner.AGENT,
        to_step=active_step,
        intent=HandoffIntent.AWAIT_AGENT,
        source="workflow.start_step",
    )


def workflow(
    playbook: Optional[str] = typer.Option(None, "--playbook", help="Playbook name"),
    issue: Optional[str] = typer.Option(None, "--issue", help="Issue directory name"),
    start_step: Optional[str] = typer.Option(None, "--start-step", help="Start execution from a specific step"),
    single_step: bool = typer.Option(False, "--single-step", help="Run only one playbook step"),
    auto_advance: bool = typer.Option(False, "--auto-advance", help="Auto-confirm user handoffs and advance without interactive prompts"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Run with built-in dry executor"),
    user_input: Optional[str] = typer.Option(
        None,
        "--user-input",
        "-u",
        help="Initial requirements to pass into the first spec step",
    ),
    fallback_preset: Optional[str] = typer.Option(
        None,
        "--fallback-preset",
        help="Crew preset to switch to when primary CLI hits rate_limit or cli_not_found",
    ),
) -> None:
    """Run playbook workflow using the new generic runner."""
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
        issue_name = issue or git.get_current_branch()
        issue_dir = Path(".cafe/issues") / issue_name
        selected_playbook = _resolve_selected_playbook(playbook)
        config_manager = _get_ConfigManager()(".cafe")
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()

        playbook_loader = PlaybookLoader()
        playbook_data = playbook_loader.load(selected_playbook)
        interactive = (sys.stdin.isatty() or os.getenv("CAFE_FORCE_INTERACTIVE") == "1") and not auto_advance
        generic_phase = GenericPhase(SkillLoader())

        def dry_executor(step_name: str, step_def: Dict, blackboard_state: object, extra_prompt: Optional[str] = None) -> StepExecutionResult:
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
                status_code="confirmed",
            )
        # Mutable holder so wrapped_executor can swap executors on fallback
        _executor_holder: Dict[str, Any] = {
            "executor": None if dry_run else _build_workflow_step_executor(
                config_manager=config_manager,
                issue_dir=issue_dir,
                issue_name=issue_name,
                playbook_data=playbook_data,
                generic_phase=generic_phase,
                step_user_inputs={"spec": user_input} if user_input else None,
                interactive=interactive,
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
                step_user_inputs={"spec": user_input} if user_input else None,
                interactive=interactive,
            )
            _executor_holder["fallback_applied"] = True

        def wrapped_executor(step_name: str, step_def: Dict, blackboard_state: object, extra_prompt: Optional[str] = None) -> Any:
            iteration = _predict_next_iteration(issue_dir, step_name)
            console.print(f"[dim]Executing[/dim] step={step_name} iteration={iteration:03d}")
            if dry_run:
                return dry_executor(step_name, step_def, blackboard_state, extra_prompt=extra_prompt)
            step_executor = _executor_holder["executor"]
            assert step_executor is not None
            try:
                result = step_executor.execute_step(step_name, step_def, blackboard_state, extra_prompt=extra_prompt)
            except AgentExecutionError as exc:
                if (
                    fallback_preset
                    and not _executor_holder["fallback_applied"]
                    and getattr(exc, "error_type", None) in ("rate_limit", "cli_not_found")
                ):
                    _apply_fallback_preset_and_rebuild()
                    result = _executor_holder["executor"].execute_step(
                        step_name, step_def, blackboard_state, extra_prompt=extra_prompt
                    )
                else:
                    raise
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
            has_explicit_start_step = pending_start_step is not None
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
                allow_legacy_text=True,
            )

            active_step = pending_start_step or blackboard.current_step
            if not dry_run and has_explicit_start_step and active_step not in {"user", "done"}:
                _reset_baton_for_explicit_start_step(
                    issue_dir=issue_dir,
                    blackboard=blackboard,
                    active_step=active_step,
                )
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
                    if auto_advance:
                        # Smart auto-advance: read the handoff contract to
                        # understand why we paused at user, then either answer
                        # pending questions or confirm the output, and resume
                        # the originating step so it can process the input.
                        step_keys = list(playbook_data.get("steps", {}).keys())
                        contract = BlackboardStore(issue_dir).load_handoff_contract(
                            blackboard,
                            allowed_steps=step_keys,
                        )
                        from_step = getattr(contract, "from_step", None) or blackboard.current_step
                        handoff_intent = getattr(contract, "intent", None)
                        intent_value = handoff_intent.value if handoff_intent else ""

                        store = BlackboardStore(issue_dir)

                        # Determine the originating step's latest iteration
                        # directory so we can locate questions.xml.
                        from_step_dir = issue_dir / from_step
                        iteration_dirs = sorted(from_step_dir.glob("iteration_*")) if from_step_dir.exists() else []
                        latest_iteration = iteration_dirs[-1] if iteration_dirs else None

                        # Build auto-answer text from questions.xml if present.
                        auto_answer = ""
                        if latest_iteration is not None:
                            questions_xml_path = latest_iteration / "questions.xml"
                            if questions_xml_path.exists() and validate_questions_xml(questions_xml_path):
                                questions = parse_questions_xml(questions_xml_path)
                                answer_lines = []
                                for q in questions:
                                    if q.multi_select:
                                        # Select all options for checkbox questions.
                                        selected = q.options
                                        answer_lines.append(f"Q: {q.title}")
                                        answer_lines.append(f"A: {'; '.join(selected)}")
                                    else:
                                        # Select first option for single-select questions.
                                        selected = q.options[0] if q.options else "(auto-confirmed)"
                                        answer_lines.append(f"Q: {q.title}")
                                        answer_lines.append(f"A: {selected}")
                                auto_answer = "\n".join(answer_lines)

                        # If no questions.xml, generate a generic confirmation.
                        if not auto_answer:
                            if intent_value == "confirm_output":
                                auto_answer = "Confirmed. Proceed to the next step."
                            elif intent_value == "need_clarification":
                                auto_answer = "No additional clarification needed. Proceed as proposed."
                            else:
                                auto_answer = "Auto-confirmed. Proceed."

                        # Write the auto-answer into the next iteration's
                        # user_input.md so the UserInputCollector hook can
                        # pick it up when the step re-executes.
                        next_iteration_num = len(iteration_dirs) + 1
                        next_iteration_dir = from_step_dir / f"iteration_{next_iteration_num:03d}"
                        next_iteration_dir.mkdir(parents=True, exist_ok=True)
                        user_input_file = next_iteration_dir / "user_input.md"
                        user_input_file.write_text(auto_answer, encoding="utf-8")

                        # Hand the baton back to the originating step so it
                        # can run its next iteration with the auto-answer.
                        store.set_current_step(blackboard, from_step)
                        store.set_handoff_summary(
                            blackboard,
                            f"Auto-advanced: answered user handoff from {from_step} (intent={intent_value})",
                        )
                        store.update_handoff_contract(
                            blackboard,
                            from_step=from_step,
                            to_owner=HandoffOwner.AGENT,
                            to_step=from_step,
                            intent=HandoffIntent.AWAIT_AGENT,
                            source="workflow.auto_advance",
                        )
                        store.record_event(
                            blackboard,
                            "auto_advance",
                            {"from_phase": from_step, "intent": intent_value, "auto_answered": True},
                        )
                        console.print(f"[dim]Auto-advancing[/dim] user handoff from {from_step} (intent={intent_value}) → re-run {from_step} with auto-answer")
                        pending_start_step = from_step
                        continue
                    else:
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
                if auto_advance:
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
                if reason.startswith("agent_"):
                    console.print(
                        "[dim]Agent execution failed. Switch to a different CLI in your config, then run 'cafe make' again.[/dim]"
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
