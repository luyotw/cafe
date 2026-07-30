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
from cafe.utils.config import ConfigError, ConfigManager
from cafe.utils.crew import CrewManager, normalize_role_config
from cafe.utils.phase_config import load_phase_step_model
from cafe.utils.git_utils import get_repo_root, get_git_toplevel

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

_ALIGNMENT_DIRECT_MENU_DECISIONS = (
    ("approve", "Approve and continue"),
    ("strategic_documents_updated", "Strategic documents updated"),
    ("manual_pause", "Pause for manual decision"),
)


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


def check_agent_clis_available(
    config_manager: ConfigManager,
    *,
    active_step: Optional[str] = None,
    active_role: Optional[str] = None,
    phase_config_local_path: Optional[Path] = None,
    phase_config_repo_path: Optional[Path] = None,
) -> List[str]:
    """Check if all configured agent CLIs are installed."""
    try:
        config_dir = getattr(config_manager, "config_dir", None)
        crew_data = (
            CrewManager(cafe_dir=Path(config_dir)).load()
            if isinstance(config_dir, (str, Path))
            else {}
        )
    except (AttributeError, TypeError):
        crew_data = {}

    def _role_config(role: str, default_cli: str) -> dict:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return crew_data[role]
        val = config_manager.get(f"agents.{role}", {})
        return val if isinstance(val, dict) else {"cli": default_cli}

    def _configured_chain(config: dict) -> list[str]:
        chain = [entry.cli.value for entry in normalize_role_config(config)]
        if chain:
            return chain
        cli = config.get("cli")
        return [cli] if isinstance(cli, str) and cli else ["copilot"]

    def _check_chain(chain: list[str], *, context: Optional[str] = None) -> List[str]:
        available = [cli for cli in chain if shutil.which(cli) is not None]
        if available:
            missing_in_chain = [cli for cli in chain if cli not in available]
            if missing_in_chain:
                warning = "[yellow]Warning:[/yellow] Some configured fallback CLIs are not installed: "
                if context is not None:
                    warning = f"[yellow]Warning:[/yellow] Some configured CLIs are not installed ({context}): "
                console.print(warning + ", ".join(dict.fromkeys(missing_in_chain)))
            return []

        missing_clis = []
        for cli in chain:
            if cli not in missing_clis:
                missing_clis.append(cli)
        return missing_clis

    def _resolve_phase_config_paths() -> tuple[Optional[Path], Optional[Path]]:
        if phase_config_local_path is not None or phase_config_repo_path is not None:
            return phase_config_local_path, phase_config_repo_path
        try:
            repo_root = get_repo_root()
            worktree_root = get_git_toplevel()
            return worktree_root / ".cafe" / "phases.yaml", repo_root / ".cafe" / "phases.yaml"
        except Exception:
            if isinstance(config_dir, (str, Path)):
                return Path(config_dir) / "phases.yaml", None
        return None, None

    if active_step is not None and active_step not in {"user", "done"}:
        local_path, repo_path = _resolve_phase_config_paths()
        phase_resolution = load_phase_step_model(
            step_name=active_step,
            local_path=local_path,
            repo_path=repo_path,
        )
        if phase_resolution.clis:
            source_paths = {
                "worktree": local_path.as_posix() if local_path is not None else None,
                "repo": repo_path.as_posix() if repo_path is not None else None,
            }
            phase_config_file = (
                source_paths.get(phase_resolution.clis_source or "")
                or phase_resolution.clis_source
                or phase_resolution.source
                or "phase-config"
            )
            return _check_chain(
                [cli for cli, _model in phase_resolution.clis],
                context=f"file={phase_config_file} step={active_step} field=clis",
            )

        target_role = active_role or phase_resolution.role or {
            "spec": "pm",
            "plan": "developer",
            "develop": "developer",
            "review": "reviewer",
            "pr": "developer",
        }.get(active_step)
        if target_role:
            return _check_chain(
                _configured_chain(_role_config(target_role, "copilot")),
                context=f"step={active_step} field=clis",
            )

    role_configs = [
        _role_config("pm", "copilot"),
        _role_config("developer", "copilot"),
        _role_config("reviewer", "copilot"),
    ]

    missing_clis = []
    for role_config in role_configs:
        chain = _configured_chain(role_config)
        for cli in _check_chain(chain):
            if cli not in missing_clis:
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

    if not crew_data:
        # No crew.yaml in this .cafe, the repo root (worktree fallback), or
        # config.yaml agents. Roles will silently use the default CLI, which is
        # rarely intended — guide the user to configure the role→CLI mapping.
        console.print(
            "[yellow]⚠️  No crew.yaml found[/yellow] (checked this .cafe, the repo "
            "root, and config.yaml agents). Roles will fall back to the default "
            "CLI, which may not be what you want. Create [bold].cafe/crew.yaml[/bold] "
            "at the repo root to map roles → CLI (e.g. via `cafe crew`)."
        )

    def _role_config(role: str, default_name: str) -> dict:
        if crew_data and role in crew_data and isinstance(crew_data[role], dict):
            return crew_data[role]
        return config_manager.get(f"agents.{role}", {"name": default_name, "cli": "copilot"})

    def _resolve_phase_config_paths() -> tuple[Optional[Path], Optional[Path]]:
        local_path = None
        repo_path = None
        try:
            repo_root = get_repo_root()
            worktree_root = get_git_toplevel()
            local_path = worktree_root / ".cafe" / "phases.yaml"
            repo_path = repo_root / ".cafe" / "phases.yaml"
        except Exception:
            fallback_cafe_dir = Path(cafe_dir) if cafe_dir else Path(config_manager.config_dir)
            local_path = fallback_cafe_dir / "phases.yaml"
        return local_path, repo_path

    def _resolve_phase_overrides() -> tuple[Optional[str], Optional[list[dict[str, str]]], Optional[str]]:
        if not issue_name or not phase_name:
            return None, None, None
        try:
            local_path, repo_path = _resolve_phase_config_paths()
            resolved = load_phase_step_model(
                step_name=phase_name,
                local_path=local_path,
                repo_path=repo_path,
            )
        except ValueError as exc:
            raise ValueError(
                f"invalid phase config for step '{phase_name}': {exc}"
            ) from exc
        if not resolved.clis:
            return resolved.name, None, resolved.role

        clis_payload = []
        for cli_name, model in resolved.clis:
            entry = {"cli": cli_name}
            if model is not None:
                entry["model"] = model
            clis_payload.append(entry)
        return resolved.name, clis_payload, resolved.role

    phase_name_override, phase_clis, phase_role = _resolve_phase_overrides()

    def _resolve_phase_target_role() -> Optional[str]:
        if phase_role is not None:
            return phase_role
        if not phase_name:
            return None
        try:
            playbook_name = config_manager.get("settings.playbook", None)
            if not playbook_name:
                playbook_name = config_manager.get("playbook", "default")
            playbook = PlaybookLoader(project_root=get_git_toplevel()).load(str(playbook_name))
            step_def = playbook.get("steps", {}).get(phase_name, {})
            if isinstance(step_def, dict):
                role = step_def.get("role")
                if isinstance(role, str) and role.strip():
                    return role.strip()
        except Exception:
            pass
        return {
            "spec": "pm",
            "plan": "developer",
            "develop": "developer",
            "review": "reviewer",
            "pr": "developer",
        }.get(phase_name)

    phase_target_role = _resolve_phase_target_role()

    def _resolve_phase_model() -> Optional[str]:
        if not phase_name or not issue_name:
            return None

        try:
            local_path, repo_path = _resolve_phase_config_paths()
            resolution = load_phase_step_model(
                step_name=phase_name,
                local_path=local_path,
                repo_path=repo_path,
            )
        except ValueError as exc:
            raise ValueError(
                f"invalid phase config for step '{phase_name}': {exc}"
            ) from exc
        return resolution.model

    pm_config = _role_config("pm", "Roger")
    dev_config = _role_config("developer", "David")
    reviewer_config = _role_config("reviewer", "Richard")

    def _build_models_config(config: dict) -> Dict[str, Dict[str, str]]:
        raw_models = config.get("models", {}) or {}
        models_config: Dict[str, Dict[str, str]] = {}
        if isinstance(raw_models, dict):
            for cli_name, phase_map in raw_models.items():
                if isinstance(phase_map, dict):
                    models_config[cli_name] = {k: str(v) for k, v in phase_map.items()}
        return models_config

    def _build_agent_config(role_config: dict, role: str, default_name: str) -> AgentConfig:
        active_chain = normalize_role_config(role_config)
        agent_name = role_config.get("name", default_name)
        phase_model = _resolve_phase_model() if phase_name else None

        phase_applies_to_role = phase_target_role == role

        if phase_target_role is not None and not phase_applies_to_role:
            active_chain = normalize_role_config(role_config)
            agent_name = str(role_config.get("name", default_name))
        elif phase_applies_to_role and (phase_clis is not None or phase_name_override is not None):
            merged_config = dict(role_config)
            if phase_clis is not None:
                merged_config["clis"] = phase_clis
            if phase_name_override is not None:
                merged_config["name"] = phase_name_override
            agent_name = str(merged_config.get("name", default_name))
            active_chain = normalize_role_config(merged_config)

        models_config = _build_models_config(role_config)

        if active_chain:
            primary = active_chain[0]
            cli = primary.cli
            if phase_clis is not None and phase_model is not None and phase_applies_to_role:
                model = phase_model
            else:
                model = primary.resolve_model(phase_name)
            backup_clis = [e.cli for e in active_chain[1:]]
            for entry in active_chain:
                if entry.phase_models:
                    models_config[entry.cli.value] = dict(entry.phase_models)
            if model is None and phase_model is not None and phase_applies_to_role:
                model = phase_model
        else:
            # No valid CLI in role config; fall back to copilot with no model
            cli = AgentCLI.COPILOT
            model = None
            backup_clis = []

        return AgentConfig(
            name=agent_name,
            cli=cli,
            model=model,
            clis=active_chain,
            backup_clis=backup_clis,
            models_config=models_config,
        )

    agent_manager.register_agent(_build_agent_config(pm_config, "pm", "Roger"))
    agent_manager.register_agent(_build_agent_config(dev_config, "developer", "David"))
    agent_manager.register_agent(_build_agent_config(reviewer_config, "reviewer", "Richard"))

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
        d for d in iter_dirs if (d / "iteration.json").exists() or (d / "context.json").exists()
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

    # playbook 設定存在 settings.playbook 之下（cafe config 寫入處），
    # 舊版讀頂層 "playbook" 永遠取不到、退回 default，使 config 選 playbook 失效。
    selected = config_manager.get("settings.playbook", None)
    if not selected:
        selected = config_manager.get("playbook", "default")
    return str(selected) if selected else "default"


