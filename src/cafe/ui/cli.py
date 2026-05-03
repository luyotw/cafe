"""Command-line interface for CAFE."""

import copy
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import click
from typer.core import TyperGroup

from cafe.ui.inquirer_prompts import prompt_checkbox, prompt_confirm, prompt_list, prompt_multiline, prompt_text
from cafe.ui.menu import InteractiveMenu
from cafe.ui.commands import lifecycle as lifecycle_commands
from cafe.ui.commands import phases_legacy as phases_legacy_commands
from cafe.ui.commands import issues as issues_commands
from cafe.ui.commands import workflow as workflow_commands
from cafe.ui.cli_shared import (
    CONTENT_TYPE_FILE_MAP,
    VALID_CONTENT_TYPES,
    check_agent_clis_available as _shared_check_agent_clis_available,
    display_iteration_delta as _shared_display_iteration_delta,
    find_latest_iteration_dir as _shared_find_latest_iteration_dir,
    get_latest_review_iteration as _shared_get_latest_review_iteration,
    get_latest_versioned_file as _shared_get_latest_versioned_file,
    get_show_file_path as _shared_get_show_file_path,
    resolve_iteration_index as _shared_resolve_iteration_index,
    resolve_iteration_number as _shared_resolve_iteration_number,
    setup_agents as _shared_setup_agents,
)
import yaml
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.git import GitOperations
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.permission import PermissionHandler
from cafe.core.types import CriticalPhaseError
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.importer import SkillImportSummary, import_skills, preview_importable_skills
from cafe.skills.loader import SkillLoader
from cafe.skills.remover import SkillRemoveSummary, remove_skills
from cafe.templates.manager import TemplateManager
from cafe.ui import init_helpers
from cafe.ui.chat import get_chat_next_step_path, launch_chat_session
from cafe.ui.display import Display
from cafe.ui.init_helpers import (
    check_available_clis,
    copy_agents_to_local,
    copy_templates_to_local,
    list_available_agents,
)
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor
from cafe.ui.template_selector import select_template
from cafe.utils.config import ConfigManager, ConfigError
from cafe.utils.git_utils import is_branch_initialized
from cafe.utils.github import (
    GitHubError,
    GitHubOps,
    filter_unresolved_comments,
    get_all_pr_comments,
    get_processed_comment_ids_from_history,
)


def _resolve_runtime_playbook_name() -> str:
    """Resolve runtime playbook from current issue state or config."""
    try:
        issue_name = GitOperations().get_current_branch()
    except Exception:
        issue_name = None

    if issue_name:
        issue_playbook = _resolve_issue_playbook_name(issue_name)
        blackboard_path = Path.cwd() / ".cafe" / "issues" / issue_name / "blackboard.json"
        if issue_playbook != "default" or blackboard_path.exists():
            return issue_playbook
    return _resolve_selected_playbook(None)


def _find_repo_checkout_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the current checkout root when running inside the cafe repo."""
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        pyproject = candidate / "pyproject.toml"
        repo_cli = candidate / "src" / "cafe" / "ui" / "cli.py"
        if not pyproject.exists() or not repo_cli.exists():
            continue
        try:
            content = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        if 'name = "cafe-engine"' in content:
            return candidate
    return None


def _build_repo_entrypoint_mismatch_message(
    *,
    cwd: Optional[Path] = None,
    imported_cli_file: Optional[Path] = None,
) -> Optional[str]:
    """Describe a repo/install mismatch when the CLI is not loaded from this checkout."""
    repo_root = _find_repo_checkout_root(cwd)
    if repo_root is None:
        return None

    expected_cli = (repo_root / "src" / "cafe" / "ui" / "cli.py").resolve()
    actual_cli = (imported_cli_file or Path(__file__)).resolve()
    if actual_cli == expected_cli:
        return None

    python_bin = Path(sys.executable).resolve()
    return textwrap.dedent(
        f"""
        Error: `cafe` is running from a different installation than this checkout.

          checkout: {repo_root}
          expected CLI: {expected_cli}
          actual CLI:   {actual_cli}

        This usually means the `cafe` command is pointing at an older/global install,
        so commands added in this checkout will not appear.

        Fix one of these before continuing:
          1. Reinstall this checkout into the same interpreter:
             {python_bin} -m pip install -e .
          2. Or run the checkout directly:
             PYTHONPATH=src {python_bin} -m cafe.ui.cli <command>
        """
    ).strip()


def _check_repo_entrypoint_alignment() -> None:
    """Fail fast when running inside a checkout but importing a different install."""
    if os.getenv("CAFE_SKIP_ENTRYPOINT_CHECK"):
        return
    message = _build_repo_entrypoint_mismatch_message()
    if message is None:
        return
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _build_dynamic_step_click_command(step_name: str) -> Optional[click.Command]:
    """Build a dynamic CLI command for one playbook step."""
    playbook_name = _resolve_runtime_playbook_name()
    step_names = _load_playbook_step_names(playbook_name)
    if step_name not in step_names:
        return None

    @click.command(name=step_name, help=f"Run playbook step '{step_name}' via workflow runtime.")
    def _dynamic_step_command() -> None:
        workflow_commands.workflow(
            playbook=playbook_name,
            issue=None,
            start_step=step_name,
            single_step=True,
            dry_run=False,
        )

    return _dynamic_step_command


class DynamicStepTyperGroup(TyperGroup):
    """Typer group that resolves playbook-defined step commands on demand."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        if cmd_name == "dev":
            return None
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command
        return _build_dynamic_step_click_command(cmd_name)

    def list_commands(self, ctx: click.Context) -> List[str]:
        commands = [command for command in super().list_commands(ctx) if command != "dev"]
        for step_name in _load_playbook_step_names(_resolve_runtime_playbook_name()):
            if step_name not in commands:
                commands.append(step_name)
        return sorted(commands)

