"""Command-line interface for CAFE."""

import copy
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List, Optional

import typer
import click
from typer.core import TyperGroup

from cafe.ui.inquirer_prompts import prompt_checkbox, prompt_confirm, prompt_list, prompt_multiline, prompt_text  # noqa: F401 — backward-compat re-export for test patch targets
from cafe.ui.menu import InteractiveMenu
from cafe.ui.commands import lifecycle as lifecycle_commands
from cafe.ui.commands import phases_legacy as phases_legacy_commands
from cafe.ui.commands import issues as issues_commands
from cafe.ui.commands import templates as template_commands
from cafe.ui.commands import catalog as catalog_commands
from cafe.ui.commands import workflow as workflow_commands
from cafe.ui.commands import audit as audit_commands
from cafe.ui.cli_shared import (
    CONTENT_TYPE_FILE_MAP as _SHARED_CONTENT_TYPE_FILE_MAP,
    VALID_CONTENT_TYPES as _SHARED_VALID_CONTENT_TYPES,
    display_iteration_delta as _shared_display_iteration_delta,
    find_latest_iteration_dir as _shared_find_latest_iteration_dir,
    get_latest_review_iteration as _shared_get_latest_review_iteration,
    get_latest_versioned_file as _shared_get_latest_versioned_file,
    get_show_file_path as _shared_get_show_file_path,
    resolve_iteration_index as _shared_resolve_iteration_index,
    resolve_iteration_number as _shared_resolve_iteration_number,
    setup_agents as _shared_setup_agents,
    # Back-imports: functions moved to cli_shared that cli.py still calls directly
    _load_playbook_step_names,
    _resolve_issue_playbook_name,
)
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler as _CompatPermissionHandler
from cafe.core.types import CriticalPhaseError as _CompatCriticalPhaseError
from cafe.core.workflow_models import StepExecutionResult as _CompatStepExecutionResult
from cafe.templates.manager import TemplateManager
from cafe.ui import init_helpers as _compat_init_helpers
from cafe.ui.chat import get_chat_next_step_path as _compat_get_chat_next_step_path, launch_chat_session
from cafe.ui.display import Display
from cafe.ui.init_helpers import (
    check_available_clis,
    copy_agents_to_local,
    copy_templates_to_local,
    list_available_agents,
)
from cafe.ui.phase_prompts import prompt_for_input_method, prompt_for_rigor
from cafe.ui.template_selector import select_template
from cafe.utils.config import ConfigManager
from cafe.utils.git_utils import is_branch_initialized  # noqa: F401 — backward-compat re-export for test patch targets
from cafe.utils.github import (  # noqa: F401 — backward-compat re-exports for test patch targets
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
    mismatch = _resolve_repo_entrypoint_mismatch(
        cwd=cwd,
        imported_cli_file=imported_cli_file,
    )
    if mismatch is None:
        return None

    repo_root, expected_cli, actual_cli = mismatch

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


def _resolve_repo_entrypoint_mismatch(
    *,
    cwd: Optional[Path] = None,
    imported_cli_file: Optional[Path] = None,
) -> Optional[tuple[Path, Path, Path]]:
    """Return checkout/expected/actual paths when a CLI install mismatch exists."""
    repo_root = _find_repo_checkout_root(cwd)
    if repo_root is None:
        return None

    expected_cli = (repo_root / "src" / "cafe" / "ui" / "cli.py").resolve()
    actual_cli = (imported_cli_file or Path(__file__)).resolve()
    if actual_cli == expected_cli:
        return None
    return repo_root, expected_cli, actual_cli


def _build_repo_entrypoint_reexec_command(repo_root: Path) -> list[str]:
    """Build a command that runs the CLI from the detected checkout."""
    return [str(Path(sys.executable).resolve()), "-m", "cafe.ui.cli", *sys.argv[1:]]


def _build_repo_entrypoint_reexec_env(repo_root: Path) -> dict[str, str]:
    """Build an environment that imports CAFE from the detected checkout."""
    env = os.environ.copy()
    checkout_src = str((repo_root / "src").resolve())
    current_pythonpath = env.get("PYTHONPATH", "")
    if current_pythonpath:
        env["PYTHONPATH"] = os.pathsep.join([checkout_src, current_pythonpath])
    else:
        env["PYTHONPATH"] = checkout_src
    return env


def _reexec_repo_entrypoint(repo_root: Path) -> None:
    """Replace the current process with the checkout-local CLI."""
    os.execvpe(
        _build_repo_entrypoint_reexec_command(repo_root)[0],
        _build_repo_entrypoint_reexec_command(repo_root),
        _build_repo_entrypoint_reexec_env(repo_root),
    )


def _check_repo_entrypoint_alignment() -> bool:
    """Fail fast when running inside a checkout but importing a different install."""
    if os.getenv("CAFE_SKIP_ENTRYPOINT_CHECK"):
        return True
    mismatch = _resolve_repo_entrypoint_mismatch()
    if mismatch is None:
        return True
    repo_root, _, _ = mismatch
    try:
        _reexec_repo_entrypoint(repo_root)
    except OSError:
        pass

    message = _build_repo_entrypoint_mismatch_message()
    if message is None:
        return True
    console.print(f"[red]{message}[/red]")
    console.print(
        "[yellow]Failed to automatically run the checkout-local CLI. "
        "Use the command above manually.[/yellow]"
    )
    return False


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
VALID_CONTENT_TYPES = _SHARED_VALID_CONTENT_TYPES
CONTENT_TYPE_FILE_MAP = _SHARED_CONTENT_TYPE_FILE_MAP

# Backward-compat re-exports for test patch targets
import shutil as _shutil  # noqa: F401 — re-exported for backward compat
shutil = _shutil
PermissionHandler = _CompatPermissionHandler
StepExecutionResult = _CompatStepExecutionResult
CriticalPhaseError = _CompatCriticalPhaseError
init_helpers = _compat_init_helpers
get_chat_next_step_path = _compat_get_chat_next_step_path

# Backward-compat re-exports for functions moved to cli_shared.
# Tests and integrations import/patch these names from cafe.ui.cli.
from cafe.ui.cli_shared import (  # noqa: F401
    _alias_confirm_output_pause,
    _alias_is_confirmed_transition,
    _alias_is_done,
    _alias_needs_clarification,
    _alias_needs_permission,
    _alias_next_step,
    _alias_status,
    _alias_targets,
    _build_workflow_pause_guidance,
    _build_workflow_step_executor,
    _check_agent_clis_available,
    _consume_pending_chat_handoff,
    _edit_file_with_editor,
    _edit_latest_phase_artifact,
    _execute_next_phase_auto,
    _execute_single_step_alias,
    _find_external_resume_step,
    _find_incomplete_workflow_step,
    _get_and_validate_branch,
    _handle_phase_exception,
    _handle_user_phase,
    _load_issue_step_names,
    _print_legacy_phase_command_notice,
    _print_workflow_pause_guidance,
    _reject_unsupported_phase_options,
    _resolve_selected_playbook,
    _run_iterative_alias_step,
)











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





def _display_iteration_delta(
    iteration_count: int,
    output_file: Optional[str],
    console: Console,
) -> None:
    """Display delta between current and previous iteration output files."""
    _shared_display_iteration_delta(iteration_count, output_file, console)





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


app.command(hidden=True)(phases_legacy_commands.spec)
app.command(hidden=True)(phases_legacy_commands.plan)
app.command(hidden=True)(phases_legacy_commands.develop)
app.command(hidden=True)(phases_legacy_commands.review)
app.command(hidden=True)(phases_legacy_commands.pr)


app.command()(issues_commands.config)
app.command(name="ls")(issues_commands.list_issues)
app.command(name="rm")(issues_commands.remove_issue)


app.command()(workflow_commands.make)
app.command()(workflow_commands.show)
app.command()(workflow_commands.summary)
app.command()(workflow_commands.workflow)

app.command(name="audit")(audit_commands.audit)


# Backward-compatible module-level command aliases for tests and integrations.
prepare = lifecycle_commands.prepare
close = lifecycle_commands.close
restore = lifecycle_commands.restore
reset = lifecycle_commands.reset

spec = phases_legacy_commands.spec
plan = phases_legacy_commands.plan
develop = phases_legacy_commands.develop
review = phases_legacy_commands.review
pr = phases_legacy_commands.pr

config = issues_commands.config
list_issues = issues_commands.list_issues
remove_issue = issues_commands.remove_issue

make = workflow_commands.make
show = workflow_commands.show
summary = workflow_commands.summary
workflow = workflow_commands.workflow


# Template management commands
app.add_typer(template_commands.template_app, name="template")

# Backward-compatible alias for TEMPLATE_TYPES (now defined in templates module)
TEMPLATE_TYPES = template_commands.TEMPLATE_TYPES



# Agent management commands (similar to template commands)
agent_app = typer.Typer(help="Manage agents")
app.add_typer(agent_app, name="agent")

# Playbook and skill management commands
app.add_typer(catalog_commands.playbook_app, name="playbook")
app.add_typer(catalog_commands.skill_app, name="skill")


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


@agent_app.command(name="ls")
def agent_ls(
    custom_only: bool = typer.Option(False, "--custom-only", help="Show only custom agents"),
) -> None:
    """List all available agents (system and custom)."""
    _print_agents(custom_only)


@agent_app.command(name="rm")
def agent_rm() -> None:
    """Remove an agent interactively."""
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


def main() -> Optional[int]:
    """Entry point for CLI."""
    # Check if all dependencies are installed
    _check_dependencies()
    if not _check_repo_entrypoint_alignment():
        return 1
    # Check for updates and auto-upgrade if available
    _check_for_updates()
    app()
    return None


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
    sys.exit(main())