def _build_workflow_role_agent_map(
    config_manager: ConfigManager, playbook_data: Dict[str, Any]
) -> Dict[str, str]:
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
        # LEGACY: This load accepts plain-text `next_step.txt` (v0.1 format) to
        # support chat/CLI handoff bootstrapping. New code should prefer structured
        # baton JSON. See issue #316 for migration plan.
        blackboard = store.load_or_create(
            str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
            playbook_id=str(playbook_data["playbook"]["id"]),
            allow_legacy_text=True,
        )
        # LEGACY: Allow legacy text parsing for the handoff contract here;
        # callers should prefer baton-based handoffs where possible.
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
    if (
        target_step not in {"user", "done"}
        and _get_git_operations_cls()().has_uncommitted_changes()
    ):
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


def _find_incomplete_workflow_step(
    *, issue_dir: Path, playbook_data: Dict[str, Any]
) -> Optional[str]:
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


def _resolve_step_handoff_label(playbook_data: Dict[str, Any], step_name: str) -> str:
    """Return the user-facing handoff label for a playbook step."""
    step_def = playbook_data.get("steps", {}).get(step_name, {})
    if isinstance(step_def, dict):
        label = step_def.get("handoff_label")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return f"Continue {step_name}"


def _resolve_step_chat_role(playbook_data: Dict[str, Any], step_name: str) -> str:
    """Return the role that should handle chat for a playbook step."""
    step_def = playbook_data.get("steps", {}).get(step_name, {})
    roles = playbook_data.get("roles", {})
    if isinstance(step_def, dict):
        chat_role = step_def.get("chat_role")
        if isinstance(chat_role, str) and chat_role.strip():
            return chat_role.strip()
        role = step_def.get("role")
        if isinstance(role, str) and role.strip():
            return role.strip()
    if isinstance(roles, dict) and roles:
        return str(next(iter(roles.keys())))
    return "developer"