app = typer.Typer(
    name="cafe",
    cls=DynamicStepTyperGroup,
    help="AI Agent Flow - Automated development workflow with AI agents",
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


@app.callback(invoke_without_command=True)
def _menu_callback(ctx: typer.Context) -> None:
    """Launch interactive menu when no subcommand is provided."""
    _sync_lifecycle_runtime()
    if ctx.invoked_subcommand is None:
        try:
            InteractiveMenu().run()
        except KeyboardInterrupt:
            pass

# List of all phases in order
ALL_PHASES = ["spec", "plan", "develop", "review", "pr"]

# Constants for cafe show command
VALID_PHASES = ["spec", "plan", "develop", "review", "pr"]


def _resolve_issue_playbook_name(issue_name: str) -> str:
    """Resolve the playbook id associated with an issue."""
    blackboard_file = Path.cwd() / ".cafe" / "issues" / issue_name / "blackboard.json"
    if not blackboard_file.exists():
        return "default"

    try:
        raw = json.loads(blackboard_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "default"

    playbook_id = raw.get("playbook_id")
    return str(playbook_id) if playbook_id else "default"


def _load_playbook_step_names(playbook_name: str) -> List[str]:
    """Load ordered step names from a playbook."""
    try:
        playbook = PlaybookLoader().load(playbook_name)
        return list(playbook["steps"].keys())
    except Exception:
        return list(ALL_PHASES)


def _load_issue_step_names(issue_name: str) -> List[str]:
    """Load ordered step names for the current issue playbook."""
    playbook_name = _resolve_issue_playbook_name(issue_name)
    return _load_playbook_step_names(playbook_name)


def _resolve_selected_playbook(playbook_name: Optional[str]) -> str:
    """Resolve workflow playbook from CLI or config."""
    if playbook_name:
        return playbook_name

    try:
        config_manager = ConfigManager(".cafe")
        try:
            config_manager.load_config()
        except ConfigError:
            config_manager._config = config_manager.get_default_config()
    except ConfigError:
        return "default"

    selected = config_manager.get("playbook", "default")
    return str(selected) if selected else "default"


def _build_playbook_loader() -> PlaybookLoader:
    """Build playbook loader with cwd-based project root."""
    return PlaybookLoader(project_root=Path.cwd())


def _build_skill_loader() -> SkillLoader:
    """Build skill loader with cwd-based project root."""
    return SkillLoader(project_root=Path.cwd())


def _build_workflow_role_agent_map(config_manager: ConfigManager, playbook_data: Dict[str, Any]) -> Dict[str, str]:
    """Resolve playbook roles to configured agent names."""
    mapping: Dict[str, str] = {
        "pm": str(config_manager.get("agents.pm.name", "Roger")),
        "developer": str(config_manager.get("agents.developer.name", "David")),
        "reviewer": str(config_manager.get("agents.reviewer.name", "Richard")),
    }
    for role_name, role_def in playbook_data.get("roles", {}).items():
        if role_name in mapping:
            continue
        if isinstance(role_def, dict) and role_def.get("default_agent"):
            mapping[str(role_name)] = str(role_def["default_agent"])
    return mapping


def _build_workflow_step_executor(
    *,
    config_manager: ConfigManager,
    issue_dir: Path,
    issue_name: str,
    playbook_data: Dict[str, Any],
    generic_phase: GenericPhase,
    phase_name: Optional[str] = None,
    role_agent_map_override: Optional[Dict[str, str]] = None,
    step_user_inputs: Optional[Dict[str, str]] = None,
    interactive: bool = False,
) -> GenericWorkflowStepExecutor:
    """Create the GenericPhase-backed executor for workflow steps."""
    role_agent_map = _build_workflow_role_agent_map(config_manager, playbook_data)
    if role_agent_map_override:
        role_agent_map.update(role_agent_map_override)
    role_configs = {
        "pm": config_manager.get("agents.pm", {}),
        "developer": config_manager.get("agents.developer", {}),
        "reviewer": config_manager.get("agents.reviewer", {}),
    }
    return GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=issue_name,
        playbook=playbook_data,
        generic_phase=generic_phase,
        agent_manager=_setup_agents(config_manager, issue_name=issue_name, phase_name=phase_name),
        git_ops=GitOperations(),
        role_agent_map=role_agent_map,
        role_configs=role_configs,
        step_user_inputs=step_user_inputs,
        interactive=interactive,
    )


def _consume_pending_chat_handoff(
    *,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    requested_start_step: Optional[str],
) -> Optional[str]:
    """Consume a chat-authored next-step baton before workflow execution."""
    if requested_start_step is not None:
        return requested_start_step

    store = BlackboardStore(issue_dir)
    # Do not call load_or_create before this check: it bootstraps next_step.txt via
    # ensure_baton(), which would falsely look like a chat handoff existed.
    if not store.next_step_path.exists():
        return None

    blackboard = store.load_or_create(
        str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
        playbook_id=str(playbook_data["playbook"]["id"]),
    )
    contract = store.load_handoff_contract(
        blackboard,
        allowed_steps=list(playbook_data["steps"].keys()),
        allow_legacy_text=True,
    )

    # `next_step.txt` is now persistent from workflow initialization onward.
    # Ignore the bootstrap/persistent baton itself; only consume a chat-authored
    # pending handoff (or legacy step-name text) when the baton meaning is real.
    if contract.source in {"bootstrap", "chat.bootstrap"}:
        return None

    target_step = contract.to_step
    if target_step not in {"user", "done"} and GitOperations().has_uncommitted_changes():
        console.print(
            "[yellow]Chat handoff was not consumed because the worktree still has uncommitted changes.[/yellow]"
        )
        console.print(
            "[yellow]Commit or stash the chat changes first, then run `cafe make` again.[/yellow]"
        )
        return None

    store.set_current_step(blackboard, target_step)
    if target_step == "done":
        store.update_handoff_contract(
            blackboard,
            from_step=contract.from_step,
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            status_code=contract.status_code,
            source="workflow.consume_handoff",
        )
    elif target_step == "user":
        store.update_handoff_contract(
            blackboard,
            from_step=contract.from_step,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=contract.intent,
            status_code=contract.status_code,
            source="workflow.consume_handoff",
        )
    else:
        store.update_handoff_contract(
            blackboard,
            from_step=contract.from_step,
            to_owner=HandoffOwner.AGENT,
            to_step=target_step,
            intent=HandoffIntent.AWAIT_AGENT,
            status_code=contract.status_code,
            source="workflow.consume_handoff",
        )
    return target_step


def _find_incomplete_workflow_step(*, issue_dir: Path, playbook_data: Dict[str, Any]) -> Optional[str]:
    """Return the most recent workflow step with an unfinished iteration context."""
    latest_incomplete: tuple[float, str] | None = None

    for step_name in playbook_data["steps"].keys():
        step_dir = issue_dir / step_name
        if not step_dir.exists():
            continue

        iteration_dirs = sorted(path for path in step_dir.glob("iteration_*") if path.is_dir())
        if not iteration_dirs:
            continue

        context_file = iteration_dirs[-1] / "context.json"
        if not context_file.exists():
            continue

        try:
            context_data = json.loads(context_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if context_data.get("end_time"):
            continue

        timestamp = context_file.stat().st_mtime
        if latest_incomplete is None or timestamp > latest_incomplete[0]:
            latest_incomplete = (timestamp, step_name)

    return latest_incomplete[1] if latest_incomplete is not None else None


def _find_external_resume_step(
    *,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    git_ops: GitOperations,
) -> Optional[str]:
    """Return a workflow step that should resume due to new external PR feedback.

    This restores the legacy behavior where `cafe make` could auto-resume the PR
    phase after new GitHub PR comments arrived, even when the workflow was
    currently paused at `user` or `done`.
    """
    for step_name, step_def in playbook_data["steps"].items():
        hooks = step_def.get("hooks", {})
        prepare_hooks = hooks.get("prepare_input", [])
        if "GitHubPRCreator" not in prepare_hooks:
            continue

        try:
            branch_name = git_ops.get_current_branch()
        except Exception:
            return None
        if not branch_name:
            return None

        try:
            existing_pr = GitHubOps().get_pr_for_branch(branch_name)
        except Exception:
            return None
        if not existing_pr:
            return None

        try:
            has_unpushed_commits = git_ops.has_unpushed_commits()
        except Exception:
            has_unpushed_commits = False
        if has_unpushed_commits:
            return None

        try:
            exclude_ids = get_processed_comment_ids_from_history(issue_dir / step_name)
            comments = get_all_pr_comments(int(existing_pr["number"]), exclude_ids=exclude_ids)
            unresolved_comments = filter_unresolved_comments(comments)
        except Exception:
            return None

        if unresolved_comments:
            return step_name

    return None


def _handle_user_phase(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    phase_name: str = "user",
) -> Optional[str]:
    phase_labels = {
        "spec": "Update requirements spec",
        "plan": "Revise implementation plan",
        "develop": "Continue implementation",
        "review": "Run review again",
        "pr": "Refresh PR output",
    }
    summary = getattr(blackboard, "handoff_summary", "").strip()
    if phase_name == "done":
        console.print("[green]Workflow already completed[/green] step=done")
        console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
    else:
        console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    action = prompt_list(
        "Select next action",
        [
            "Leave a handoff note and continue the workflow",
            "Open chat with a role",
            "Mark the workflow complete",
            "Leave it for now",
        ],
    )
    store = BlackboardStore(issue_dir)

    if action == "Leave a handoff note and continue the workflow":
        note = prompt_multiline(
            "What should be written to the blackboard before continuing?",
            default=summary,
        ).strip()
        if not note:
            note = "user handed workflow back without additional note"

        step_names = list(playbook_data["steps"].keys())
        step_labels = [
            f"{phase_labels.get(step_name, step_name)} ({step_name})"
            for step_name in step_names
        ]
        default_step = str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys())))
        default_label = f"{phase_labels.get(default_step, default_step)} ({default_step})"
        selected_label = prompt_list(
            "Which phase should continue next?",
            step_labels,
            default=default_label,
        )
        target_step = step_names[step_labels.index(selected_label)]
        console.print()
        console.print("[bold]Handoff summary[/bold]")
        console.print(f"  Next phase: {selected_label}")
        console.print(f"  Note: {note}")
        console.print()
        if not prompt_confirm("Write this handoff to the blackboard and continue now?"):
            console.print("[dim]No workflow action taken.[/dim]")
            return ""
        store.set_current_step(blackboard, target_step)
        store.set_handoff_summary(blackboard, note)
        store.update_handoff_contract(
            blackboard,
            from_step="user",
            to_owner=HandoffOwner.AGENT,
            to_step=target_step,
            intent=HandoffIntent.MANUAL_HANDOFF,
            source="user.phase",
        )
        store.record_event(
            blackboard,
            "user_handoff",
            {"from_phase": "user", "to_step": target_step, "note": note},
        )
        return str(target_step)

    if action == "Open chat with a role":
        role_choices = list(playbook_data.get("roles", {}).keys()) or ["pm", "developer", "reviewer"]
        role = prompt_list("Select role", role_choices)
        launch_chat_session(str(role), issue_name)
        target_step = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step=None,
        )
        if target_step is not None:
            store.set_handoff_summary(
                blackboard,
                f"chat handed workflow to {target_step}",
            )
        return target_step

    if action == "Mark the workflow complete":
        store.set_current_step(blackboard, "done")
        store.set_handoff_summary(blackboard, "workflow completed by user")
        store.update_handoff_contract(
            blackboard,
            from_step="user",
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            source="user.phase",
        )
        store.record_event(
            blackboard,
            "workflow_completed_by_user",
            {"step": "user"},
        )
        console.print("[green]Workflow completed by user[/green]")
        return ""

    console.print("[dim]No workflow action taken.[/dim]")
    return ""


def _handle_phase_exception(e: Exception, phase_name: str) -> None:
    """Unified exception handling for phase execution.

    Args:
        e: Caught exception
        phase_name: Phase name (for error messages)

    Raises:
        typer.Exit: Always raises exit(1)
    """
    from cafe.core.types import CriticalPhaseError

    # typer.Exit propagating up from a subprocess chain — already handled, just re-raise
    if isinstance(e, typer.Exit):
        raise e

    console.print()

    # Check if it's a critical error that should stop the entire workflow
    if isinstance(e, CriticalPhaseError):
        console.print(f"[bold red]❌ Critical error in {phase_name} phase[/bold red]")
        console.print()
        if e.error_type == "rate_limit":
            error_msg = str(e)
            all_agents_exhausted = "All agents failed" in error_msg
            console.print("[yellow]⚠️  API rate limit reached[/yellow]")
            console.print()
            if all_agents_exhausted:
                console.print("[dim]All configured agents (primary + backups) have been exhausted.[/dim]")
                console.print()
                console.print(f"[dim]{error_msg}[/dim]")
            else:
                console.print(f"[dim]{error_msg}[/dim]")
                console.print("[dim]The workflow has been stopped to prevent wasting resources.[/dim]")
            console.print()
            console.print("[bold]Next steps (choose one):[/bold]")
            console.print("  • Wait for quota reset or switch to a different account, OR")
            console.print("  • Use [cyan]cafe config edit[/cyan] to add backup agents or switch CLI tool")
            console.print()
            console.print("Then run [cyan]cafe make[/cyan] again to resume from where it stopped")
            console.print()
        elif e.error_type == "cli_not_found":
            console.print("[yellow]⚠️  Required CLI tool not found. Please install it and try again.[/yellow]")
            console.print()
            console.print("[dim]ℹ️  The workflow has been stopped to prevent wasting resources.[/dim]")
            console.print()
        else:
            console.print(f"[yellow]⚠️  {e}[/yellow]")
            console.print()
            console.print("[dim]ℹ️  The workflow has been stopped to prevent wasting resources.[/dim]")
            console.print()
    else:
        console.print(f"[bold red]❌ Error in {phase_name} phase: {e}[/bold red]")
        console.print()

    raise typer.Exit(1)


