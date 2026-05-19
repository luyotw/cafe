"""Shared helper functions and constants used by the CLI module.

NOTE: Several helpers in this module use late imports from ``cafe.ui.cli``
(via ``_lazy_get_*`` getters) to avoid circular imports. Do NOT add
top-level imports of ``cafe.ui.cli`` here — always use the lazy getters
or local imports inside function bodies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.workflow_models import BatonRejected
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.services.delta_display import DeltaDisplay
from cafe.skills.loader import SkillLoader
from cafe.ui.chat import launch_chat_session  # noqa: F401 — re-exported via cli for test-patch compat
from cafe.ui.inquirer_prompts import prompt_confirm, prompt_list, prompt_multiline  # noqa: F401 — re-exported via cli for test-patch compat
from cafe.utils.config import ConfigError, ConfigManager
from cafe.utils.crew import CrewManager, normalize_role_config

VALID_CONTENT_TYPES = [
    "context",
    "output",
    "streaming",
    "error",
    "status",
    "iterations",
    "checklist",
    "user_input",
    "questions",
]

CONTENT_TYPE_FILE_MAP = {
    "context": "iteration.json",
    "output": "output.md",
    "streaming": "streaming.jsonl",
    "error": "error.json",
    "status": "status.json",
    "iterations": "iterations.jsonl",
    "checklist": "checklist.md",
    "user_input": "user_input.md",
    "questions": "questions.xml",
}

# Phases used as a fallback when a playbook cannot be loaded.
ALL_PHASES = ["spec", "plan", "develop", "review", "pr"]

console = Console()


def _get_git_operations_cls():
    """Lazy import of GitOperations from cafe.ui.cli for test-patch compatibility.

    Tests patch ``cafe.ui.cli.GitOperations``; importing here at call-time
    ensures the patched version is picked up.
    """
    from cafe.ui.cli import GitOperations
    return GitOperations


def _get_is_branch_initialized():
    """Lazy import of is_branch_initialized from cafe.ui.cli for test-patch compatibility."""
    from cafe.ui.cli import is_branch_initialized
    return is_branch_initialized


def _get_github_ops_cls():
    """Lazy import of GitHubOps from cafe.ui.cli for test-patch compatibility."""
    from cafe.ui.cli import GitHubOps
    return GitHubOps


def _get_github_helpers():
    """Lazy import of GitHub helper functions for test-patch compatibility."""
    from cafe.utils.github import (
        filter_unresolved_comments,
        get_all_pr_comments,
    )
    return get_all_pr_comments, filter_unresolved_comments


def check_agent_clis_available(config_manager: ConfigManager) -> List[str]:
    """Check if all configured agent CLIs are installed."""
    try:
        crew_data = CrewManager(cafe_dir=Path(config_manager.config_dir)).load()
    except (AttributeError, TypeError):
        crew_data = {}

    def _role_cli(role: str, default_cli: str) -> str:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return crew_data[role].get("cli", default_cli)
        val = config_manager.get(f"agents.{role}", {})
        return val.get("cli", default_cli) if isinstance(val, dict) else default_cli

    pm_config = {"cli": _role_cli("pm", "copilot")}
    dev_config = {"cli": _role_cli("developer", "copilot")}
    reviewer_config = {"cli": _role_cli("reviewer", "copilot")}

    required_clis = [pm_config["cli"], dev_config["cli"], reviewer_config["cli"]]

    missing_clis = []
    for cli in required_clis:
        if shutil.which(cli) is None and cli not in missing_clis:
            missing_clis.append(cli)

    return missing_clis


def setup_agents(
    config_manager: ConfigManager,
    issue_name: Optional[str] = None,
    phase_name: Optional[str] = None,
    cafe_dir: Optional[Path] = None,
) -> AgentManager:
    """Setup agent manager with configured role agents.

    Reads from crew.yaml first; falls back to config.yaml agents: section for backward compat.
    """
    agent_manager = AgentManager(issue_name=issue_name)

    # Prefer crew.yaml; fall back to config.yaml agents: section
    try:
        _cafe_dir = Path(cafe_dir) if cafe_dir else Path(config_manager.config_dir)
        crew_data = CrewManager(cafe_dir=_cafe_dir).load()
    except (AttributeError, TypeError):
        crew_data = {}

    def _role_config(role: str, default_name: str) -> dict:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return crew_data[role]
        return config_manager.get(f"agents.{role}", {"name": default_name, "cli": "copilot"})

    pm_config = _role_config("pm", "Roger")
    dev_config = _role_config("developer", "David")
    reviewer_config = _role_config("reviewer", "Richard")

    def _build_agent_config(role_config: dict, default_name: str) -> AgentConfig:
        chain = normalize_role_config(role_config)

        # Seed models_config from raw models: dict for backward compat
        # (manager._try_backup_agents reads it; callers may also inspect it)
        raw_models = role_config.get("models", {}) or {}
        models_config: Dict[str, Dict[str, str]] = {}
        if isinstance(raw_models, dict):
            for cli_name, phase_map in raw_models.items():
                if isinstance(phase_map, dict):
                    models_config[cli_name] = {k: str(v) for k, v in phase_map.items()}

        if chain:
            primary = chain[0]
            cli = primary.cli
            model = primary.resolve_model(phase_name)
            backup_clis = [e.cli for e in chain[1:]]
            # Merge chain entries into models_config (chain takes priority)
            for entry in chain:
                if entry.phase_models:
                    models_config[entry.cli.value] = dict(entry.phase_models)
        else:
            # No valid CLI in role config; fall back to copilot with no model
            cli = AgentCLI.COPILOT
            model = None
            backup_clis = []

        return AgentConfig(
            name=role_config.get("name", default_name),
            cli=cli,
            model=model,
            clis=chain,
            backup_clis=backup_clis,
            models_config=models_config,
        )

    agent_manager.register_agent(_build_agent_config(pm_config, "Roger"))
    agent_manager.register_agent(_build_agent_config(dev_config, "David"))
    agent_manager.register_agent(_build_agent_config(reviewer_config, "Richard"))

    return agent_manager


def get_latest_versioned_file(phase_name: str, issue_name: str) -> Optional[Path]:
    """Get the latest output.md for a phase."""
    phase_dir = Path(f".cafe/issues/{issue_name}/{phase_name}")
    if not phase_dir.exists():
        return None

    output_files = sorted(phase_dir.glob("iteration_*/output.md"))
    if output_files:
        return output_files[-1]
    return None


def find_latest_iteration_dir(phase_dir: Path) -> Optional[Path]:
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


def resolve_iteration_index(iteration_numbers: List[int], iteration_input: int) -> int:
    """Resolve concrete iteration number from positive/zero/negative input."""
    if not iteration_numbers:
        raise ValueError("No iterations available")

    if iteration_input == 0:
        return iteration_numbers[-1]
    if iteration_input > 0:
        if iteration_input not in iteration_numbers:
            raise ValueError(
                f"Iteration {iteration_input} not found. "
                f"Available iterations: {iteration_numbers}"
            )
        return iteration_input

    try:
        return iteration_numbers[iteration_input - 1]
    except IndexError:
        raise ValueError(
            f"Iteration index {iteration_input} out of range. "
            f"Available iterations: {iteration_numbers}"
        )


def resolve_iteration_number(phase_dir: Path, iteration_input: int, content_type: str) -> int:
    """Resolve iteration number that contains the requested content file."""
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    iter_dirs = sorted(d for d in phase_dir.glob("iteration_*") if d.is_dir())
    existing_iter_dirs = [
        d for d in iter_dirs
        if (d / "iteration.json").exists() or (d / "context.json").exists()
    ]
    if not existing_iter_dirs:
        raise ValueError(f"No iterations found in {phase_dir}")

    all_iteration_numbers = []
    for iter_dir in existing_iter_dirs:
        dir_name = iter_dir.name
        if dir_name.startswith("iteration_"):
            try:
                num = int(dir_name.split("_")[1])
                all_iteration_numbers.append(num)
            except (IndexError, ValueError):
                continue

    if not all_iteration_numbers:
        raise ValueError(f"No valid iterations found in {phase_dir}")

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

    return resolve_iteration_index(iteration_numbers_with_file, iteration_input)


def get_show_file_path(phase_dir: Path, iteration: int, content_type: str) -> Path:
    """Get path for a show-command content type."""
    filename = CONTENT_TYPE_FILE_MAP.get(content_type)
    if not filename:
        raise ValueError(f"Unknown content type: {content_type}")

    if content_type in ["status", "iterations"]:
        return phase_dir / filename

    iteration_dir = phase_dir / f"iteration_{iteration:03d}"
    return iteration_dir / filename


def get_latest_review_iteration(issue_name: str) -> int:
    """Get latest review iteration number from directory names."""
    review_dir = Path(f".cafe/issues/{issue_name}/review")
    if not review_dir.exists():
        return 0

    iteration_dirs = sorted(review_dir.glob("iteration_*"))
    if not iteration_dirs:
        return 0

    latest_dir = iteration_dirs[-1]
    try:
        return int(latest_dir.name.split("_")[1])
    except (IndexError, ValueError):
        return 0


def display_iteration_delta(
    iteration_count: int,
    output_file: Optional[str],
    console: Console,
) -> None:
    """Display delta between current and previous iteration output files."""
    if iteration_count > 1 and output_file:
        current_file = Path(output_file)
        iteration_dir = current_file.parent
        phase_dir = iteration_dir.parent
        prev_iteration_num = iteration_count - 1
        previous_file = phase_dir / f"iteration_{prev_iteration_num:03d}" / "output.md"

        delta_display = DeltaDisplay()
        delta_display.display_delta(current_file, previous_file, console)


# ---------------------------------------------------------------------------
# Backward-compatible aliases (underscore-prefixed wrappers used by
# command modules that import from this file).
# ---------------------------------------------------------------------------
_check_agent_clis_available = check_agent_clis_available
_setup_agents = setup_agents
_find_latest_iteration_dir = find_latest_iteration_dir
_get_latest_versioned_file = get_latest_versioned_file


# ---------------------------------------------------------------------------
# Workflow and phase helper functions (moved from cli.py)
# ---------------------------------------------------------------------------

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


def _build_workflow_role_agent_map(config_manager: ConfigManager, playbook_data: Dict[str, Any]) -> Dict[str, str]:
    """Resolve playbook roles to configured agent names."""
    try:
        crew_data = CrewManager(cafe_dir=Path(config_manager.config_dir)).load()
    except (AttributeError, TypeError):
        crew_data = {}

    def _agent_name(role: str, default: str) -> str:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return str(crew_data[role].get("name", default))
        return str(config_manager.get(f"agents.{role}.name", default))

    mapping: Dict[str, str] = {
        "pm": _agent_name("pm", "Roger"),
        "developer": _agent_name("developer", "David"),
        "reviewer": _agent_name("reviewer", "Richard"),
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
    extra_allowed_directories: Optional[List[str]] = None,
) -> GenericWorkflowStepExecutor:
    """Create the GenericPhase-backed executor for workflow steps."""
    role_agent_map = _build_workflow_role_agent_map(config_manager, playbook_data)
    if role_agent_map_override:
        role_agent_map.update(role_agent_map_override)
    try:
        crew_data = CrewManager(cafe_dir=Path(config_manager.config_dir)).load()
    except (AttributeError, TypeError):
        crew_data = {}

    def _role_cfg(role: str) -> dict:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return crew_data[role]
        return config_manager.get(f"agents.{role}", {})

    role_configs = {
        "pm": _role_cfg("pm"),
        "developer": _role_cfg("developer"),
        "reviewer": _role_cfg("reviewer"),
    }
    return GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=issue_name,
        playbook=playbook_data,
        generic_phase=generic_phase,
        agent_manager=setup_agents(config_manager, issue_name=issue_name, phase_name=phase_name),
        git_ops=_get_git_operations_cls()(),
        role_agent_map=role_agent_map,
        role_configs=role_configs,
        step_user_inputs=step_user_inputs,
        interactive=interactive,
        config_allowed_directories=config_manager.get_allowed_directories(),
        extra_allowed_directories=extra_allowed_directories,
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

    try:
        blackboard = store.load_or_create(
            str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
            playbook_id=str(playbook_data["playbook"]["id"]),
            allow_legacy_text=True,
        )
        contract = store.load_handoff_contract(
            blackboard,
            allowed_steps=list(playbook_data["steps"].keys()),
            allow_legacy_text=True,
        )
    except BatonRejected:
        # Leave invalid structured batons for the workflow runtime; it can
        # feed the schema error back to the step agent and ask for a rewrite.
        return None

    # `next_step.txt` is now persistent from workflow initialization onward.
    # Ignore the bootstrap/persistent baton itself; only consume a chat-authored
    # pending handoff (or legacy step-name text) when the baton meaning is real.
    if contract.source in {"bootstrap", "chat.bootstrap"}:
        return None

    target_step = contract.to_step
    if target_step not in {"user", "done"} and _get_git_operations_cls()().has_uncommitted_changes():
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

        last_iter_dir = iteration_dirs[-1]
        context_file = (
            last_iter_dir / "iteration.json"
            if (last_iter_dir / "iteration.json").exists()
            else last_iter_dir / "context.json"
        )
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
    git_ops: Any,
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
            existing_pr = _get_github_ops_cls()().get_pr_for_branch(branch_name)
        except Exception:
            return None
        if not existing_pr:
            return None

        try:
            from cafe.utils.github import load_pr_last_seen_comment_ids

            _get_comments, _filter_comments = _get_github_helpers()
            exclude_ids = load_pr_last_seen_comment_ids(issue_dir / step_name)
            comments = _get_comments(int(existing_pr["number"]), exclude_ids=exclude_ids)
            unresolved_comments = _filter_comments(comments)
        except Exception as exc:
            console.print(
                "[red]Error:[/red] could not evaluate unresolved PR discussion for external resume "
                f"({exc}). Leaving workflow paused."
            )
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

    # Check if this is a review-confirmation handoff from spec/plan.
    # When the handoff intent is confirm_output, show the output and
    # offer Confirm / Request modification instead of the generic menu.
    store = BlackboardStore(issue_dir)
    contract = getattr(blackboard, "handoff_contract", None)
    handoff_intent = getattr(contract, "intent", None) if contract else None
    from_step = getattr(contract, "from_step", None) if contract else None

    if handoff_intent == HandoffIntent.CONFIRM_OUTPUT and from_step in {"spec", "plan"}:
        return _handle_review_confirmation(
            issue_name=issue_name,
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            store=store,
            from_step=from_step,
            phase_labels=phase_labels,
            summary=summary,
        )

    if handoff_intent == HandoffIntent.NEED_CLARIFICATION and from_step in {"spec", "plan"}:
        return _handle_clarification_handoff(
            issue_name=issue_name,
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            store=store,
            from_step=from_step,
            summary=summary,
        )

    # Default generic user-phase menu
    return _handle_user_phase_generic(
        issue_name=issue_name,
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        phase_name=phase_name,
        phase_labels=phase_labels,
        summary=summary,
    )


def _handle_clarification_handoff(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    store: BlackboardStore,
    from_step: str,
    summary: str,
) -> Optional[str]:
    """Handle need_clarification handoff by collecting answers and resuming."""
    console.print(f"[yellow]Clarification requested[/yellow] step={from_step}")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    from_step_dir = issue_dir / from_step
    iteration_dirs: List[Path] = []
    latest_iter_dir: Optional[Path] = None

    def _refresh_iteration_state() -> Optional[Path]:
        nonlocal iteration_dirs, latest_iter_dir
        iteration_dirs = sorted(from_step_dir.glob("iteration_*")) if from_step_dir.exists() else []
        latest_iter_dir = iteration_dirs[-1] if iteration_dirs else None
        return latest_iter_dir

    def _display_latest_output() -> None:
        current_iter_dir = _refresh_iteration_state()
        if current_iter_dir is not None:
            _print_output_file(current_iter_dir / "output.md")

    def _reload_after_chat():
        _display_latest_output()
        current_iter_dir = latest_iter_dir
        current_questions_file = current_iter_dir / "questions.xml" if current_iter_dir is not None else None
        if current_questions_file is None or not current_questions_file.exists():
            return None
        from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml

        if not validate_questions_xml(current_questions_file):
            return None
        return parse_questions_xml(current_questions_file)

    _display_latest_output()

    user_input = ""
    questions_file = latest_iter_dir / "questions.xml" if latest_iter_dir is not None else None
    if questions_file is not None and questions_file.exists():
        from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
        from cafe.ui.interactive_qa import interactive_qa_flow

        if validate_questions_xml(questions_file):
            step_def = playbook_data.get("steps", {}).get(from_step, {})
            role = str(step_def.get("role", "developer"))
            prompt_role = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
            role_names = playbook_data.get("roles", {})
            agent_name = role_names.get(role, {}).get("default_agent", role)
            user_input = interactive_qa_flow(
                parse_questions_xml(questions_file),
                role=prompt_role,
                issue_name=issue_name,
                agent_name=agent_name,
                after_chat=_reload_after_chat,
            )

    if not user_input:
        from cafe.ui.inquirer_prompts import prompt_multiline

        user_input = prompt_multiline(
            f"Answer the pending clarification for {from_step}",
            default="",
        ).strip()

    if not user_input:
        console.print("[dim]No answer provided; workflow remains paused.[/dim]")
        return None

    next_iter = len(iteration_dirs) + 1
    next_iter_dir = from_step_dir / f"iteration_{next_iter:03d}"
    next_iter_dir.mkdir(parents=True, exist_ok=True)
    (next_iter_dir / "user_input.md").write_text(user_input, encoding="utf-8")

    store.set_current_step(blackboard, from_step)
    store.set_handoff_summary(blackboard, f"User answered clarification for {from_step}")
    store.update_handoff_contract(
        blackboard,
        from_step=from_step,
        to_owner=HandoffOwner.AGENT,
        to_step=from_step,
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="",
        source="user.clarification_answers",
    )
    store.record_event(
        blackboard,
        "clarification_answered",
        {"step": from_step, "source": "questions_xml" if questions_file and questions_file.exists() else "prompt"},
    )
    console.print(f"[green]Clarification answered[/green] -> back to {from_step}")
    return from_step


def _handle_review_confirmation(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    store: BlackboardStore,
    from_step: str,
    phase_labels: Dict[str, str],
    summary: str,
) -> Optional[str]:
    """Handle confirm_output handoff: display output and ask for confirmation."""
    console.print(f"[yellow]Review requested[/yellow] step={from_step}")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    # Display the output file
    from_step_dir = issue_dir / from_step
    output_files = sorted(from_step_dir.glob("iteration_*/output.md")) if from_step_dir.exists() else []
    if output_files:
        latest_output = output_files[-1]
        # Show delta if there's a previous iteration
        iteration_num = int(latest_output.parent.name.split("_")[1])
        if iteration_num > 1:
            prev_output = from_step_dir / f"iteration_{iteration_num - 1:03d}" / "output.md"
            if prev_output.exists():
                delta_display = DeltaDisplay()
                delta_display.display_delta(latest_output, prev_output, console)
            else:
                _print_output_file(latest_output)
        else:
            _print_output_file(latest_output)

    # Resolve the successor step for the confirmed phase
    step_def = playbook_data.get("steps", {}).get(from_step, {})
    transitions = step_def.get("on", {})
    confirmed_target = transitions.get("await_agent") or transitions.get("confirmed")
    # Fallback: first transition target that isn't self
    if not confirmed_target:
        for target in transitions.values():
            if target != from_step:
                confirmed_target = target
                break

    role_map = {"spec": "pm", "plan": "developer"}.get(from_step, "developer")
    role_names = playbook_data.get("roles", {})
    agent_name = role_names.get(role_map, {}).get("default_agent", role_map.capitalize())

    from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline

    while True:
        choices = [
            {"name": "Confirm - Continue", "value": "confirm"},
            {"name": "Request modification - Send feedback", "value": "modify"},
            {"name": "Open chat with role", "value": "chat"},
            {"name": "More options...", "value": "more"},
        ]
        action = prompt_list(
            f"{from_step.capitalize()} is ready for review. Please select an option",
            choices,
            default=None,
        )

        if action == "confirm":
            if confirmed_target and confirmed_target in playbook_data.get("steps", {}):
                target_step = confirmed_target
            else:
                target_step = from_step
            store.set_current_step(blackboard, target_step)
            store.set_handoff_summary(blackboard, f"{from_step} confirmed by user")
            store.update_handoff_contract(
                blackboard,
                from_step=from_step,
                to_owner=HandoffOwner.AGENT,
                to_step=target_step,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code="confirmed",
                source="user.review_confirmation",
            )
            store.record_event(
                blackboard,
                "review_confirmed",
                {"step": from_step, "to_step": target_step},
            )
            console.print(f"[green]Confirmed[/green] {from_step} -> {target_step}")
            return str(target_step)

        if action == "modify":
            feedback = prompt_multiline(
                "What changes would you like to request?",
                default="",
            ).strip()
            if not feedback:
                feedback = "Please review and revise as needed."
            store.set_current_step(blackboard, from_step)
            store.set_handoff_summary(blackboard, f"User requested changes: {feedback}")
            store.update_handoff_contract(
                blackboard,
                from_step=from_step,
                to_owner=HandoffOwner.AGENT,
                to_step=from_step,
                intent=HandoffIntent.NEED_CLARIFICATION,
                status_code="need_clarification",
                source="user.review_modification",
            )
            # Write feedback as user_input for the next iteration
            iteration_dirs = sorted(from_step_dir.glob("iteration_*")) if from_step_dir.exists() else []
            next_iter = len(iteration_dirs) + 1
            next_iter_dir = from_step_dir / f"iteration_{next_iter:03d}"
            next_iter_dir.mkdir(parents=True, exist_ok=True)
            (next_iter_dir / "user_input.md").write_text(feedback, encoding="utf-8")
            store.record_event(
                blackboard,
                "review_modification_requested",
                {"step": from_step, "feedback": feedback},
            )
            console.print(f"[yellow]Modification requested[/yellow] -> back to {from_step}")
            return str(from_step)

        if action == "chat":
            from cafe.ui.cli import launch_chat_session as _lcs
            _lcs(role_map, issue_name)
            from cafe.ui.cli import _consume_pending_chat_handoff as _cpch
            target_step = _cpch(
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
            # Chat didn't produce a handoff; re-display output and re-ask
            if output_files:
                _print_output_file(output_files[-1])
            continue

        if action == "more":
            # Fall through to the generic user-phase menu
            return _handle_user_phase_generic(
                issue_name=issue_name,
                issue_dir=issue_dir,
                playbook_data=playbook_data,
                blackboard=blackboard,
                phase_name="user",
                phase_labels=phase_labels,
                summary=summary,
            )

    return None


def _print_output_file(output_file: Path) -> None:
    """Print the content of an output.md file to the console."""
    if output_file.exists():
        content = output_file.read_text(encoding="utf-8")
        console.print()
        console.print(f"[bold]{'=' * 60}[/bold]")
        console.print(content)
        console.print(f"[bold]{'=' * 60}[/bold]")
        console.print()


def _handle_user_phase_generic(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    phase_name: str,
    phase_labels: Dict[str, str],
    summary: str,
) -> Optional[str]:
    """Generic user-phase menu (original _handle_user_phase behavior)."""
    if phase_name == "done":
        console.print("[green]Workflow already completed[/green] step=done")
        console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
    else:
        console.print("[yellow]Workflow is waiting for user input[/yellow] step=user")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    from cafe.ui.cli import prompt_list as _pl
    action = _pl(
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
        from cafe.ui.cli import prompt_multiline as _pml
        note = _pml(
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
        from cafe.ui.cli import prompt_list as _pl2
        selected_label = _pl2(
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
        from cafe.ui.cli import prompt_confirm as _pc
        if not _pc("Write this handoff to the blackboard and continue now?"):
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
        from cafe.ui.cli import prompt_list as _pl3
        role = _pl3("Select role", role_choices)
        from cafe.ui.cli import launch_chat_session as _lcs
        _lcs(str(role), issue_name)
        from cafe.ui.cli import _consume_pending_chat_handoff as _cpch
        target_step = _cpch(
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
        allow_legacy_text=True,
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
    # Use late import from cli for test-patch compatibility
    from cafe.ui.cli import _build_workflow_step_executor as _bwse
    step_executor = _bwse(
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
        allow_legacy_text=True,
    )
    handoff = store.load_handoff_contract(
        latest_blackboard,
        allowed_steps=list(playbook_data["steps"].keys()),
        allow_legacy_text=True,
    )
    latest_iteration_dir = find_latest_iteration_dir(issue_dir / step_name)
    iteration = None
    if latest_iteration_dir is not None:
        try:
            iteration = int(latest_iteration_dir.name.split("_")[1])
        except (IndexError, ValueError):
            iteration = None
    output_file = get_latest_versioned_file(step_name, issue_name)
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
                invalid_codes = data.get("invalid_intents")
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
    return _alias_targets(alias_result, step_name) or _alias_status(alias_result) == "confirmed"


def _alias_needs_clarification(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "need_clarification") or _alias_status(alias_result) == "need_clarification"


def _alias_needs_permission(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "need_permission") or _alias_status(alias_result) == "need_permission"


def _alias_confirm_output_pause(alias_result: Dict[str, Any]) -> bool:
    return _alias_pause_intent(alias_result, "confirm_output") or _alias_status(alias_result) == "ready_for_review"


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


def _edit_latest_phase_artifact(
    *,
    ctx: typer.Context,
    phase_name: str,
    missing_hint: str,
) -> None:
    """Open the latest phase artifact in the user's editor."""
    from cafe.ui.cli import _get_and_validate_branch as _gavb
    issue_name = _gavb(ctx, phase_name)
    phase_file = get_latest_versioned_file(phase_name, issue_name)
    if not phase_file:
        console.print(f"[red]Error: No {phase_name} file found for issue '{issue_name}'[/red]")
        console.print(f"[dim]Hint: {missing_hint}[/dim]")
        raise typer.Exit(1)

    from cafe.ui.cli import _edit_file_with_editor as _efe
    _efe(phase_file)


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

        from cafe.ui.cli import _execute_single_step_alias as _essa
        alias_result = _essa(
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
        if handoff_intent == "need_clarification" or status_code == "need_clarification":
            console.print("[yellow]💬 Agent needs clarification[/yellow]")
            _display_iteration_questions(issue_name=issue_name, step_name=step_name, alias_result=alias_result)
        else:
            console.print("[yellow]📝 Draft ready for review[/yellow]")

        from cafe.ui.cli import prompt_confirm as _pc2
        should_continue = auto or _pc2("Continue to next iteration?", default=True)
        if not should_continue:
            console.print("[dim]Stopped by user.[/dim]")
            return alias_result

        if (handoff_intent == "need_clarification" or status_code == "need_clarification") and clarification_prompt:
            from cafe.ui.cli import prompt_multiline as _pml2
            current_input = _pml2(clarification_prompt).strip() or current_input
        iteration_count += 1
        console.print("[dim]Continuing...[/dim]")


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
    git = _get_git_operations_cls()()
    try:
        if not git.is_valid_branch():
            console.print(
                "[red]Error: You are not currently on a valid Git branch. "
                "Please checkout a branch first.[/red]"
            )
            raise typer.Exit(1)

        branch_name = git.get_current_branch()

        # Check if branch is initialized
        if not _get_is_branch_initialized()(branch_name):
            console.print(
                "[red]Error: This branch has not been initialized. "
                "Please run 'cafe prepare' first.[/red]"
            )
            raise typer.Exit(1)

        return branch_name

    except Exception as e:
        console.print(f"[red]Error: Failed to get current branch: {e}[/red]")
        raise typer.Exit(1)


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