def _resolve_role_agent_name(playbook_data: Dict[str, Any], role: str) -> str:
    role_def = playbook_data.get("roles", {}).get(role, {})
    if isinstance(role_def, dict):
        default_agent = role_def.get("default_agent")
        if isinstance(default_agent, str) and default_agent.strip():
            return default_agent.strip()
    return role


def _handle_user_phase(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    phase_name: str = "user",
) -> Optional[str]:
    summary = getattr(blackboard, "handoff_summary", "").strip()

    # Check if this is a review-confirmation handoff from spec/plan.
    # When the handoff intent is confirm_output, show the output and
    # offer Confirm / Request modification instead of the generic menu.
    store = BlackboardStore(issue_dir)
    contract = getattr(blackboard, "handoff_contract", None)
    handoff_intent = getattr(contract, "intent", None) if contract else None
    from_step = getattr(contract, "from_step", None) if contract else None

    if handoff_intent == HandoffIntent.ALIGNMENT_CHECKPOINT and from_step in playbook_data.get(
        "steps", {}
    ):
        return _handle_alignment_checkpoint_handoff(
            issue_name=issue_name,
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            store=store,
            from_step=from_step,
            summary=summary,
        )

    if handoff_intent in {
        HandoffIntent.CONFIRM_OUTPUT,
        HandoffIntent.NEED_CLARIFICATION,
        HandoffIntent.NO_CHANGES_NEEDED,
    } and from_step in playbook_data.get("steps", {}):
        return _handle_declared_human_task_handoff(
            issue_name=issue_name,
            issue_dir=issue_dir,
            blackboard=blackboard,
            from_step=from_step,
            summary=summary,
            playbook_data=playbook_data,
            trigger=handoff_intent.value,
        )

    # Default generic user-phase menu
    return _handle_user_phase_generic(
        issue_name=issue_name,
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        phase_name=phase_name,
        summary=summary,
    )


def _handle_declared_human_task_handoff(
    *,
    issue_name: str,
    issue_dir: Path,
    blackboard,
    from_step: str,
    summary: str,
    playbook_data: Dict[str, Any],
    trigger: str,
) -> Optional[str]:
    """Render and apply any step-declared human task through one shared path."""
    from cafe.core.human_tasks import HumanTaskPolicyError
    from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
    from cafe.ui.human_tasks import (
        apply_human_task_payload,
        collect_human_task_payload,
        latest_step_iteration,
        resolve_step_human_task,
    )

    if summary:
        console.print(f"[dim]{summary}[/dim]")
    try:
        policy, _binding = resolve_step_human_task(
            playbook_data=playbook_data,
            step_name=from_step,
            trigger=trigger,
            iteration=latest_step_iteration(issue_dir=issue_dir, step_name=from_step),
        )
    except HumanTaskPolicyError:
        result = apply_human_task_payload(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            from_step=from_step,
            trigger=trigger,
            raw_payload={},
            source="interactive",
        )
        console.print(f"[red]{result.rejection.message}[/red]")
        return None

    if policy.pattern in {"confirm_output", "no_changes_needed"}:
        output_files = sorted((issue_dir / from_step).glob("iteration_*/output.md"))
        if output_files:
            _print_output_file(output_files[-1])
    questions = None
    if policy.questions_from_xml:
        iteration_dirs = sorted((issue_dir / from_step).glob("iteration_*"))
        questions_file = iteration_dirs[-1] / "questions.xml" if iteration_dirs else None
        if questions_file is not None and questions_file.exists() and validate_questions_xml(questions_file):
            questions = parse_questions_xml(questions_file)
    payload = collect_human_task_payload(
        policy,
        questions=questions,
        role=_resolve_step_chat_role(playbook_data, from_step),
        issue_name=issue_name,
        agent_name=_resolve_role_agent_name(
            playbook_data, _resolve_step_chat_role(playbook_data, from_step)
        ),
    )
    if isinstance(payload, dict) and payload.get("decision") == "chat":
        from cafe.ui.cli import _consume_pending_chat_handoff, launch_chat_session

        role = _resolve_step_chat_role(playbook_data, from_step)
        launch_chat_session(role, issue_name)
        target = _consume_pending_chat_handoff(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            requested_start_step=None,
        )
        if target is not None:
            return target
        return _handle_declared_human_task_handoff(
            issue_name=issue_name,
            issue_dir=issue_dir,
            blackboard=blackboard,
            from_step=from_step,
            summary=summary,
            playbook_data=playbook_data,
            trigger=trigger,
        )
    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        from_step=from_step,
        trigger=trigger,
        raw_payload=payload or {},
        source="interactive",
    )
    if result.rejection is not None:
        console.print(f"[yellow]{result.rejection.message}[/yellow]")
        console.print(f"[dim]{result.rejection.correction_guidance}[/dim]")
        return None
    console.print(f"[green]Completed human task[/green] {policy.id} -> {result.target}")
    return result.target