def _execute_single_step_alias(
    *,
    issue_name: str,
    step_name: str,
    config_manager: ConfigManager,
    selected_playbook: Optional[str] = None,
    role_agent_map_override: Optional[Dict[str, str]] = None,
    user_input: Optional[str] = None,
    show_prompt: bool = False,
) -> Dict[str, Any]:
    """Execute one workflow step directly and return the latest step metadata."""
    issue_dir = Path(".cafe/issues") / issue_name
    playbook_name = selected_playbook or _resolve_selected_playbook(None)
    playbook_data = PlaybookLoader().load(playbook_name)
    if step_name not in playbook_data["steps"]:
        raise ValueError(f"Playbook '{playbook_name}' does not define step '{step_name}'")
    blackboard = BlackboardStore(issue_dir).load_or_create(
        str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
        playbook_id=str(playbook_data["playbook"]["id"]),
    )
    store = BlackboardStore(issue_dir)
    store.set_current_step(blackboard, step_name)
    store.update_handoff_contract(
        blackboard,
        from_step=step_name,
        to_owner=HandoffOwner.AGENT,
        to_step=step_name,
        intent=HandoffIntent.AWAIT_AGENT,
        source="single_step_alias",
    )

    generic_phase = GenericPhase(SkillLoader())
    step_executor = _build_workflow_step_executor(
        config_manager=config_manager,
        issue_dir=issue_dir,
        issue_name=issue_name,
        playbook_data=playbook_data,
        generic_phase=generic_phase,
        phase_name=step_name,
        role_agent_map_override=role_agent_map_override,
        step_user_inputs={step_name: user_input or f"{step_name} alias execute"},
    )
    step_executor.agent_manager.show_prompt = show_prompt

    runner = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook_data,
        executor=step_executor.execute_step,
    )
    result = runner.run(start_step=step_name, single_step=True)
    latest_blackboard = store.load_or_create(
        str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
        playbook_id=str(playbook_data["playbook"]["id"]),
    )
    handoff = store.load_handoff_contract(
        latest_blackboard,
        allowed_steps=list(playbook_data["steps"].keys()),
        allow_legacy_text=True,
    )
    latest_iteration_dir = _find_latest_iteration_dir(issue_dir / step_name)
    iteration = None
    if latest_iteration_dir is not None:
        try:
            iteration = int(latest_iteration_dir.name.split("_")[1])
        except (IndexError, ValueError):
            iteration = None
    output_file = _get_latest_versioned_file(step_name, issue_name)
    return {
        "result": result,
        "status_code": result.final_status_code,
        "iterations": iteration,
        "output_file": str(output_file) if output_file else None,
        "current_step": latest_blackboard.current_step,
        "handoff_owner": handoff.to_owner.value,
        "handoff_intent": handoff.intent.value,
        "next_step": handoff.to_step,
    }


def _build_workflow_pause_guidance(*, blackboard: object, final_status_code: str) -> str:
    events = getattr(blackboard, "events", None)
    if isinstance(events, list):
        for event in reversed(events):
            event_type = getattr(event, "event_type", "")
            data = getattr(event, "data", {}) or {}
            if event_type == "workflow_blocked" and data.get("reason") == "missing_capability_receipt":
                required_event = str(data.get("required_event", "")).strip()
                if required_event:
                    return (
                        f"Host capability did not complete for this step. "
                        f"Required receipt: {required_event}. Resolve the host-side action, then run cafe make again."
                    )
                return "Host capability did not complete for this step. Resolve the host-side action, then run cafe make again."
            if event_type == "baton_missing_transition":
                return "Agent did not hand off to a new step. Open chat with the current role or update the baton, then run cafe make again."
            if event_type == "status_code_invalid":
                invalid_codes = data.get("invalid_status_codes")
                if isinstance(invalid_codes, list) and invalid_codes:
                    rendered = ", ".join(str(code) for code in invalid_codes)
                    return (
                        f"Agent response did not match a valid workflow transition ({rendered}). "
                        "Fix the agent output or prompt, then run cafe make again."
                    )
                return "Agent response did not match a valid workflow transition. Fix the agent output or prompt, then run cafe make again."
            if event_type == "status_code_missing":
                return "Agent response did not include a recognizable workflow transition. Fix the agent output or prompt, then run cafe make again."

    if final_status_code == "INVALID_STATUS_CODE":
        return "Agent response did not match a valid workflow transition. Fix the agent output or prompt, then run cafe make again."
    if final_status_code == "NO_STATUS_CODE":
        return "Agent response did not include a recognizable workflow transition. Fix the agent output or prompt, then run cafe make again."
    if final_status_code == "NO_BATON_TRANSITION":
        return "Agent did not hand off to a new step. Open chat with the current role or update the baton, then run cafe make again."
    if final_status_code == "MISSING_CAPABILITY_RECEIPT":
        return "Host capability did not complete for this step. Resolve the host-side action, then run cafe make again."
    return "Resolve the requested input, then run cafe make again to resume."


def _alias_status(alias_result: Dict[str, Any]) -> str:
    return str(alias_result.get("status_code", ""))


def _alias_handoff_owner(alias_result: Dict[str, Any]) -> str:
    return str(alias_result.get("handoff_owner", ""))


def _alias_handoff_intent(alias_result: Dict[str, Any]) -> str:
    return str(alias_result.get("handoff_intent", ""))


def _alias_next_step(alias_result: Dict[str, Any]) -> str:
    return str(alias_result.get("next_step", ""))


def _alias_is_user_pause(alias_result: Dict[str, Any]) -> bool:
    return _alias_handoff_owner(alias_result) == "user"


def _alias_is_done(alias_result: Dict[str, Any]) -> bool:
    return _alias_handoff_owner(alias_result) == "done"


def _alias_targets(alias_result: Dict[str, Any], step_name: str) -> bool:
    return _alias_next_step(alias_result) == step_name


def _alias_pause_intent(alias_result: Dict[str, Any], *intents: str) -> bool:
    return _alias_is_user_pause(alias_result) and _alias_handoff_intent(alias_result) in set(intents)


def _alias_is_confirmed_transition(alias_result: Dict[str, Any], step_name: str) -> bool:
    return _alias_targets(alias_result, step_name) or _alias_status(alias_result) == "CAFE_CONFIRMED"


def _alias_needs_clarification(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "need_clarification") or _alias_status(alias_result) == "CAFE_NEED_CLARIFICATION"


def _alias_needs_permission(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "need_permission") or _alias_status(alias_result) == "CAFE_NEED_PERMISSION"


def _alias_confirm_output_pause(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "confirm_output") or _alias_status(alias_result) == "CAFE_READY_FOR_REVIEW"


def _reject_unsupported_phase_options(phase_name: str, unsupported_options: Dict[str, bool]) -> None:
    """Exit when a legacy-only CLI option is requested."""
    unsupported = [name for name, enabled in unsupported_options.items() if enabled]
    if not unsupported:
        return
    rendered = ", ".join(f"--{name}" for name in unsupported)
    console.print(
        f"[red]Error: {phase_name} no longer supports legacy phase options: {rendered}[/red]"
    )
    console.print(
        "[dim]Use the workflow runtime directly or rerun without those flags.[/dim]"
    )
    raise typer.Exit(1)


def _print_legacy_phase_command_notice(*, phase_name: str, preferred_command: str) -> None:
    """Show migration guidance for legacy phase aliases."""
    console.print(
        f"[yellow]Legacy phase command:[/yellow] [bold]cafe {phase_name}[/bold] is being retired."
    )
    console.print(
        f"[dim]Preferred entrypoint:[/dim] [bold]{preferred_command}[/bold]"
    )
    console.print()


def _edit_latest_phase_artifact(
    *,
    ctx: typer.Context,
    phase_name: str,
    missing_hint: str,
) -> None:
    """Open the latest phase artifact in the user's editor."""
    issue_name = _get_and_validate_branch(ctx, phase_name)
    phase_file = _get_latest_versioned_file(phase_name, issue_name)
    if not phase_file:
        console.print(f"[red]Error: No {phase_name} file found for issue '{issue_name}'[/red]")
        console.print(f"[dim]Hint: {missing_hint}[/dim]")
        raise typer.Exit(1)

    _edit_file_with_editor(phase_file)


