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
import yaml
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.git import GitOperations
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.permission import PermissionHandler
from cafe.core.types import AgentCLI, AgentConfig, CriticalPhaseError
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
from cafe.services.delta_display import DeltaDisplay
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
        workflow(
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
    if ctx.invoked_subcommand is None:
        try:
            InteractiveMenu().run()
        except KeyboardInterrupt:
            pass

# List of all phases in order
ALL_PHASES = ["spec", "plan", "develop", "review", "pr"]

# Constants for cafe show command
VALID_PHASES = ["spec", "plan", "develop", "review", "pr"]
VALID_CONTENT_TYPES = [
    "context", "output", "streaming", "error",
    "status", "iterations", "checklist", "user_input", "questions"
]
CONTENT_TYPE_FILE_MAP = {
    "context": "context.json",
    "output": "output.md",
    "streaming": "streaming.jsonl",
    "error": "error.json",
    "status": "status.json",
    "iterations": "iterations.jsonl",
    "checklist": "checklist.md",
    "user_input": "user_input.md",
    "questions": "questions.xml",
}


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


def _setup_agents(
    config_manager: ConfigManager,
    issue_name: Optional[str] = None,
    phase_name: Optional[str] = None,
) -> AgentManager:
    """Setup agent manager with default agents.

    Args:
        config_manager: Configuration manager
        issue_name: Issue name for issue-specific sessions
        phase_name: Current phase name for phase-specific model resolution

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

    # Helper to resolve model
    def resolve_model(config: dict, phase: Optional[str]) -> Optional[str]:
        model = None
        if phase and phase in config:
            phase_config = config[phase]
            if isinstance(phase_config, dict):
                model = phase_config.get("model")

        if model is None:
            model = config.get("model")

        return model

    # Helper to resolve backup CLIs (filter out entries that match the primary CLI)
    def resolve_backup_clis(config: dict, primary_cli: AgentCLI) -> List[AgentCLI]:
        backup_raw = config.get("backup", [])
        seen = {primary_cli}
        result = []
        for cli_str in backup_raw:
            try:
                cli = AgentCLI(cli_str)
            except ValueError:
                continue
            if cli not in seen:
                seen.add(cli)
                result.append(cli)
        return result

    # Helper to resolve models config dict
    def resolve_models_config(config: dict) -> Dict[str, Dict[str, str]]:
        raw = config.get("models", {})
        if not isinstance(raw, dict):
            return {}
        result: Dict[str, Dict[str, str]] = {}
        for cli_name, phase_models in raw.items():
            if isinstance(phase_models, dict):
                result[cli_name] = {k: str(v) for k, v in phase_models.items()}
        return result

    # Register agents
    pm_cli = AgentCLI(pm_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=pm_config["name"],
            cli=pm_cli,
            model=resolve_model(pm_config, phase_name),
            backup_clis=resolve_backup_clis(pm_config, pm_cli),
            models_config=resolve_models_config(pm_config),
        )
    )
    dev_cli = AgentCLI(dev_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=dev_config["name"],
            cli=dev_cli,
            model=resolve_model(dev_config, phase_name),
            backup_clis=resolve_backup_clis(dev_config, dev_cli),
            models_config=resolve_models_config(dev_config),
        )
    )
    reviewer_cli = AgentCLI(reviewer_config["cli"])
    agent_manager.register_agent(
        AgentConfig(
            name=reviewer_config["name"],
            cli=reviewer_cli,
            model=resolve_model(reviewer_config, phase_name),
            backup_clis=resolve_backup_clis(reviewer_config, reviewer_cli),
            models_config=resolve_models_config(reviewer_config),
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


def _find_latest_iteration_dir(phase_dir: Path) -> Optional[Path]:
    """Find latest iteration directory by numeric suffix."""
    iteration_dirs = sorted(phase_dir.glob("iteration_*"))
    valid: List[tuple[int, Path]] = []
    for path in iteration_dirs:
        if not path.is_dir():
            continue
        try:
            number = int(path.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        valid.append((number, path))
    if not valid:
        return None
    valid.sort(key=lambda item: item[0])
    return valid[-1][1]


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
    """Resolve iteration number from user input.

    Shared iteration resolution logic used by both cafe show and cafe reset.

    Args:
        iteration_numbers: Sorted list of available iteration numbers
        iteration_input: User input iteration number (can be positive, 0, negative)

    Returns:
        Actual iteration number (positive integer)

    Raises:
        ValueError: When iteration number does not exist or index out of range

    Examples:
        If iteration_numbers = [1, 2, 3, 4, 5]:
        - iteration_input = 0 → returns 5 (latest)
        - iteration_input = 3 → returns 3 (exact match)
        - iteration_input = -1 → returns 4 (one before latest)
        - iteration_input = -2 → returns 3 (two before latest)
    """
    if not iteration_numbers:
        raise ValueError("No iterations available")

    # Handle different iteration number formats
    if iteration_input == 0:
        # Zero means latest iteration
        return iteration_numbers[-1]
    elif iteration_input > 0:
        # Positive number used directly, but need to verify existence
        if iteration_input not in iteration_numbers:
            raise ValueError(
                f"Iteration {iteration_input} not found. "
                f"Available iterations: {iteration_numbers}"
            )
        return iteration_input
    else:
        # Negative number: -1 means one before latest, -2 means two before, etc.
        # Since 0 already represents latest (iteration_numbers[-1]),
        # we need to offset: -1 -> [-2], -2 -> [-3], etc.
        try:
            return iteration_numbers[iteration_input - 1]
        except IndexError:
            raise ValueError(
                f"Iteration index {iteration_input} out of range. "
                f"Available iterations: {iteration_numbers}"
            )


def _resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number based on iterations that have the specified file.

    Args:
        phase_dir: Phase directory path (e.g., .cafe/issues/issue84/spec)
        iteration_input: User input iteration number (can be positive, 0, negative)
        content_type: Content type to search for (output, context, etc.)

    Returns:
        Actual iteration number (positive integer)

    Raises:
        ValueError: When iteration number does not exist or file not found
    """
    # Get filename for the content type
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # Get all iteration directories (verified by context.json file)
    all_iteration_files = sorted(phase_dir.glob("iteration_*/context.json"))

    if not all_iteration_files:
        raise ValueError(f"No iterations found in {phase_dir}")

    # Extract all iteration numbers
    all_iteration_numbers = []
    for file_path in all_iteration_files:
        dir_name = file_path.parent.name
        if dir_name.startswith("iteration_"):
            try:
                num = int(dir_name.split("_")[1])
                all_iteration_numbers.append(num)
            except (IndexError, ValueError):
                continue

    if not all_iteration_numbers:
        raise ValueError(f"No valid iterations found in {phase_dir}")

    # Find iterations that have the requested file
    iteration_numbers_with_file = []
    for iter_num in all_iteration_numbers:
        iteration_dir = phase_dir / f"iteration_{iter_num:03d}"
        file_path = iteration_dir / filename
        if file_path.exists():
            iteration_numbers_with_file.append(iter_num)

    if not iteration_numbers_with_file:
        raise ValueError(
            f"No iterations found with file '{filename}'. "
            f"Available iterations: {all_iteration_numbers}"
        )

    # Use shared iteration resolution logic
    return _resolve_iteration_index(iteration_numbers_with_file, iteration_input)


def _get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get file path for specified content type.

    Args:
        phase_dir: Phase directory path
        iteration: Iteration number (resolved to positive integer)
        content_type: Content type (context, output, streaming, etc.)

    Returns:
        Complete file path
    """
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    # status and iterations are located at phase directory root level
    if content_type in ["status", "iterations"]:
        return phase_dir / filename
    else:
        # Other files are located in iteration directory
        iteration_dir = phase_dir / f"iteration_{iteration:03d}"
        return iteration_dir / filename


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


def _display_iteration_delta(
    iteration_count: int,
    output_file: Optional[str],
    console: Console,
) -> None:
    """Display delta between current and previous iteration output files.

    Args:
        iteration_count: Current iteration number
        output_file: Path to current iteration output file (spec_file or plan_file)
        console: Rich console for output
    """
    if iteration_count > 1 and output_file:
        current_file = Path(output_file)
        # Calculate previous iteration path
        iteration_dir = current_file.parent
        phase_dir = iteration_dir.parent
        prev_iteration_num = iteration_count - 1
        previous_file = phase_dir / f"iteration_{prev_iteration_num:03d}" / "output.md"

        delta_display = DeltaDisplay()
        delta_display.display_delta(current_file, previous_file, console)


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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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
    """Run plan phase: Implementation planning with developer agent.

    The developer agent will analyze the specification and create a detailed
    implementation plan with technical considerations and development guide.

    This command automatically uses the current Git branch name as the issue identifier.

    Use 'cafe edit plan' to edit the latest plan file.

    \b
    Examples:
        cafe plan
        cafe plan --auto
        cafe plan -i 123
        cafe plan --dev CustomDev
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
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
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
            console.print("[dim]To continue, run:[/dim] [bold]cafe plan[/bold]")
        elif _alias_confirm_output_pause(alias_result):
            console.print("[bold yellow]📋 Plan ready for review[/bold yellow]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            console.print("[dim]To review the plan, run:[/dim] [bold]cafe plan[/bold]")
        elif _alias_is_confirmed_transition(alias_result, "develop"):
            console.print("[bold green]✅ Implementation plan completed![/bold green]")
            console.print(f"Iterations: {alias_result.get('iterations', 'N/A')}")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print()
            if auto:
                _execute_next_phase_auto("develop", issue_name)
            else:
                console.print("[dim]Next step:[/dim] [bold]cafe develop[/bold]")
        else:
            console.print(f"[bold yellow]Status: {status_code}[/bold yellow]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "plan")


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

    \b
    Examples:
        cafe develop
        cafe develop --dev CustomDev
        cafe develop --pr-number 123
        cafe develop --no-interactive --approve-denied-tools 0,2 --user-input "Please be careful"
    """
    try:
        # Get and validate current branch
        issue_name = _get_and_validate_branch(ctx, "develop")

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
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print(
                "[dim]Hint: Run 'cafe plan' first to create the implementation plan.[/dim]"
            )
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
            elif resolved_next_step == "pr":
                console.print("[dim]Next step:[/dim] [bold]cafe pr[/bold]")
            else:
                console.print("[dim]Next step:[/dim] [bold]cafe review[/bold]")
        elif _alias_needs_clarification(alias_result) or _alias_needs_permission(alias_result):
            if auto:
                _execute_next_phase_auto("develop", issue_name)
            else:
                console.print(f"[yellow]⏸️  Development paused: {status_code}[/yellow]")
                console.print("[dim]Resume with: cafe develop[/dim]")
        else:
            console.print(f"[bold red]❌ Development failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "develop")


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

    \b
    Examples:
        cafe review
        cafe review --commit abc123
        cafe review --reviewer CustomReviewer
        cafe review --force
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
            console.print("[dim]Hint: Run 'cafe spec' first to create the specification.[/dim]")
            raise typer.Exit(1)

        plan_file_path = _get_latest_versioned_file("plan", issue_name)
        if plan_file_path is None:
            console.print(f"[red]Error: No plan file found for issue '{issue_name}'[/red]")
            console.print(
                "[dim]Hint: Run 'cafe plan' first to create the implementation plan.[/dim]"
            )
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
                console.print("[dim]Next steps:[/dim]")
                console.print("[dim]  1. Create PR: cafe pr[/dim]")
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
                    console.print("[dim]  • Continue: [bold]cafe review[/bold] (without --auto)[/dim]")
                    console.print("[dim]  • Adjust limit: [bold]cafe config set auto.max_review_iterations 10[/bold][/dim]")
                else:
                    console.print(f"[dim]Review iteration: {current_iteration}/{max_iterations}[/dim]")
                    _execute_next_phase_auto("develop", issue_name)
            else:
                console.print("[dim]Next steps:[/dim]")
                console.print("[dim]  1. Make changes: cafe develop[/dim]")
                console.print("[dim]  2. Review again: cafe review[/dim]")
        elif _alias_needs_clarification(alias_result):
            console.print("[bold yellow]💬 Review needs clarification[/bold yellow]")
            if alias_result.get("output_file"):
                console.print(f"Saved to: {alias_result['output_file']}")
            console.print("[dim]Resume with:[/dim] [bold]cafe review[/bold]")
        else:
            console.print(f"[bold red]❌ Review failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "review")


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
    post_todo_list: Optional[bool] = typer.Option(
        None,
        "--post-todo-list/--no-post-todo-list",
        help="Post organized todo list as PR comment (default: auto-detect from config)",
    ),
) -> None:
    """Create pull request for the issue.

    The PR phase will push the feature branch and create a GitHub Pull Request.

    This command automatically uses the current Git branch name as the issue identifier.

    \b
    Examples:
        cafe pr
        cafe pr --no-draft
        cafe pr --title "Add user authentication" --body "Implements login/logout"
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
            console.print("[dim]Next step:[/dim] [bold]cafe develop[/bold]")
        else:
            console.print(f"[bold red]❌ PR failed: {status_code}[/bold red]")
            raise typer.Exit(1)
        return

    except Exception as e:
        _handle_phase_exception(e, "pr")


@app.command()
def config(
    action: Optional[str] = typer.Argument(
        None, help="Action: set, get, edit, reset, or config key"
    ),
    key: Optional[str] = typer.Argument(None, help="Configuration key"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
) -> None:
    """Manage CAFE configuration.

    \b
    Examples:
        cafe config
        cafe config set pm gemini
        cafe config set pm.cli gemini
        cafe config set agents.pm.cli gemini
        cafe config get pm
        cafe config get agents.pm.cli
        cafe config edit
        cafe config reset
    """
    config_manager = ConfigManager()
    import os
    import subprocess

    # No arguments: show all config
    if not action:
        try:
            loaded_config = config_manager.load_config()
        except ConfigError as e:
            console.print(f"[red]{e}[/red]")
            return
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

    # Get all issue directories (recursively find dirs containing issue.yaml or phase dirs)
    def _find_issues(base_dir: Path) -> list[Path]:
        """Find issue directories by looking for issue.yaml or phase subdirectories."""
        found = []
        for d in sorted(base_dir.iterdir()):
            if not d.is_dir():
                continue
            # A directory is an issue if it contains issue.yaml or any phase dir
            if (d / "issue.yaml").exists() or any((d / phase).exists() for phase in ALL_PHASES):
                found.append(d)
            else:
                # Check subdirectories (for nested issue names like feature/chat-web-ui)
                found.extend(_find_issues(d))
        return found

    issues = _find_issues(issues_dir)

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
        # Get worktree path from issue.yaml first
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
                # If read fails, keep default value "-"
                pass

        # Check which phases exist
        # If worktree_path exists, read phases from worktree location
        phases = []
        if worktree_path != "-":
            # Read phases from worktree/.cafe/issues/{issue_name}/
            worktree_issue_dir = Path(worktree_path) / ".cafe" / "issues" / issue.relative_to(issues_dir)
            if worktree_issue_dir.exists():
                for phase in ALL_PHASES:
                    phase_dir = worktree_issue_dir / phase
                    if phase_dir.exists():
                        phases.append(phase)
            # If worktree issue dir doesn't exist, fall back to current location
            if not phases:
                for phase in ALL_PHASES:
                    phase_dir = issue / phase
                    if phase_dir.exists():
                        phases.append(phase)
        else:
            # No worktree, read phases from current location
            for phase in ALL_PHASES:
                phase_dir = issue / phase
                if phase_dir.exists():
                    phases.append(phase)

        phases_str = ", ".join(phases) if phases else "empty"

        # Get last modified time
        import datetime

        mtime = datetime.datetime.fromtimestamp(issue.stat().st_mtime)
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M")

        issue_name = str(issue.relative_to(issues_dir))
        table.add_row(issue_name, phases_str, worktree_path, mtime_str)

    console.print(table)
    console.print(f"\n[dim]Total: {len(issues)} issue(s)[/dim]")


@app.command(name="rm")
def remove_issue(
    issue_names: Optional[list[str]] = typer.Argument(
        None, help="Names of the issues to delete (supports wildcards like 'test-*')"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Remove one or more issues and all their data."""
    import fnmatch

    # If no arguments, show issue list and prompt for issue name
    if not issue_names:
        list_issues()
        console.print()
        issue_input = prompt_text("Issue name(s) to remove (space-separated):")
        if not issue_input or not issue_input.strip():
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)
        issue_names = issue_input.strip().split()

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
            worktree_path: Optional[Path] = None
            backup_info = "none"
            issue_yaml = issue_path / "issue.yaml"
            if issue_yaml.exists():
                try:
                    config_data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
                except Exception:
                    config_data = {}
                raw_worktree_path = config_data.get("worktree_path")
                if isinstance(raw_worktree_path, str) and raw_worktree_path.strip():
                    worktree_path = Path(raw_worktree_path)
                    if not worktree_path.is_absolute():
                        worktree_path = (Path.cwd() / worktree_path).resolve()

            if worktree_path is not None and worktree_path.exists():
                worktree_issue_dir = worktree_path / ".cafe" / "issues" / issue_name
                if worktree_issue_dir.exists():
                    archive_path = _backup_issue_directory(worktree_issue_dir, issue_name)
                    backup_info = str(archive_path)
                    console.print(f"[green]✓[/green] Backed up issue '{issue_name}' to {archive_path}")
                shutil.rmtree(worktree_path)
                console.print(f"[green]✓[/green] Removed worktree '{worktree_path}'")

            shutil.rmtree(issue_path)
            console.print(f"[dim]  Backup: {backup_info}[/dim]")
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


if __name__ == "__main__":
    main()