def parse_alignment_decision_payload(user_input: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse explicit non-interactive alignment decisions.

    Plain text must not approve alignment checkpoints. Non-interactive callers
    have to send JSON so approval is deliberate, for example
    `{"decision":"approve"}` or `{"decision":"narrow","correction":"..."}`.
    """
    if not isinstance(user_input, str) or not user_input.strip():
        return None
    try:
        payload = json.loads(user_input)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    decision = _normalize_alignment_decision(payload.get("decision"))
    if not decision:
        return None
    parsed = dict(payload)
    parsed["decision"] = decision
    return parsed


def apply_alignment_decision_from_payload(
    *,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    payload: Dict[str, Any],
) -> Optional[str]:
    """Apply a parsed non-interactive alignment decision payload."""
    store = BlackboardStore(issue_dir)
    contract = getattr(blackboard, "handoff_contract", None)
    from_step = getattr(contract, "from_step", None) if contract else None
    if not from_step or from_step not in playbook_data.get("steps", {}):
        return None
    request_payload, request_file = _load_latest_alignment_request(issue_dir, str(from_step))
    decision = _normalize_alignment_decision(payload.get("decision")) or str(payload["decision"])
    return _apply_alignment_decision(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        store=store,
        from_step=str(from_step),
        request_payload=request_payload,
        request_file=request_file,
        decision=decision,
        correction=str(payload.get("correction") or payload.get("reason") or "").strip(),
        requires_user_confirmation=decision == "strategic_documents_updated",
        user_confirmation=_alignment_chat_user_confirmation(payload),
    )


def _handle_alignment_checkpoint_handoff(
    *,
    issue_name: str,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    store: BlackboardStore,
    from_step: str,
    summary: str,
) -> Optional[str]:
    """Handle alignment_checkpoint handoff with stable user decisions."""
    console.print(f"[yellow]Alignment checkpoint requested[/yellow] step={from_step}")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    request_payload, request_file = _load_latest_alignment_request(issue_dir, from_step)
    if request_file is not None:
        console.print(f"[dim]Request: {request_file}[/dim]")
    if request_payload:
        console.print()
        console.print("[bold]Alignment summary[/bold]")
        for key, label in (
            ("interpreted_goal", "Goal"),
            ("proposed_scope", "Scope"),
            ("non_scope", "Non-scope"),
            ("risk_level", "Risk"),
            ("strategic_update_recommendation", "Strategic docs"),
            ("decision_requested", "Decision"),
        ):
            value = request_payload.get(key)
            if value:
                console.print(f"  {label}: {value}")
        affected = request_payload.get("affected_documents") or []
        if affected:
            names = [
                f"{item.get('category')}:{item.get('status')}"
                for item in affected
                if isinstance(item, dict)
            ]
            console.print(f"  Affected docs: {', '.join(names)}")
            existing_docs, missing_docs = _alignment_document_status_groups(affected)
            if existing_docs:
                console.print(f"  Existing strategic docs: {', '.join(existing_docs)}")
            if missing_docs:
                console.print(f"  Missing/unconfigured strategic docs: {', '.join(missing_docs)}")
        rules = request_payload.get("triggered_rules") or []
        if rules:
            names = [
                str(item.get("rule_id"))
                for item in rules
                if isinstance(item, dict) and item.get("rule_id")
            ]
            console.print(f"  Triggered rules: {', '.join(names)}")
        console.print()

    from cafe.ui.inquirer_prompts import prompt_list, prompt_multiline

    chat_role = _resolve_step_chat_role(playbook_data, from_step)
    chat_agent = _resolve_role_agent_name(playbook_data, chat_role)
    choices = _alignment_checkpoint_menu_choices(
        chat_agent,
        request_payload.get("allowed_decisions") if request_payload else None,
    )
    decision = prompt_list(
        "How should this alignment checkpoint continue?", choices, default="chat_alignment"
    )
    if decision == "chat_alignment":
        decision_file = _alignment_chat_decision_file(
            issue_dir=issue_dir,
            from_step=from_step,
            request_file=request_file,
        )
        if decision_file.exists():
            decision_file.unlink()
        from cafe.ui.cli import launch_chat_session as _lcs

        _lcs(
            chat_role,
            issue_name,
            chat_mode="alignment",
            initial_prompt=_build_alignment_chat_initial_prompt(
                request_payload=request_payload,
                request_file=request_file,
                decision_file=decision_file,
                from_step=from_step,
            ),
            extra_env={
                "CAFE_ALIGNMENT_FROM_STEP": from_step,
                "CAFE_ALIGNMENT_REQUEST_FILE": str(request_file) if request_file else "",
                "CAFE_ALIGNMENT_DECISION_FILE": str(decision_file),
            },
        )
        decision_payload = _load_alignment_chat_decision_payload(decision_file)
        if decision_payload is None:
            console.print(
                "[yellow]Chat ended without a valid alignment decision payload; "
                "workflow remains paused.[/yellow]"
            )
            return None
        return _apply_alignment_decision(
            issue_dir=issue_dir,
            playbook_data=playbook_data,
            blackboard=blackboard,
            store=store,
            from_step=from_step,
            request_payload=request_payload,
            request_file=request_file,
            decision=str(decision_payload["decision"]),
            correction=str(
                decision_payload.get("correction") or decision_payload.get("reason") or ""
            ).strip(),
            requires_user_confirmation=True,
            user_confirmation=_alignment_chat_user_confirmation(decision_payload),
        )

    correction = ""
    if decision in {
        "narrow_scope",
        "revise_spec",
        "revise_plan",
        "reject_or_defer",
        "manual_pause",
    }:
        correction = prompt_multiline("Add context for this alignment decision", default="").strip()

    return _apply_alignment_decision(
        issue_dir=issue_dir,
        playbook_data=playbook_data,
        blackboard=blackboard,
        store=store,
        from_step=from_step,
        request_payload=request_payload,
        request_file=request_file,
        decision=str(decision),
        correction=correction,
    )


def _alignment_checkpoint_menu_choices(
    chat_agent: str, allowed_decisions: Any = None
) -> list[dict[str, str]]:
    allowed: set[str] = set()
    if isinstance(allowed_decisions, list):
        allowed = {
            normalized
            for normalized in (_normalize_alignment_decision(item) for item in allowed_decisions)
            if normalized
        }

    choices = [{"name": f"Chat with {chat_agent} about alignment", "value": "chat_alignment"}]
    for decision, label in _ALIGNMENT_DIRECT_MENU_DECISIONS:
        if allowed and decision not in allowed:
            continue
        choices.append({"name": label, "value": decision})
    return choices


def _normalize_alignment_decision(raw: Any) -> Optional[str]:
    normalized = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "approve": "approve",
        "continue": "approve",
        "approve_and_continue": "approve",
        "narrow": "narrow_scope",
        "narrow_scope": "narrow_scope",
        "revise_spec": "revise_spec",
        "spec": "revise_spec",
        "revise_plan": "revise_plan",
        "plan": "revise_plan",
        "update_docs": "update_strategic_documents_first",
        "update_documents": "update_strategic_documents_first",
        "update_strategic_documents_first": "update_strategic_documents_first",
        "docs_updated": "strategic_documents_updated",
        "strategic_documents_updated": "strategic_documents_updated",
        "pause": "manual_pause",
        "manual_pause": "manual_pause",
        "reject": "reject_or_defer",
        "defer": "reject_or_defer",
        "reject_or_defer": "reject_or_defer",
    }
    return aliases.get(normalized)


def _alignment_chat_decision_file(
    *,
    issue_dir: Path,
    from_step: str,
    request_file: Optional[Path],
) -> Path:
    if request_file is not None:
        return request_file.with_name("alignment_decision.json")
    return (
        _latest_or_next_iteration_dir(issue_dir=issue_dir, step_name=from_step)
        / "alignment_decision.json"
    )


def _build_alignment_chat_initial_prompt(
    *,
    request_payload: Dict[str, Any],
    request_file: Optional[Path],
    decision_file: Path,
    from_step: str,
) -> str:
    rules = [
        str(item.get("rule_id"))
        for item in request_payload.get("triggered_rules") or []
        if isinstance(item, dict) and item.get("rule_id")
    ]
    affected_documents = [
        f"{item.get('category')}:{item.get('status')}"
        for item in request_payload.get("affected_documents") or []
        if isinstance(item, dict) and item.get("category")
    ]
    allowed_decisions = [
        str(item) for item in request_payload.get("allowed_decisions") or [] if str(item).strip()
    ]
    existing_docs, missing_docs = _alignment_document_status_groups(
        request_payload.get("affected_documents") or []
    )
    request_ref = str(request_file) if request_file is not None else "the latest request file"

    summary_parts = [
        f"level={request_payload.get('level', 'unknown')}",
        f"score={request_payload.get('score', 'unknown')}",
        f"risk={request_payload.get('risk_level', 'unknown')}",
    ]
    if rules:
        summary_parts.append("rules=" + ", ".join(rules[:8]))
    if affected_documents:
        summary_parts.append("docs=" + ", ".join(affected_documents[:8]))

    return "\n".join(
        [
            "You are opening a CAFE alignment checkpoint chat.",
            "",
            f"Step: {from_step}",
            f"Alignment request file: {request_ref}",
            f"Decision output file: {decision_file}",
            "Checkpoint summary: " + "; ".join(summary_parts),
            "Allowed decisions: " + ", ".join(allowed_decisions or ["none listed"]),
            "Existing strategic docs: " + ", ".join(existing_docs or ["none listed"]),
            "Missing/unconfigured strategic categories: "
            + ", ".join(missing_docs or ["none listed"]),
            "Chat-mode decision mapping:",
            "- Option 2 starts strategic document alignment; it does not by itself approve document content.",
            "- Before writing strategic_documents_updated from chat, the user must explicitly confirm the final document content after seeing the draft or summary.",
            "- If strategic docs are drafted but not confirmed, keep them as draft/missing and write update_strategic_documents_first or manual_pause.",
            "- User chooses to pause without document edits: final JSON decision is update_strategic_documents_first.",
            "",
            "Read the alignment request file first. Then start the conversation yourself:",
            "1. Briefly explain why CAFE paused and what decision is needed.",
            "2. Distinguish existing strategic docs from missing/unconfigured categories; do not say all strategic docs are missing when existing docs are listed.",
            "3. If the user chooses to update strategic documents first, ask at least one concrete strategic alignment question before treating the document as final.",
            "4. You may draft or update strategic document files in this chat, but do not mark a missing configured document as status=exists until the user has confirmed the final content.",
            "5. For unconfirmed drafts, keep .cafe/strategic_context.yaml status as draft or missing and do not write strategic_documents_updated.",
            "6. After the user explicitly confirms the final strategic document content, update .cafe/strategic_context.yaml so confirmed configured documents have status=exists.",
            "7. Only then may you write strategic_documents_updated. The JSON must include user_confirmed=true and user_confirmation with the user's confirmation summary.",
            "8. Write update_strategic_documents_first only when the user wants the workflow to remain paused for document work, or when documents cannot be safely finalized in this chat.",
            "9. Ask the user one or two concrete questions to choose an allowed decision.",
            "10. Once the user chooses, requested document edits are done, and required confirmation is present, write JSON to the decision output file with decision, reason, optional correction, and confirmation fields when needed.",
            "",
            "Do not edit the blackboard or next_step.txt. Do not wait for the user to guess the opening prompt.",
            "Use the user's language when possible.",
        ]
    )


def _alignment_document_status_groups(documents: Any) -> tuple[list[str], list[str]]:
    existing_docs: list[str] = []
    missing_docs: list[str] = []

    if not isinstance(documents, list):
        return existing_docs, missing_docs

    for item in documents:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if not category:
            continue
        status = str(item.get("status") or "missing").strip() or "missing"
        path = item.get("path")
        path_label = str(path).strip() if path else ""
        label = f"{category}:{status}"
        if path_label:
            label = f"{label} ({path_label})"
        else:
            label = f"{label} (unconfigured)"

        if status == "exists" and item.get("exists") is not False:
            existing_docs.append(label)
        else:
            missing_docs.append(label)

    return existing_docs, missing_docs


def _load_alignment_chat_decision_payload(decision_file: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(decision_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    decision = _normalize_alignment_decision(payload.get("decision"))
    if not decision:
        return None
    parsed = dict(payload)
    parsed["decision"] = decision
    return parsed


def _alignment_chat_user_confirmation(payload: Dict[str, Any]) -> str:
    confirmed = payload.get("user_confirmed")
    if isinstance(confirmed, str):
        confirmed_value = confirmed.strip().lower() in {"1", "true", "yes", "y", "confirmed"}
    else:
        confirmed_value = bool(confirmed)
    if not confirmed_value:
        return ""

    for key in ("user_confirmation", "confirmation_summary", "confirmation"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _load_latest_alignment_request(
    issue_dir: Path, from_step: str
) -> tuple[Dict[str, Any], Optional[Path]]:
    step_dir = issue_dir / from_step
    candidates = (
        sorted(step_dir.glob("iteration_*/alignment_request.json")) if step_dir.exists() else []
    )
    for request_file in reversed(candidates):
        try:
            data = json.loads(request_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            return data, request_file
    return {}, None


def _apply_alignment_decision(
    *,
    issue_dir: Path,
    playbook_data: Dict[str, Any],
    blackboard,
    store: BlackboardStore,
    from_step: str,
    request_payload: Dict[str, Any],
    request_file: Optional[Path],
    decision: str,
    correction: str = "",
    requires_user_confirmation: bool = False,
    user_confirmation: str = "",
) -> Optional[str]:
    decision = _normalize_alignment_decision(decision) or decision
    fingerprint = str(request_payload.get("fingerprint") or "")
    requirements = request_payload.get("strategic_document_update_requirements") or []
    allowed_decisions = request_payload.get("allowed_decisions") or []
    if isinstance(allowed_decisions, list):
        allowed = {
            normalized
            for normalized in (_normalize_alignment_decision(item) for item in allowed_decisions)
            if normalized
        }
        if allowed and decision not in allowed:
            console.print(
                f"[yellow]Alignment decision '{decision}' is not allowed for this "
                "checkpoint.[/yellow]"
            )
            store.record_event(
                blackboard,
                "alignment_decision_blocked",
                {
                    "step": from_step,
                    "decision": decision,
                    "fingerprint": fingerprint,
                    "reason": "decision_not_allowed",
                    "allowed_decisions": sorted(allowed),
                },
            )
            return None

    if decision == "approve" and requirements:
        console.print(
            "[yellow]Strategic document updates are required before approval can "
            "resume execution.[/yellow]"
        )
        store.record_event(
            blackboard,
            "alignment_decision_blocked",
            {
                "step": from_step,
                "decision": decision,
                "fingerprint": fingerprint,
                "reason": "required_document_update",
            },
        )
        return None

    if decision == "strategic_documents_updated":
        if requires_user_confirmation and not user_confirmation.strip():
            console.print(
                "[yellow]Strategic document updates from chat require explicit "
                "user confirmation after the final content is presented.[/yellow]"
            )
            store.record_event(
                blackboard,
                "alignment_decision_blocked",
                {
                    "step": from_step,
                    "decision": decision,
                    "fingerprint": fingerprint,
                    "reason": "missing_user_confirmation",
                },
            )
            return None
        if not _alignment_documents_changed(
            issue_dir,
            requirements,
            request_payload.get("affected_documents") or [],
        ):
            console.print(
                "[yellow]No affected strategic document change was detected; "
                "workflow remains paused.[/yellow]"
            )
            store.record_event(
                blackboard,
                "alignment_decision_blocked",
                {
                    "step": from_step,
                    "decision": decision,
                    "fingerprint": fingerprint,
                    "reason": "document_hash_unchanged",
                },
            )
            return None
        decision = "approve"

    if decision == "update_strategic_documents_first":
        store.set_current_step(blackboard, "user")
        store.set_handoff_summary(
            blackboard, f"Waiting for strategic document updates before {from_step}"
        )
        store.update_handoff_contract(
            blackboard,
            from_step=from_step,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
            status_code="alignment_checkpoint",
            source="user.alignment_update_docs_first",
        )
        store.record_event(
            blackboard,
            "alignment_decision",
            {
                "step": from_step,
                "decision": decision,
                "fingerprint": fingerprint,
                "unblocks_execution": False,
                "requirements": requirements,
            },
        )
        console.print("[yellow]Workflow remains paused for strategic document updates.[/yellow]")
        return None

    if decision in {"manual_pause", "reject_or_defer"}:
        store.set_current_step(blackboard, "user")
        store.set_handoff_summary(blackboard, correction or f"Alignment decision: {decision}")
        store.update_handoff_contract(
            blackboard,
            from_step=from_step,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
            status_code="alignment_checkpoint",
            source=f"user.alignment_{decision}",
        )
        store.record_event(
            blackboard,
            "alignment_decision",
            {
                "step": from_step,
                "decision": decision,
                "fingerprint": fingerprint,
                "correction": correction,
                "unblocks_execution": False,
            },
        )
        console.print("[yellow]Workflow remains paused.[/yellow]")
        return None

    target_step = from_step
    if decision == "revise_spec":
        target_step = "spec" if "spec" in playbook_data.get("steps", {}) else from_step
    elif decision == "revise_plan":
        target_step = "plan" if "plan" in playbook_data.get("steps", {}) else from_step

    if decision == "narrow_scope":
        correction = correction or "Narrow scope according to the alignment checkpoint decision."
        _write_alignment_user_input(
            issue_dir=issue_dir, step_name=from_step, request_file=request_file, text=correction
        )
    elif decision in {"revise_spec", "revise_plan"}:
        _write_next_iteration_user_input(
            issue_dir=issue_dir,
            step_name=target_step,
            text=correction or f"Revise due to alignment decision from {from_step}.",
        )

    store.set_current_step(blackboard, target_step)
    store.set_handoff_summary(
        blackboard, correction or f"Alignment decision '{decision}' for {from_step}"
    )
    store.update_handoff_contract(
        blackboard,
        from_step=from_step,
        to_owner=HandoffOwner.AGENT,
        to_step=target_step,
        intent=HandoffIntent.AWAIT_AGENT,
        status_code="",
        source=f"user.alignment_{decision}",
    )
    store.record_event(
        blackboard,
        "alignment_decision",
        {
            "step": from_step,
            "decision": decision,
            "target_step": target_step,
            "fingerprint": fingerprint,
            "correction": correction,
            "unblocks_execution": True,
        },
    )
    console.print(f"[green]Alignment decision recorded[/green] {decision} -> {target_step}")
    return target_step


def _alignment_documents_changed(
    issue_dir: Path,
    requirements: Any,
    affected_documents: Any = None,
) -> bool:
    from cafe.core.strategic_context import load_strategic_context

    project_root, issue_name = _resolve_issue_context_root(issue_dir)
    context = load_strategic_context(project_root, issue_name=issue_name)
    checked_any = False

    for item in (requirements if isinstance(requirements, list) else []):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        if not category:
            continue
        checked_any = True
        previous_hash = item.get("current_sha256")
        current = context.document(category)
        if current.sha256 and current.sha256 != previous_hash:
            return True

    for item in (affected_documents if isinstance(affected_documents, list) else []):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        if not category:
            continue
        previous_status = str(item.get("status") or "").strip()
        previous_exists = item.get("exists")
        previous_hash = item.get("sha256") or item.get("current_sha256")
        current = context.document(category)

        if previous_status in {"missing", "draft"} or previous_exists is False:
            checked_any = True
            if current.status == "exists" and current.exists and current.sha256:
                return True
            if previous_status == "draft" and current.sha256 and current.sha256 != previous_hash:
                return True
        elif previous_hash:
            checked_any = True
            if current.sha256 and current.sha256 != previous_hash:
                return True

    if not checked_any:
        return True
    return False


def _resolve_issue_context_root(issue_dir: Path) -> tuple[Path, str]:
    resolved = issue_dir.resolve()
    for parent in resolved.parents:
        if parent.name == "issues" and parent.parent.name == ".cafe":
            return parent.parent.parent, resolved.relative_to(parent).as_posix()
    for parent in resolved.parents:
        if parent.name == ".cafe":
            return parent.parent, resolved.name
    return issue_dir.parent.parent.parent, issue_dir.name


def _write_alignment_user_input(
    *, issue_dir: Path, step_name: str, request_file: Optional[Path], text: str
) -> None:
    if request_file is not None:
        target_dir = request_file.parent
    else:
        target_dir = _latest_or_next_iteration_dir(issue_dir=issue_dir, step_name=step_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "user_input.md").write_text(text, encoding="utf-8")


def _write_next_iteration_user_input(*, issue_dir: Path, step_name: str, text: str) -> None:
    step_dir = issue_dir / step_name
    iteration_dirs = sorted(step_dir.glob("iteration_*")) if step_dir.exists() else []
    next_iter = len(iteration_dirs) + 1
    target_dir = step_dir / f"iteration_{next_iter:03d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "user_input.md").write_text(text, encoding="utf-8")


def _latest_or_next_iteration_dir(*, issue_dir: Path, step_name: str) -> Path:
    step_dir = issue_dir / step_name
    iteration_dirs = sorted(step_dir.glob("iteration_*")) if step_dir.exists() else []
    if iteration_dirs:
        return iteration_dirs[-1]
    return step_dir / "iteration_001"


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
        current_questions_file = (
            current_iter_dir / "questions.xml" if current_iter_dir is not None else None
        )
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
            prompt_role = _resolve_step_chat_role(playbook_data, from_step)
            agent_name = _resolve_role_agent_name(playbook_data, prompt_role)
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
        {
            "step": from_step,
            "source": "questions_xml" if questions_file and questions_file.exists() else "prompt",
        },
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
    summary: str,
) -> Optional[str]:
    """Handle confirm_output handoff: display output and ask for confirmation."""
    console.print(f"[yellow]Review requested[/yellow] step={from_step}")
    if summary:
        console.print(f"[dim]{summary}[/dim]")

    # Display the output file
    from_step_dir = issue_dir / from_step
    output_files = (
        sorted(from_step_dir.glob("iteration_*/output.md")) if from_step_dir.exists() else []
    )
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

    role_map = _resolve_step_chat_role(playbook_data, from_step)

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
            iteration_dirs = (
                sorted(from_step_dir.glob("iteration_*")) if from_step_dir.exists() else []
            )
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
            f"{_resolve_step_handoff_label(playbook_data, step_name)} ({step_name})"
            for step_name in step_names
        ]
        default_step = str(
            playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))
        )
        default_label = (
            f"{_resolve_step_handoff_label(playbook_data, default_step)} ({default_step})"
        )
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
        role_choices = list(playbook_data.get("roles", {}).keys()) or [
            "pm",
            "developer",
            "reviewer",
        ]
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
                console.print(
                    "[dim]All configured agents (primary + backups) have been exhausted.[/dim]"
                )
                console.print()
                console.print(f"[dim]{error_msg}[/dim]")
            else:
                console.print(f"[dim]{error_msg}[/dim]")
                console.print(
                    "[dim]The workflow has been stopped to prevent wasting resources.[/dim]"
                )
            console.print()
            console.print("[bold]Next steps (choose one):[/bold]")
            console.print("  • Wait for quota reset or switch to a different account, OR")
            console.print(
                "  • Use [cyan]cafe config edit[/cyan] to add backup agents or switch CLI tool"
            )
            console.print()
            console.print("Then run [cyan]cafe make[/cyan] again to resume from where it stopped")
            console.print()
        elif e.error_type == "cli_not_found":
            console.print(
                "[yellow]⚠️  Required CLI tool not found. Please install it and try again.[/yellow]"
            )
            console.print()
            console.print(
                "[dim]ℹ️  The workflow has been stopped to prevent wasting resources.[/dim]"
            )
            console.print()
        else:
            console.print(f"[yellow]⚠️  {e}[/yellow]")
            console.print()
            console.print(
                "[dim]ℹ️  The workflow has been stopped to prevent wasting resources.[/dim]"
            )
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
    # LEGACY: Single-step alias bootstrap accepts legacy plain-text `next_step.txt`
    # for CLI convenience. Preserve for v0.2 compatibility; target migration in v0.3.
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
    # LEGACY: After running single-step alias, re-load latest blackboard allowing
    # legacy `next_step.txt` formats for compatibility with older sessions.
    latest_blackboard = store.load_or_create(
        str(playbook_data.get("entry_point") or next(iter(playbook_data["steps"].keys()))),
        playbook_id=str(playbook_data["playbook"]["id"]),
        allow_legacy_text=True,
    )
    # LEGACY: Handoff contract loader accepts legacy text; new agent-written
    # handoffs should emit structured batons instead.
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
            if (
                event_type == "workflow_blocked"
                and data.get("reason") == "missing_capability_receipt"
            ):
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
    return _alias_is_user_pause(alias_result) and _alias_handoff_intent(alias_result) in set(
        intents
    )


def _alias_is_confirmed_transition(alias_result: Dict[str, Any], step_name: str) -> bool:
    return _alias_targets(alias_result, step_name) or _alias_status(alias_result) == "confirmed"


def _alias_needs_clarification(alias_result: Dict[str, Any]) -> bool:
    return (
        _alias_pause_intent(alias_result, "need_clarification")
        or _alias_status(alias_result) == "need_clarification"
    )


def _alias_needs_permission(alias_result: Dict[str, Any]) -> bool:
    return (
        _alias_pause_intent(alias_result, "need_permission")
        or _alias_status(alias_result) == "need_permission"
    )


def _alias_confirm_output_pause(alias_result: Dict[str, Any]) -> bool:
    return (
        _alias_pause_intent(alias_result, "confirm_output")
        or _alias_status(alias_result) == "ready_for_review"
    )


def _reject_unsupported_phase_options(
    phase_name: str, unsupported_options: Dict[str, bool]
) -> None:
    """Exit when a legacy-only CLI option is requested."""
    unsupported = [name for name, enabled in unsupported_options.items() if enabled]
    if not unsupported:
        return
    rendered = ", ".join(f"--{name}" for name in unsupported)
    console.print(
        f"[red]Error: {phase_name} no longer supports legacy phase options: {rendered}[/red]"
    )
    console.print("[dim]Use the workflow runtime directly or rerun without those flags.[/dim]")
    raise typer.Exit(1)


def _print_legacy_phase_command_notice(
    *,
    phase_name: str,
    preferred_command: str,
    workflow_step: str | None = None,
) -> None:
    """Show alias guidance for hidden legacy workflow step commands."""
    step = workflow_step or phase_name.split()[0]
    console.print(
        f"[yellow]Legacy workflow alias:[/yellow] [bold]cafe {phase_name}[/bold] runs the same "
        f"runtime path as [bold]cafe workflow --start-step {step} --execute[/bold]."
    )
    console.print(f"[dim]Preferred entrypoint:[/dim] [bold]{preferred_command}[/bold]")
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


def _display_iteration_questions(
    *, issue_name: str, step_name: str, alias_result: Dict[str, Any]
) -> None:
    """Render clarification questions from the latest iteration when available."""
    iteration = alias_result.get("iterations")
    if not isinstance(iteration, int) or iteration <= 0:
        return

    questions_file = (
        Path(".cafe")
        / "issues"
        / issue_name
        / step_name
        / f"iteration_{iteration:03d}"
        / "questions.xml"
    )
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
            _display_iteration_questions(
                issue_name=issue_name, step_name=step_name, alias_result=alias_result
            )
        else:
            console.print("[yellow]📝 Draft ready for review[/yellow]")

        from cafe.ui.cli import prompt_confirm as _pc2

        should_continue = auto or _pc2("Continue to next iteration?", default=True)
        if not should_continue:
            console.print("[dim]Stopped by user.[/dim]")
            return alias_result

        if (
            handoff_intent == "need_clarification" or status_code == "need_clarification"
        ) and clarification_prompt:
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
        console.print(
            "[dim]Agent finished without updating the workflow baton for this step.[/dim]"
        )
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
        console.print(
            "[dim]After the chat or handoff is written back, run cafe make again to resume.[/dim]"
        )
        return

    if status_code == "NO_STATUS_TRANSITION":
        console.print(
            "[dim]Agent returned a status code, but the playbook has no transition for it. "
            "Open chat with the step role or fix the playbook mapping, then run cafe make again.[/dim]"
        )
        return

    console.print("[dim]Resolve the requested input, then run cafe make again to resume.[/dim]")