@app.command()
def edit(
    ctx: typer.Context,
    phase_name: str = typer.Argument(..., help="Phase artifact to edit: spec, plan, develop, review, pr"),
) -> None:
    """Open the latest phase artifact in your editor."""
    supported_phases = {"spec", "plan", "develop", "review", "pr"}
    if phase_name not in supported_phases:
        console.print(
            "[red]Error: phase must be one of spec, plan, develop, review, pr[/red]"
        )
        raise typer.Exit(1)

    missing_hints = {
        "spec": "Run 'cafe make --user-input ...' or 'cafe workflow --start-step spec --execute --user-input ...' first.",
        "plan": "Run 'cafe make' first.",
        "develop": "Run 'cafe make' first.",
        "review": "Run 'cafe make' first.",
        "pr": "Run 'cafe make' first.",
    }

    try:
        _edit_latest_phase_artifact(
            ctx=ctx,
            phase_name=phase_name,
            missing_hint=missing_hints[phase_name],
        )
        return
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def _run_iterative_alias_step(
    *,
    issue_name: str,
    step_name: str,
    config_manager: ConfigManager,
    interactive: bool,
    auto: bool,
    continuation_statuses: List[str],
    role_agent_map_override: Optional[Dict[str, str]] = None,
    user_input: Optional[str] = None,
    show_prompt: bool = False,
    clarification_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one step repeatedly through workflow aliases until it settles."""
    current_input = user_input
    iteration_count = 1

    while True:
        if iteration_count > 1:
            console.print(f"\n[bold cyan]━━━ Iteration {iteration_count} ━━━[/bold cyan]\n")

        alias_result = _execute_single_step_alias(
            issue_name=issue_name,
            step_name=step_name,
            config_manager=config_manager,
            role_agent_map_override=role_agent_map_override,
            user_input=current_input,
            show_prompt=show_prompt,
        )
        status_code = _alias_status(alias_result)
        handoff_owner = _alias_handoff_owner(alias_result)
        handoff_intent = _alias_handoff_intent(alias_result)
        should_iterate = (
            handoff_owner == "user" and handoff_intent in {"need_clarification", "confirm_output"}
        ) or status_code in continuation_statuses
        if not should_iterate:
            return alias_result
        if not interactive:
            return alias_result

        console.print()
        if handoff_intent == "need_clarification" or status_code == "CAFE_NEED_CLARIFICATION":
            console.print("[yellow]💬 Agent needs clarification[/yellow]")
            _display_iteration_questions(issue_name=issue_name, step_name=step_name, alias_result=alias_result)
        else:
            console.print("[yellow]📝 Draft ready for review[/yellow]")

        should_continue = auto or prompt_confirm("Continue to next iteration?", default=True)
        if not should_continue:
            console.print("[dim]Stopped by user.[/dim]")
            return alias_result

        if (handoff_intent == "need_clarification" or status_code == "CAFE_NEED_CLARIFICATION") and clarification_prompt:
            current_input = prompt_multiline(clarification_prompt).strip() or current_input
        iteration_count += 1
        console.print("[dim]Continuing...[/dim]")


def _display_iteration_questions(*, issue_name: str, step_name: str, alias_result: Dict[str, Any]) -> None:
    """Render clarification questions from the latest iteration when available."""
    iteration = alias_result.get("iterations")
    if not isinstance(iteration, int) or iteration <= 0:
        return

    questions_file = Path(".cafe") / "issues" / issue_name / step_name / f"iteration_{iteration:03d}" / "questions.xml"
    if not questions_file.exists():
        return

    try:
        from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml

        if not validate_questions_xml(questions_file):
            return

        questions = parse_questions_xml(questions_file)
    except Exception:
        return

    if not questions:
        return

    console.print("Questions to confirm:")
    for idx, question in enumerate(questions, start=1):
        console.print(f"{idx}. {question.title}")
        for option in question.options:
            console.print(f"   - {option}")


def _check_agent_clis_available(config_manager: ConfigManager) -> List[str]:
    """Check if all agent CLI tools are installed."""
    return _shared_check_agent_clis_available(config_manager)


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


def _setup_agents(
    config_manager: ConfigManager,
    issue_name: Optional[str] = None,
    phase_name: Optional[str] = None,
) -> AgentManager:
    """Setup agent manager with default agents."""
    return _shared_setup_agents(
        config_manager=config_manager,
        issue_name=issue_name,
        phase_name=phase_name,
    )


def _get_latest_versioned_file(phase_name: str, issue_name: str) -> Optional[Path]:
    """Get the latest versioned file for a phase."""
    return _shared_get_latest_versioned_file(phase_name, issue_name)


def _find_latest_iteration_dir(phase_dir: Path) -> Optional[Path]:
    """Find latest iteration directory by numeric suffix."""
    return _shared_find_latest_iteration_dir(phase_dir)


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


def _resolve_iteration_index(iteration_numbers: List[int], iteration_input: int) -> int:
    """Resolve iteration number from user input."""
    return _shared_resolve_iteration_index(iteration_numbers, iteration_input)


def _resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number based on iterations that have the specified file."""
    return _shared_resolve_iteration_number(phase_dir, iteration_input, content_type)


def _get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get file path for specified content type."""
    return _shared_get_show_file_path(phase_dir, iteration, content_type)


def _get_latest_review_iteration(issue_name: str) -> int:
    """Get the latest review iteration number from iteration directories."""
    return _shared_get_latest_review_iteration(issue_name)


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


def _display_iteration_delta(
    iteration_count: int,
    output_file: Optional[str],
    console: Console,
) -> None:
    """Display delta between current and previous iteration output files."""
    _shared_display_iteration_delta(iteration_count, output_file, console)


def _print_workflow_pause_guidance(*, step_name: str, status_code: Optional[str]) -> None:
    """Render actionable recovery guidance for paused workflows."""
    if status_code == "INVALID_STATUS_CODE":
        console.print(
            "[dim]Agent returned an invalid CAFE status code for this step. "
            "Fix prompt/agent output and run cafe make again to resume.[/dim]"
        )
        return

    if status_code == "NO_BATON_TRANSITION":
        console.print("[dim]Agent finished without updating the workflow baton for this step.[/dim]")
        if step_name == "pr":
            console.print("[bold]Recommended next action:[/bold] Open chat with role `developer`")
            console.print("[bold]Suggested prompt:[/bold]")
            console.print(
                "  Do not wait for remote PR existence. Complete the local PR artifact/checklist,"
            )
            console.print(
                "  update the workflow baton, and treat remote PR publish as a later host-side hook."
            )
        else:
            console.print(
                "[bold]Recommended next action:[/bold] Open chat with the role responsible for this step,"
            )
            console.print("  or leave a handoff note in the workflow UI before resuming.")
        console.print("[dim]After the chat or handoff is written back, run cafe make again to resume.[/dim]")
        return

    if status_code == "NO_STATUS_TRANSITION":
        console.print(
            "[dim]Agent returned a status code, but the playbook has no transition for it. "
            "Open chat with the step role or fix the playbook mapping, then run cafe make again.[/dim]"
        )
        return

    console.print("[dim]Resolve the requested input, then run cafe make again to resume.[/dim]")


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
            overwrite = prompt_confirm(
                message="Do you want to overwrite the existing configuration?",
                default=False
            )

            if overwrite is None or not overwrite:
                console.print("[yellow]Cancelled. To modify existing config, use `cafe config` commands.[/yellow]")
                raise typer.Exit(0)

            console.print("[yellow]⚠️  Proceeding to overwrite existing configuration...[/yellow]")
            console.print()

        # 2. Copy agents and templates directories to local .cafe
        cafe_dir = Path(".cafe")
        cafe_dir.mkdir(parents=True, exist_ok=True)
        _ensure_default_content(cafe_dir)
        console.print()

        # 3. Check available CLIs
        available_clis = check_available_clis()

        if not available_clis:
            console.print("[red]No supported AI agents found. Please install at least one agent before retrying.[/red]")
            console.print("[yellow]Supported agents: claude, gemini, cursor-agent, codex, copilot[/yellow]")
            raise typer.Exit(1)

        console.print(f"[green]Found available AI agents: {', '.join(available_clis)}[/green]\n")

        # 4. Interactive configuration for three roles
        agents_config = _interactive_agent_setup(available_clis)

        # 5. Build full config
        config = {
            "agents": agents_config,
            "settings": {
                "auto_update": True,
            },
        }

        # 6. Save configuration
        config_manager.save_config(config)

        # 7. Display success message
        console.print("[bold green]Configuration saved successfully![/bold green]\n")
        _display_agent_summary(agents_config)

        console.print("\n[cyan]You can now use `cafe prepare` to start a new development task.[/cyan]")
        console.print(
            "[cyan]To modify settings, use `cafe config` commands. See `cafe config --help` for details.[/cyan]"
        )

    except KeyboardInterrupt:
        console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
        raise typer.Exit(1)


@app.command()
def setup() -> None:
    """Reconfigure agent roles (CLI, agent, and model assignments).

    Re-runs the interactive agent configuration from `cafe init` without
    reinitializing the project. Existing non-agent settings are preserved.

    Requires `cafe init` to have been run first.
    """
    try:
        config_manager = ConfigManager()

        # Check that .cafe is initialized
        if not config_manager.config_file.exists():
            console.print("[red]Configuration not found. Please run `cafe init` first.[/red]")
            raise typer.Exit(1)

        # Load existing config
        existing_config = config_manager.load_config()

        # Check available CLIs
        available_clis = check_available_clis()

        if not available_clis:
            console.print("[red]No supported AI agents found. Please install at least one agent before retrying.[/red]")
            console.print("[yellow]Supported agents: claude, gemini, cursor-agent, codex, copilot[/yellow]")
            raise typer.Exit(1)

        console.print(f"[green]Found available AI agents: {', '.join(available_clis)}[/green]\n")

        existing_agents = existing_config.get("agents", {})

        # Display current agent configuration
        if _has_complete_agent_setup(existing_agents):
            console.print("[bold]Current agent configuration:[/bold]")
            _display_agent_summary(existing_agents)
            console.print()
        elif "agents" in existing_config:
            console.print("[yellow]Current agent configuration is incomplete.[/yellow]\n")

        # Use selective role editing only when existing setup is complete.
        # Otherwise fall back to the original full setup flow.
        if _has_complete_agent_setup(existing_agents):
            agents_config = _interactive_agent_setup_selective(existing_agents, available_clis)
        else:
            console.print("[yellow]Incomplete agent configuration detected. Starting full setup flow.[/yellow]\n")
            agents_config = _interactive_agent_setup(available_clis)

        # Merge: update agents section, preserve everything else
        existing_config["agents"] = agents_config
        config_manager.save_config(existing_config)

        console.print("[bold green]Agent configuration updated successfully![/bold green]\n")
        _display_agent_summary(agents_config)

        console.print(
            "\n[cyan]To modify individual settings, use `cafe config` commands. See `cafe config --help` for details.[/cyan]"
        )

    except KeyboardInterrupt:
        console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show CAFE version."""
    console.print(f"CAFE version {_get_version()}")


def _get_version() -> str:
    """Get package version, trying both package names."""
    from importlib.metadata import version as pkg_version, PackageNotFoundError

    for name in ("cafe-engine", "cafe"):
        try:
            return pkg_version(name)
        except PackageNotFoundError:
            continue
    return "unknown"


BACK_SENTINEL = "__BACK__"
CUSTOM_MODEL_SENTINEL = "__CUSTOM__"
KEEP_MODEL_SENTINEL = "__KEEP__"
SAVE_SENTINEL = "save"
ROLE_PHASES = {
    "pm": ["spec"],
    "developer": ["plan", "develop", "pr"],
    "reviewer": ["review"],
}


def _has_complete_agent_setup(agents_config: dict) -> bool:
    """Return True if role setup has minimum required fields for selective editing."""
    required_roles = ["pm", "developer", "reviewer"]
    if not isinstance(agents_config, dict):
        return False

    for role_key in required_roles:
        role_config = agents_config.get(role_key)
        if not isinstance(role_config, dict):
            return False
        if not role_config.get("cli") or not role_config.get("name"):
            return False

    return True


def _interactive_agent_setup_selective(existing_agents_config: dict, available_clis: list) -> dict:
    """Run selective agent configuration with explicit Save action."""
    from InquirerPy.separator import Separator

    role_choices = [
        {"name": "PM", "value": "pm"},
        {"name": "Developer", "value": "developer"},
        {"name": "Reviewer", "value": "reviewer"},
        Separator(),
        {"name": "Save", "value": SAVE_SENTINEL},
    ]

    role_display_map = {
        "pm": "PM",
        "developer": "Developer",
        "reviewer": "Reviewer",
    }

    staged_agents_config = copy.deepcopy(existing_agents_config)

    while True:
        selected_role = prompt_list(
            message="Select role to update:",
            choices=role_choices,
        )

        if selected_role == SAVE_SENTINEL:
            return staged_agents_config

        role_display = role_display_map.get(selected_role)
        if not role_display:
            console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
            raise typer.Exit(1)

        staged_agents_config[selected_role] = _interactive_role_setup(
            role_key=selected_role,
            role_display=role_display,
            available_clis=available_clis,
            existing_role_config=staged_agents_config.get(selected_role),
            allow_back=True,
        )
        if staged_agents_config[selected_role] == BACK_SENTINEL:
            staged_agents_config[selected_role] = copy.deepcopy(existing_agents_config.get(selected_role))
            continue
        console.print("")


def _interactive_agent_setup(available_clis: list) -> dict:
    """Run interactive agent configuration for all three roles.

    Prompts user to select CLI, agent, and phase-specific models for each role.
    Supports back navigation within each role's configuration steps.

    Args:
        available_clis: List of available CLI names

    Returns:
        Agents configuration dictionary

    Raises:
        typer.Exit: If user cancels or agents not found
    """
    from InquirerPy.separator import Separator

    agents_config = {}
    roles = [("pm", "PM"), ("developer", "Developer"), ("reviewer", "Reviewer")]

    for role_key, role_display in roles:
        agents_config[role_key] = _interactive_role_setup(
            role_key=role_key,
            role_display=role_display,
            available_clis=available_clis,
        )
        console.print("")

    return agents_config


def _interactive_role_setup(
    role_key: str,
    role_display: str,
    available_clis: list,
    existing_role_config: Optional[dict] = None,
    allow_back: bool = False,
) -> dict | str:
    """Run interactive setup for a single role."""
    from InquirerPy.separator import Separator

    phase_recommendations = {
        "spec": "high-speed / economical models",
        "plan": "smarter models",
        "develop": "smarter models",
        "review": "flexible based on your needs",
        "pr": "high-speed / economical models",
    }

    console.print(f"[bold cyan]Configuring {role_display} role:[/bold cyan]")

    phases = ROLE_PHASES.get(role_key, [])

    # Steps: 0=CLI, 1=agent, 2..N=phase models
    step = 0
    total_steps = 2 + len(phases)
    selected_cli = None
    selected_agent_name = None
    phase_models = {}
    if isinstance(existing_role_config, dict):
        for phase in phases:
            phase_config = existing_role_config.get(phase)
            if isinstance(phase_config, dict):
                model = phase_config.get("model")
                if isinstance(model, str) and model.strip():
                    phase_models[phase] = model.strip()

    while step < total_steps:
        if step == 0:
            cli_choices = list(available_clis)
            if allow_back:
                cli_choices.extend([
                    Separator(),
                    {"name": "\u2190 Back", "value": BACK_SENTINEL},
                ])

            selected_cli = prompt_list(
                message=f"Select CLI for {role_display}:",
                choices=cli_choices,
            )
            if selected_cli == BACK_SENTINEL:
                return BACK_SENTINEL
            if not selected_cli:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)
            step += 1
            continue

        if step == 1:
            agents = list_available_agents(role_key)

            if not agents:
                console.print(f"[red]Error: Agent files not found for {role_display} role.[/red]")
                console.print(
                    f"[yellow]Please ensure valid .md files exist in ~/.cafe/agents/{role_key}/ or src/cafe/data/agents/{role_key}/ directory.[/yellow]"
                )
                raise typer.Exit(1)

            agent_choices = []
            for name, desc, _, source_type in agents:
                source_label = " (custom)" if source_type == "custom" else " (system default)"
                agent_choices.append(f"{name}: {desc}{source_label}")

            agent_choices.append(Separator())
            agent_choices.append({"name": "\u2190 Back to CLI selection", "value": BACK_SENTINEL})

            selected = prompt_list(
                message=f"Select agent for {role_display}:",
                choices=agent_choices,
            )

            if selected == BACK_SENTINEL:
                step = 0
                continue

            if not selected:
                console.print("\n[yellow]Configuration incomplete, cancelled.[/yellow]")
                raise typer.Exit(1)

            selected_agent_name = selected.split(":")[0].strip()
            step += 1
            continue

        phase_idx = step - 2
        phase = phases[phase_idx]
        recommendation = phase_recommendations.get(phase, "")
        recommendation_text = f" (recommended: {recommendation})" if recommendation else ""

        model_choices = [
            {"name": "Use default model", "value": ""},
            {"name": "Keep current setting", "value": KEEP_MODEL_SENTINEL},
            {"name": "Custom (type model name)", "value": CUSTOM_MODEL_SENTINEL},
            Separator(),
            {"name": "\u2190 Back", "value": BACK_SENTINEL},
        ]

        selected = prompt_list(
            message=f"Model for {phase} phase{recommendation_text}:",
            choices=model_choices,
        )

        if selected == BACK_SENTINEL:
            step -= 1
            continue

        if selected == CUSTOM_MODEL_SENTINEL:
            model_name = prompt_text(
                message=f"Enter {selected_cli} model name for {phase} phase:",
                default="",
            )
            if model_name and model_name.strip():
                phase_models[phase] = model_name.strip()
            else:
                phase_models.pop(phase, None)
        elif selected == KEEP_MODEL_SENTINEL:
            # Preserve current phase override (or default) without changes.
            pass
        else:
            # "Use default model" clears any existing phase-specific override.
            phase_models.pop(phase, None)

        step += 1

    role_config = {
        "name": selected_agent_name,
        "cli": selected_cli,
    }
    for phase, model in phase_models.items():
        role_config[phase] = {"model": model}

    if not isinstance(existing_role_config, dict):
        return role_config

    # Preserve non-interactive role-level settings (for example backup/models)
    # while replacing editable fields from the setup flow.
    merged_role_config = copy.deepcopy(existing_role_config)
    merged_role_config["name"] = role_config["name"]
    merged_role_config["cli"] = role_config["cli"]

    for phase in phases:
        merged_role_config.pop(phase, None)
    for phase, model_config in role_config.items():
        if phase in phases:
            merged_role_config[phase] = model_config

    return merged_role_config


def _display_agent_summary(agents_config: dict) -> None:
    """Display a summary of agent configuration.

    Args:
        agents_config: Agents configuration dictionary
    """
    roles = [
        ("pm", "PM"),
        ("developer", "Developer"),
        ("reviewer", "Reviewer"),
    ]

    for role_key, role_display in roles:
        role_config = agents_config[role_key]
        phases = ROLE_PHASES.get(role_key, [])

        # Build phase models display
        phase_models = []
        for phase in phases:
            if phase in role_config and "model" in role_config[phase]:
                phase_models.append(f"{phase}={role_config[phase]['model']}")
            else:
                phase_models.append(f"{phase}=default")

        models_display = ", ".join(phase_models) if phase_models else "default"

        console.print(
            f"- {role_display}: {role_config['cli']} "
            f"(agent: {role_config['name']}, models: {models_display})"
        )


def _ensure_default_content(cafe_dir: Path) -> None:
    """Copy agent and template files into local .cafe directory.

    Copies from global custom (~/.cafe/) and system default (src/cafe/data/)
    directories. Global custom files take precedence over system defaults.

    Args:
        cafe_dir: Path to .cafe directory
    """
    # Copy agents and templates to local .cafe
    agent_results = copy_agents_to_local(cafe_dir)
    template_results = copy_templates_to_local(cafe_dir)

    # Count results
    agent_success = sum(1 for _, _, success in agent_results if success)
    agent_failed = sum(1 for _, _, success in agent_results if not success)
    template_success = sum(1 for _, _, success in template_results if success)
    template_failed = sum(1 for _, _, success in template_results if not success)

    # Display summary
    if agent_success > 0 or template_success > 0:
        console.print(f"  [green]✓[/green] Updated .cafe directory with {agent_success} agent(s) and {template_success} template(s)")

    if agent_failed > 0 or template_failed > 0:
        console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {agent_failed + template_failed} file(s)")


def _sync_lifecycle_runtime() -> None:
    """Synchronize runtime dependencies into command modules."""
    lifecycle_commands.set_runtime(
        console_obj=console,
        prompt_text_fn=prompt_text,
        prompt_list_fn=prompt_list,
        prompt_confirm_fn=prompt_confirm,
        git_operations_cls=GitOperations,
        github_ops_cls=GitHubOps,
        github_error_cls=GitHubError,
        template_manager_cls=TemplateManager,
        display_cls=Display,
        config_manager_cls=ConfigManager,
        prompt_for_input_method_fn=prompt_for_input_method,
        prompt_for_rigor_fn=prompt_for_rigor,
        select_template_fn=select_template,
        ensure_default_content_fn=_ensure_default_content,
        resolve_iteration_index_fn=_resolve_iteration_index,
        valid_phases=VALID_PHASES,
        path_cls=Path,
    )
    phases_legacy_commands.set_runtime(globals())
    issues_commands.set_runtime(globals())
    workflow_commands.set_runtime(globals())


def _get_project_path() -> str:
    _sync_lifecycle_runtime()
    return lifecycle_commands._get_project_path()


def _get_issue_archive_path(issue_name: str) -> Path:
    _sync_lifecycle_runtime()
    return lifecycle_commands._get_issue_archive_path(issue_name)


def _backup_issue_directory(issue_dir: Path, issue_name: str) -> Path:
    _sync_lifecycle_runtime()
    return lifecycle_commands._backup_issue_directory(issue_dir, issue_name)


_sync_lifecycle_runtime()
app.command()(lifecycle_commands.prepare)
app.command()(lifecycle_commands.close)
app.command()(lifecycle_commands.restore)
app.command()(lifecycle_commands.reset)




phases_legacy_commands.set_runtime(globals())
app.command(hidden=True)(phases_legacy_commands.spec)
app.command(hidden=True)(phases_legacy_commands.plan)
app.command(hidden=True)(phases_legacy_commands.develop)
app.command(hidden=True)(phases_legacy_commands.review)
app.command(hidden=True)(phases_legacy_commands.pr)




issues_commands.set_runtime(globals())
app.command()(issues_commands.config)
app.command(name="ls")(issues_commands.list_issues)
app.command(name="rm")(issues_commands.remove_issue)


workflow_commands.set_runtime(globals())
app.command()(workflow_commands.make)
app.command()(workflow_commands.show)
app.command()(workflow_commands.summary)
app.command()(workflow_commands.workflow)




# Template management commands
TEMPLATE_TYPES = ["plan", "spec"]
template_app = typer.Typer(help="Manage plan and spec templates")
app.add_typer(template_app, name="template")


@template_app.command(name="add")
def template_add(
    source_file: Optional[str] = typer.Option(None, "--source-file", help="Path to the template file to add"),
    name: Optional[str] = typer.Option(None, "--name", help="Name for the template"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
) -> None:
    """Add a new template from a file.

    \b
    Examples:
        cafe template add --source-file path/to/template.md --name my-template --type plan
        cafe template add  # Interactive mode
    """
    import tempfile

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        if not name:
            name = prompt_text(
                message="Template name:",
                default="",
            )
            name = name.strip()
            if not name:
                console.print("[red]Error: Template name cannot be empty[/red]")
                raise typer.Exit(1)

        if not source_file:
            source_file = prompt_text(
                message="Source file path:",
                default="",
            )
            source_file = source_file.strip()
            if not source_file:
                console.print("[red]Error: Source file path cannot be empty[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Add template
    manager = TemplateManager(template_type=template_type)
    try:
        template_path = manager.add_template(source_file, name)
        # Show path relative to home directory
        try:
            relative_path = template_path.relative_to(Path.home())
            console.print(f"[green]✅ {template_type.capitalize()} template '{name}' added successfully: ~/{relative_path}[/green]")
        except ValueError:
            console.print(f"[green]✅ {template_type.capitalize()} template '{name}' added successfully: {template_path}[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except FileExistsError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def _print_templates(custom_only: bool = False) -> None:
    """Print templates table. Used by template ls, edit, rm."""
    from rich.table import Table

    table = Table(title="Custom Templates" if custom_only else "Available Templates", show_header=True, header_style="bold cyan")
    table.add_column("Type", style="green")
    table.add_column("Template", style="yellow")
    table.add_column("Source", style="dim")

    has_templates = False
    for template_type in TEMPLATE_TYPES:
        manager = TemplateManager(template_type=template_type)
        for name, source_type in manager.list_templates():
            if custom_only and source_type == "system":
                continue
            has_templates = True
            table.add_row(template_type, name, source_type)

    if not has_templates:
        console.print(f"[yellow]No {'custom ' if custom_only else ''}templates found.[/yellow]")
        return

    console.print(table)


@template_app.command(name="ls")
def template_ls(
    custom_only: bool = typer.Option(False, "--custom-only", help="Show only custom templates"),
) -> None:
    """List available templates.

    \b
    Examples:
        cafe template ls
        cafe template ls --custom-only
    """
    _print_templates(custom_only)


@template_app.command(name="rm")
def template_rm(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to remove"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Remove a template.

    \b
    Examples:
        cafe template rm --name my-template --type plan
        cafe template rm --name my-template --type plan --force
        cafe template rm  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Show custom templates before prompting
    if not name:
        _print_templates(custom_only=True)
        console.print()

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            # Only list custom templates (system templates cannot be deleted)
            custom_templates = [name for name, src in manager.list_templates() if src != "system"]
            if not custom_templates:
                console.print(f"[yellow]No custom {template_type} templates found[/yellow]")
                console.print("[dim]System default templates cannot be deleted[/dim]")
                raise typer.Exit(1)

            name = prompt_list(
                message="Select template to delete:",
                choices=custom_templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Confirm deletion unless --force
    if not force:
        try:
            confirm = prompt_confirm(
                f"Are you sure you want to delete template '{template_type}/{name}.md'?",
                default=False
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Remove template
    try:
        manager.remove_template(name)
        console.print(f"[green]✅ {template_type.capitalize()} template '{name}' removed successfully[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@template_app.command(name="cat")
def template_cat(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to view"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """View template content.

    \b
    Examples:
        cafe template cat --name my-template --type plan
        cafe template cat  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            templates_with_source = manager.list_templates()
            if not templates_with_source:
                console.print(f"[red]No {template_type} templates found[/red]")
                raise typer.Exit(1)

            templates = [name for name, _ in templates_with_source]
            name = prompt_list(
                message="Select template to view:",
                choices=templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Display template
    template_path = manager.get_template_path(name)
    if not template_path:
        console.print(f"[red]Error: {template_type.capitalize()} template '{name}' not found[/red]")
        raise typer.Exit(1)

    # Display template content using pager
    try:
        subprocess.run(["less", "-R", str(template_path)], check=False)
    except FileNotFoundError:
        # Fallback: print to console
        content = template_path.read_text()
        console.print(content)


@template_app.command(name="edit")
def template_edit(
    name: Optional[str] = typer.Option(None, "--name", help="Template name to edit"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
    config_file: str = typer.Option(
        ".cafe/config.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
) -> None:
    """Edit a template with $EDITOR.

    \b
    Examples:
        cafe template edit --name my-template --type plan
        cafe template edit  # Interactive mode
    """
    config_dir = str(Path(config_file).parent) if config_file != ".cafe/config.yaml" else ".cafe"

    # Show custom templates before prompting
    if not name:
        _print_templates(custom_only=True)
        console.print()

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        manager = TemplateManager(template_type=template_type)

        if not name:
            # Only list custom templates (system templates cannot be edited)
            custom_templates = [name for name, src in manager.list_templates() if src != "system"]
            if not custom_templates:
                console.print(f"[yellow]No custom {template_type} templates found[/yellow]")
                console.print("[dim]System default templates cannot be edited[/dim]")
                raise typer.Exit(1)

            name = prompt_list(
                message="Select template to edit:",
                choices=custom_templates,
            )

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Edit template
    template_path = manager.get_template_path(name)
    if not template_path:
        console.print(f"[red]Error: {template_type.capitalize()} template '{name}' not found[/red]")
        raise typer.Exit(1)

    # Open template in editor
    editor = os.environ.get("EDITOR", "vim")
    try:
        subprocess.run([editor, str(template_path)], check=True)
        console.print(f"[green]✅ Template '{name}' updated[/green]")

        # Auto-sync templates to local .cafe directory
        from cafe.ui.init_helpers import sync_templates
        cafe_dir = Path(".cafe")
        if cafe_dir.exists():
            template_success, template_failed = sync_templates(cafe_dir)
            if template_success > 0:
                console.print(f"  [green]✓[/green] Updated .cafe directory with {template_success} template(s)")
            if template_failed > 0:
                console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {template_failed} template file(s)")

    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit template[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        console.print("[dim]Set EDITOR environment variable or install vim[/dim]")
        raise typer.Exit(1)


@template_app.command(name="create")
def template_create(
    name: Optional[str] = typer.Option(None, "--name", help="Template name"),
    template_type: Optional[str] = typer.Option(None, "--type", "-t", help="Template type: plan or spec"),
) -> None:
    """Create a new template from scratch.

    \b
    Examples:
        cafe template create --name my-template --type plan
        cafe template create  # Interactive mode
    """
    import tempfile

    # Interactive prompting for missing arguments
    try:
        if not template_type:
            template_type = prompt_list(
                message="Select template type:",
                choices=TEMPLATE_TYPES,
            )

        # Validate template type
        if template_type not in TEMPLATE_TYPES:
            console.print(f"[red]Error: Invalid template type '{template_type}'. Must be 'plan' or 'spec'.[/red]")
            raise typer.Exit(1)

        if not name:
            name = prompt_text(
                message="Template name:",
                default="",
            )
            name = name.strip()
            if not name:
                console.print("[red]Error: Template name cannot be empty[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Create TemplateManager
    manager = TemplateManager(template_type=template_type)

    # Create template with editor
    editor = os.environ.get("EDITOR", "vim")

    # Create temp file with placeholder
    placeholder_content = f"""# Please enter your {template_type} template "{name}" content below.
# Note: It is highly recommended to include a todo list for all tasks.

"""

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tf:
        tf.write(placeholder_content)
        temp_path = tf.name

    try:
        # Open editor
        subprocess.run([editor, temp_path], check=True)

        # Read content
        with open(temp_path, "r") as f:
            content = f.read().strip()

        # Remove placeholder comments if user didn't modify
        if f'# Please enter your {template_type} template "{name}" content below.' in content:
            lines = [
                line for line in content.split('\n')
                if not (line.strip().startswith('#') and (
                    'Please enter your' in line or
                    'Note: It is highly recommended' in line
                ))
            ]
            content = '\n'.join(lines).strip()

        if not content:
            console.print("[red]Error: Template content cannot be empty[/red]")
            raise typer.Exit(1)

    finally:
        # Clean up temp file
        os.unlink(temp_path)

    # Save template using TemplateManager
    try:
        # Write content to a temporary file first
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
            tf.write(content)
            source_path = tf.name

        try:
            template_path = manager.add_template(source_path, name)
            # Show path relative to home directory
            try:
                relative_path = template_path.relative_to(Path.home())
                console.print(f"[green]✅ {template_type.capitalize()} template '{name}' created successfully: ~/{relative_path}[/green]")
            except ValueError:
                console.print(f"[green]✅ {template_type.capitalize()} template '{name}' created successfully: {template_path}[/green]")
        finally:
            # Clean up temporary source file
            os.unlink(source_path)
    except FileExistsError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@template_app.command(name="sync")
def template_sync() -> None:
    """Sync template files from global/system sources to local .cafe directory.

    Updates all template files in .cafe/templates to their latest versions from
    ~/.cafe/templates (custom) or src/cafe/data/templates (system default).
    Global custom templates take precedence over system defaults.
    """
    from cafe.ui.init_helpers import sync_templates

    # Check if .cafe directory exists
    cafe_dir = Path(".cafe")
    if not cafe_dir.exists():
        console.print("[red]Error: CAFE not initialized in this directory[/red]")
        console.print("[dim]Run 'cafe init' first[/dim]")
        raise typer.Exit(1)

    # Sync templates
    template_success, template_failed = sync_templates(cafe_dir)

    # Display summary
    if template_success > 0:
        console.print(f"  [green]✓[/green] Updated .cafe directory with {template_success} template(s)")

    if template_failed > 0:
        console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {template_failed} template file(s)")




# Agent management commands (similar to template commands)
agent_app = typer.Typer(help="Manage agents")
app.add_typer(agent_app, name="agent")

playbook_app = typer.Typer(help="Inspect and validate playbooks")
app.add_typer(playbook_app, name="playbook")

skill_app = typer.Typer(help="Inspect and validate skills")
app.add_typer(skill_app, name="skill")


def _print_agents(custom_only: bool = False) -> None:
    """Print agents table. Used by agent ls, edit, rm."""
    from rich.table import Table
    from cafe.ui.init_helpers import list_available_agents

    roles = ["pm", "developer", "reviewer"]
    has_agents = False

    table = Table(title="Custom Agents" if custom_only else "Available Agents", show_header=True, header_style="bold cyan")
    table.add_column("Role", style="green")
    table.add_column("Agent", style="yellow")
    table.add_column("Description", style="dim")
    table.add_column("Source", style="dim")

    for role in roles:
        for agent_name, description, _, source_type in list_available_agents(role):
            if custom_only and source_type == "system":
                continue
            has_agents = True
            table.add_row(role, agent_name, description, source_type)

    if not has_agents:
        console.print(f"[yellow]No {'custom ' if custom_only else ''}agents found.[/yellow]")
        return

    console.print(table)


@playbook_app.command(name="list")
def playbook_list() -> None:
    """List resolved playbooks from builtin/global/project catalogs."""
    loader = _build_playbook_loader()
    for name in loader.list_playbooks():
        loaded = loader.load_model(name)
        console.print(f"{name}\t{loaded.source}\t{loaded.path}")


@playbook_app.command(name="show")
def playbook_show(
    name: str = typer.Argument(..., help="Playbook name"),
) -> None:
    """Show the resolved playbook definition."""
    try:
        loaded = _build_playbook_loader().load_model(name)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(yaml.dump(loaded.as_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False))
    console.print(f"\n[dim]source={loaded.source} path={loaded.path}[/dim]")
    for warning in loaded.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


@playbook_app.command(name="validate")
def playbook_validate(
    name: str = typer.Argument(..., help="Playbook name"),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Validate one playbook and print warnings if present."""
    try:
        loaded = _build_playbook_loader().load_model(name, strict=strict)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Valid[/green] {name} source={loaded.source}")
    if loaded.warnings:
        for warning in loaded.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")


@skill_app.command(name="list")
def skill_list() -> None:
    """List resolved skills from builtin/global/project catalogs."""
    try:
        items = _build_skill_loader().discover()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    for item in items:
        console.print(f"{item.name}\t{item.source}\t{item.directory}")


@skill_app.command(name="show")
def skill_show(
    name: str = typer.Argument(..., help="Skill name"),
) -> None:
    """Show resolved skill body and references path."""
    try:
        loader = _build_skill_loader()
        items = {item.name: item for item in loader.discover()}
        body = loader.activate(name)
        item = items[name]
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(body)
    console.print(f"\n[dim]source={item.source} path={item.directory}[/dim]")
    if item.warning:
        console.print(f"[yellow]warning:[/yellow] {item.warning}")


@skill_app.command(name="validate")
def skill_validate(
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Validate all discovered skills."""
    try:
        items = _build_skill_loader().discover(strict=strict)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    warnings = [item.warning for item in items if item.warning]
    console.print(f"[green]Valid[/green] {len(items)} skill(s)")
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_skill_import_summary(summary: SkillImportSummary) -> None:
    """Print skill import result summary."""
    console.print(f"[green]Imported {summary.imported_count} skill(s)[/green]")
    if summary.skipped_count:
        console.print(f"[yellow]Skipped {summary.skipped_count} item(s)[/yellow]")
    if summary.failed_count:
        console.print(f"[red]Failed {summary.failed_count} item(s)[/red]")

    for item in summary.results:
        if item.status == "imported":
            reason_suffix = f" ({item.reason})" if item.reason else ""
            console.print(f"[green]imported:[/green] {item.name}{reason_suffix}")
        elif item.status == "skipped":
            console.print(f"[yellow]skipped:[/yellow] {item.name} ({item.reason})")
        else:
            console.print(f"[red]failed:[/red] {item.name} ({item.reason})")


def _print_skill_remove_summary(summary: SkillRemoveSummary) -> None:
    """Print skill removal result summary."""
    console.print(f"[green]Removed {summary.removed_count} skill(s)[/green]")
    if summary.skipped_count:
        console.print(f"[yellow]Skipped {summary.skipped_count} item(s)[/yellow]")
    if summary.failed_count:
        console.print(f"[red]Failed {summary.failed_count} item(s)[/red]")

    for item in summary.results:
        if item.status == "removed":
            console.print(f"[green]removed:[/green] {item.name}")
        elif item.status == "skipped":
            console.print(f"[yellow]skipped:[/yellow] {item.name} ({item.reason})")
        else:
            console.print(f"[red]failed:[/red] {item.name} ({item.reason})")


@skill_app.command(name="import")
def skill_import(
    path: str = typer.Argument(..., help="Directory containing one or more skill folders"),
) -> None:
    """Import skill folders into the current project's `.cafe/skills` directory."""
    try:
        skill_names = preview_importable_skills(Path(path))
        console.print(f"[yellow]Found {len(skill_names)} skill(s) to import:[/yellow]")
        for name in skill_names:
            console.print(f"  • {name}")
        console.print()

        if not prompt_confirm(
            f"Continue importing {len(skill_names)} skill(s)?",
            default=False,
        ):
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

        summary = import_skills(
            Path(path),
            Path.cwd(),
            overwrite_decider=lambda name, destination: prompt_confirm(
                f"Skill '{name}' already exists at '{destination}'. Overwrite?",
                default=False,
            ),
        )
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    _print_skill_import_summary(summary)


@skill_app.command(name="rm")
def skill_rm(
    names: Optional[list[str]] = typer.Argument(None, help="Names of skills to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more project skills."""
    project_root = Path.cwd()
    skills_root = project_root / ".cafe" / "skills"

    try:
        if not names:
            available_skills = sorted(
                item.name for item in skills_root.iterdir()
                if item.is_dir() or item.is_symlink()
            ) if skills_root.exists() else []
            if not available_skills:
                console.print("[yellow]No project skills found[/yellow]")
                raise typer.Exit(0)

            selected = prompt_checkbox(
                message="Select skill(s) to delete: (Press space to select, enter to confirm)",
                choices=available_skills,
            )
            if not selected:
                console.print("[dim]Cancelled[/dim]")
                raise typer.Exit(0)
            names = selected
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    names = list(dict.fromkeys(names))
    existing_names = [
        name for name in names
        if (skills_root / name).exists() or (skills_root / name).is_symlink()
    ]

    if not existing_names:
        summary = remove_skills(names, project_root)
        _print_skill_remove_summary(summary)
        raise typer.Exit(1)

    if not force:
        console.print(f"[yellow]About to delete {len(existing_names)} skill(s):[/yellow]")
        for name in existing_names:
            console.print(f"  • {name} [dim]({skills_root / name})[/dim]")
        console.print()
        try:
            confirm = prompt_confirm(
                f"Are you sure you want to delete {len(existing_names)} skill(s)?",
                default=False,
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled[/dim]")
            raise typer.Exit(0)
        if not confirm:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    summary = remove_skills(names, project_root)
    _print_skill_remove_summary(summary)

    if summary.failed_count:
        raise typer.Exit(1)

    if summary.removed_count == 0:
        raise typer.Exit(1)


@agent_app.command(name="ls")
def agent_ls(
    custom_only: bool = typer.Option(False, "--custom-only", help="Show only custom agents"),
) -> None:
    """List all available agents (system and custom)."""
    _print_agents(custom_only)


@agent_app.command(name="rm")
def agent_rm() -> None:
    """Remove an agent interactively."""
    from pathlib import Path
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

    _print_agents(custom_only=True)
    console.print()

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
    agent_files = sorted([f.name for f in role_dir.glob("*.md")]) if role_dir.exists() else []
    if not agent_files:
        console.print(f"[yellow]No custom agents found in role '{role}'[/yellow]")
        console.print(f"[dim]Use 'cafe agent add' to create a custom agent.[/dim]")
        raise typer.Exit(0)

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
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

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
        console.print("[yellow]Use 'cafe agent edit' to modify the existing agent.[/yellow]")
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
# IMPORTANT: Each guideline MUST start with "-" to maximize effectiveness
#
# Example:
# You are a {description}.
# Your responsibilities include:
# - Use camelCase for variable names (e.g., userName, not user_name)
# - Always add JSDoc comments for public functions
# - Prefer async/await over Promise.then() chains
# - Write unit tests in __tests__/ directory using Jest
# - Follow the project's existing error handling patterns

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

    # Show path relative to home directory
    try:
        relative_path = agent_file.relative_to(Path.home())
        console.print(f"[green]✓[/green] Agent created successfully: ~/{relative_path}")
    except ValueError:
        console.print(f"[green]✓[/green] Agent created successfully: {agent_file}")


@agent_app.command(name="edit")
def agent_edit() -> None:
    """Edit an existing agent."""
    from pathlib import Path
    import os
    from cafe.utils.config import get_global_cafe_dir

    # Get global agents directory
    agents_dir = get_global_cafe_dir() / "agents"

    _print_agents(custom_only=True)
    console.print()

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
    agent_files = sorted([f.name for f in role_dir.glob("*.md")]) if role_dir.exists() else []
    if not agent_files:
        console.print(f"[yellow]No custom agents found in role '{role}'[/yellow]")
        console.print(f"[dim]Use 'cafe agent add' to create a custom agent.[/dim]")
        raise typer.Exit(0)

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
        # Show path relative to home directory
        try:
            relative_path = agent_file.relative_to(Path.home())
            console.print(f"[green]✓[/green] Agent updated successfully: ~/{relative_path}")
        except ValueError:
            console.print(f"[green]✓[/green] Agent updated successfully: {agent_file}")

        # Auto-sync agents to local .cafe directory
        from cafe.ui.init_helpers import sync_agents
        cafe_dir = Path(".cafe")
        if cafe_dir.exists():
            agent_success, agent_failed = sync_agents(cafe_dir)
            if agent_success > 0:
                console.print(f"  [green]✓[/green] Updated .cafe directory with {agent_success} agent(s)")
            if agent_failed > 0:
                console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {agent_failed} agent file(s)")

    except subprocess.CalledProcessError:
        console.print("[red]Error: Failed to edit agent[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        raise typer.Exit(1)


@agent_app.command(name="cat")
def agent_cat(
    role: Optional[str] = typer.Option(None, "--role", "-r", help="Agent role: pm, developer, or reviewer"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Agent name to view"),
) -> None:
    """View agent content.

    \b
    Examples:
        cafe agent cat --role developer --name Nick
        cafe agent cat  # Interactive mode
    """
    from cafe.ui.init_helpers import list_available_agents

    # Interactive prompting for missing arguments
    try:
        if not role:
            role = prompt_list(
                message="Select agent role:",
                choices=["pm", "developer", "reviewer"],
            )

        # Validate role
        if role not in ["pm", "developer", "reviewer"]:
            console.print(f"[red]Error: Invalid role '{role}'. Must be 'pm', 'developer', or 'reviewer'.[/red]")
            raise typer.Exit(1)

        if not name:
            # Get all agents for this role (system + custom)
            agents = list_available_agents(role)
            if not agents:
                console.print(f"[red]No agents found for role '{role}'[/red]")
                raise typer.Exit(1)

            # Create choices with source indicators
            choices = []
            agent_map = {}
            for agent_name, description, agent_path, source_type in agents:
                if source_type == "custom":
                    display_name = f"{agent_name} (custom)"
                else:
                    display_name = f"{agent_name} (system default)"
                choices.append(display_name)
                agent_map[display_name] = (agent_name, agent_path)

            selected = prompt_list(
                message="Select agent to view:",
                choices=choices,
            )
            name, agent_path = agent_map[selected]
        else:
            # Find agent by name
            agents = list_available_agents(role)
            agent_path = None
            for agent_name, _, path, _ in agents:
                if agent_name == name:
                    agent_path = path
                    break

            if not agent_path:
                console.print(f"[red]Error: Agent '{name}' not found in role '{role}'[/red]")
                raise typer.Exit(1)

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled[/dim]")
        raise typer.Exit(0)

    # Display agent content using pager
    try:
        subprocess.run(["less", "-R", str(agent_path)], check=False)
    except FileNotFoundError:
        # Fallback: print to console
        content = agent_path.read_text()
        console.print(content)


@agent_app.command(name="sync")
def agent_sync() -> None:
    """Sync agent files from global/system sources to local .cafe directory.

    Updates all agent files in .cafe/agents to their latest versions from
    ~/.cafe/agents (custom) or src/cafe/data/agents (system default).
    Global custom agents take precedence over system defaults.
    """
    from cafe.ui.init_helpers import sync_agents

    # Check if .cafe directory exists
    cafe_dir = Path(".cafe")
    if not cafe_dir.exists():
        console.print("[red]Error: CAFE not initialized in this directory[/red]")
        console.print("[dim]Run 'cafe init' first[/dim]")
        raise typer.Exit(1)

    # Sync agents
    agent_success, agent_failed = sync_agents(cafe_dir)

    # Display summary
    if agent_success > 0:
        console.print(f"  [green]✓[/green] Updated .cafe directory with {agent_success} agent(s)")

    if agent_failed > 0:
        console.print(f"  [yellow]⚠[/yellow] Warning: Failed to copy {agent_failed} agent file(s)")




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

    raise typer.Exit(launch_chat_session(role, issue_name))


def main() -> None:
    """Entry point for CLI."""
    # Check if all dependencies are installed
    _check_dependencies()
    _check_repo_entrypoint_alignment()
    # Check for updates and auto-upgrade if available
    _check_for_updates()
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


def _check_for_updates() -> None:
    """Check for new versions of cafe-engine and auto-update if available.

    This function:
    1. Checks if auto-update is enabled in .cafe/config.yaml
    2. Respects CAFE_SKIP_UPDATE_CHECK environment variable
    3. Rate-limits checks to once per 24 hours
    4. Queries PyPI for the latest version
    5. Automatically upgrades if a newer version is available
    6. Fails silently without blocking the workflow
    """
    import os
    import urllib.request
    import json
    import importlib.metadata
    from pathlib import Path
    import subprocess

    # Check if update check is explicitly disabled via environment variable
    if os.getenv("CAFE_SKIP_UPDATE_CHECK"):
        return

    # Try to load config to check if auto-update is enabled
    try:
        config_manager = ConfigManager()
        if config_manager.config_file.exists():
            config = config_manager.load_config()
            # Check if auto_update is explicitly disabled (default is True)
            if config.get("settings", {}).get("auto_update") is False:
                return
    except Exception:
        # If config loading fails, proceed with update check
        pass

    # Import helper functions from config module
    from cafe.utils.config import should_check_for_updates, update_last_check_timestamp

    # Check if enough time has passed since last update
    if not should_check_for_updates():
        return

    try:
        # Get current installed version
        try:
            current_version = importlib.metadata.version("cafe-engine")
        except importlib.metadata.PackageNotFoundError:
            # If package not found, skip update check
            return

        # Query PyPI for latest version
        pypi_url = "https://pypi.org/pypi/cafe-engine/json"
        try:
            with urllib.request.urlopen(pypi_url, timeout=2) as response:
                pypi_data = json.loads(response.read().decode())
                latest_version = pypi_data["info"]["version"]
        except Exception:
            # If PyPI query fails, just update timestamp and return
            update_last_check_timestamp()
            return

        # Update the last check timestamp
        update_last_check_timestamp()

        # Compare versions (simple string comparison works for semantic versioning)
        # Parse versions properly for comparison
        from packaging.version import Version

        current = Version(current_version)
        latest = Version(latest_version)

        if latest > current:
            # Newer version available, attempt upgrade
            try:
                # Run pip upgrade non-interactively
                result = subprocess.run(
                    ["pip", "install", "--upgrade", "cafe-engine"],
                    capture_output=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    console.print(
                        f"\n[green]✓ cafe-engine upgraded from {current_version} to {latest_version}[/green]"
                    )
                else:
                    # Upgrade failed, but don't block workflow
                    pass
            except Exception:
                # If upgrade fails, don't block workflow
                pass

    except Exception:
        # Catch all exceptions to ensure we never block the main workflow
        pass






if __name__ == "__main__":
    main()
