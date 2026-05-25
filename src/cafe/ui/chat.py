"""Reusable chat launcher for inline agent chat sessions."""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.types import AgentCLI, AgentConfig
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.config import ConfigManager
from cafe.utils.crew import CrewManager, normalize_role_config


CHAT_SKILL_NAMES = [
    "common-chat-handoff",
    "chat-develop-change",
    "chat-spec-revision",
    "chat-plan-revision",
]

CURSOR_NATIVE_MODULE_HINT = "@anysphere/file-service-"


def _extract_latest_codex_session_id(
    since_ts: int,
    history_file: Optional[Path] = None,
) -> Optional[str]:
    """Extract the latest Codex session id from local history."""
    if history_file is None:
        history_file = Path.home() / ".codex" / "history.jsonl"

    if not history_file.exists():
        return None

    latest_session_id: Optional[str] = None
    latest_ts = since_ts

    for line in history_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        session_id = data.get("session_id")
        ts = data.get("ts")
        if isinstance(session_id, str) and isinstance(ts, int) and ts >= latest_ts:
            latest_ts = ts
            latest_session_id = session_id

    return latest_session_id

def get_chat_next_step_path(issue_dir: Path) -> Path:
    return issue_dir / "next_step.txt"


def _load_chat_role_config(config_manager: ConfigManager, role: str, issue_dir: Optional[Path] = None) -> Optional[dict]:
    """Load role config from crew.yaml first, then config.yaml."""
    try:
        cafe_dir = Path(getattr(config_manager, "config_dir"))
        crew_data = CrewManager(cafe_dir=cafe_dir).load()
        role_config = crew_data.get(role)
        if isinstance(role_config, dict):
            return role_config
    except Exception:
        pass

    role_config = config_manager.get(f"agents.{role}", None)
    if isinstance(role_config, dict):
        return role_config

    if issue_dir is not None:
        playbook_role_config = _load_playbook_role_config(issue_dir, role)
        if playbook_role_config is not None:
            return playbook_role_config
    return None


def _load_playbook_role_config(issue_dir: Path, role: str) -> Optional[dict]:
    try:
        _, _, playbook_id = _load_chat_workflow_context(issue_dir)
        playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
    except Exception:
        return None

    role_def = playbook.get("roles", {}).get(role, {})
    if not isinstance(role_def, dict):
        return None

    agent_name = role_def.get("default_agent")
    cli = role_def.get("default_cli")
    if not isinstance(agent_name, str) or not agent_name.strip():
        return None
    if not isinstance(cli, str) or not cli.strip():
        return None
    return {"name": agent_name.strip(), "cli": cli.strip()}


def _configured_cli_values(role_config: dict) -> set[str]:
    chain = normalize_role_config(role_config)
    values = {entry.cli.value for entry in chain}
    cli = role_config.get("cli")
    if isinstance(cli, str):
        values.add(cli)
    return values


def _configured_model_for_cli(
    role_config: dict,
    cli: str,
    phase_name: Optional[str],
) -> Optional[str]:
    for entry in normalize_role_config(role_config):
        if entry.cli.value == cli:
            return entry.resolve_model(phase_name)

    if role_config.get("cli") == cli:
        model = role_config.get("model")
        return model if isinstance(model, str) else None
    return None


def _resolve_primary_chat_cli(role_config: dict) -> tuple[Optional[str], Optional[str]]:
    chain = normalize_role_config(role_config)
    if chain:
        primary = chain[0]
        return primary.cli.value, primary.resolve_model(None)

    cli = role_config.get("cli")
    model = role_config.get("model")
    return (cli if isinstance(cli, str) else None, model if isinstance(model, str) else None)


def _load_active_chat_cli(
    issue_dir: Path,
    *,
    agent_name: str,
    role_config: dict,
) -> Optional[tuple[str, Optional[str]]]:
    active_file = issue_dir / "active_clis.json"
    if not active_file.exists():
        return None

    try:
        data = json.loads(active_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    record = data.get(agent_name)
    if not isinstance(record, dict):
        return None

    cli = record.get("cli")
    if not isinstance(cli, str):
        return None
    configured = _configured_cli_values(role_config)
    if configured and cli not in configured:
        return None

    model = record.get("model")
    if not isinstance(model, str):
        step_name = record.get("step_name")
        model = _configured_model_for_cli(
            role_config,
            cli,
            step_name if isinstance(step_name, str) else None,
        )
    return cli, model if isinstance(model, str) else None


def _infer_cli_from_streaming_jsonl(context_file: Path) -> Optional[str]:
    """Infer CLI for legacy iterations written before actual fallback CLI was recorded."""
    streaming_file = context_file.with_name("streaming.jsonl")
    if not streaming_file.exists():
        return None

    try:
        lines = streaming_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("type") in {"thread.started", "turn.started", "item.completed"}:
            return AgentCLI.CODEX.value
    return None


def _load_latest_role_iteration_cli(
    issue_dir: Path,
    *,
    role: str,
    role_config: dict,
) -> Optional[tuple[str, Optional[str]]]:
    """Find the latest successful iteration for this role and reuse its CLI."""
    try:
        _, _, playbook_id = _load_chat_workflow_context(issue_dir)
        playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
    except Exception:
        return None

    steps = playbook.get("steps", {})
    if not isinstance(steps, dict):
        return None

    role_steps = {
        step_name
        for step_name, step_def in steps.items()
        if isinstance(step_def, dict) and step_def.get("role") == role
    }
    if not role_steps:
        return None

    configured = _configured_cli_values(role_config)
    candidates: list[tuple[str, str, Optional[str]]] = []
    for step_name in role_steps:
        for context_file in (issue_dir / step_name).glob("iteration_*/iteration.json"):
            try:
                context = json.loads(context_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(context, dict) or context.get("error"):
                continue
            cli = _infer_cli_from_streaming_jsonl(context_file) or context.get("cli")
            if not isinstance(cli, str):
                continue
            if configured and cli not in configured:
                continue
            updated_at = context.get("end_time") or context.get("timestamp") or ""
            model = context.get("model")
            if not isinstance(model, str):
                model = _configured_model_for_cli(role_config, cli, step_name)
            candidates.append((str(updated_at), cli, model if isinstance(model, str) else None))

    if not candidates:
        return None

    _, cli, model = sorted(candidates, key=lambda item: item[0])[-1]
    return cli, model


def _resolve_chat_cli(
    issue_dir: Path,
    *,
    role: str,
    agent_name: str,
    role_config: dict,
) -> tuple[Optional[str], Optional[str]]:
    active = _load_active_chat_cli(issue_dir, agent_name=agent_name, role_config=role_config)
    if active:
        return active

    latest = _load_latest_role_iteration_cli(issue_dir, role=role, role_config=role_config)
    if latest:
        return latest

    return _resolve_primary_chat_cli(role_config)


def _load_chat_workflow_context(issue_dir: Path) -> tuple[str, list[str], str]:
    # LEGACY: Accept plain-text `next_step.txt` (v0.1 format) during chat
    # bootstrap so sessions started by an older build remain readable.
    # New chat handoffs always write structured JSON batons.
    blackboard = BlackboardStore(issue_dir).load_or_create("spec", allow_legacy_text=True)
    playbook_id = getattr(blackboard, "playbook_id", "default") or "default"
    current_step = blackboard.current_step

    try:
        playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
        steps = list(playbook["steps"].keys())
    except Exception:
        steps = ["spec", "plan", "develop", "review", "pr"]

    return current_step, steps, playbook_id


def _prepare_chat_handoff_state(issue_dir: Path) -> tuple[str, list[str], str]:
    current_step, valid_steps, playbook_id = _load_chat_workflow_context(issue_dir)
    issue_dir.mkdir(parents=True, exist_ok=True)
    store = BlackboardStore(issue_dir)
    # LEGACY: Allow legacy text when preparing chat handoff state; resumed
    # sessions may have been written by an older build.
    blackboard = store.load_or_create(current_step, allow_legacy_text=True)
    if current_step == "done":
        store.update_handoff_contract(
            blackboard,
            from_step=current_step,
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
            source="chat.bootstrap",
        )
    elif current_step == "user":
        contract = getattr(blackboard, "handoff_contract", None)
        if not (
            contract is not None
            and contract.to_owner == HandoffOwner.USER
            and contract.to_step == "user"
            and contract.from_step != "user"
        ):
            store.update_handoff_contract(
                blackboard,
                from_step=current_step,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=HandoffIntent.MANUAL_HANDOFF,
                source="chat.bootstrap",
            )
    else:
        store.update_handoff_contract(
            blackboard,
            from_step=current_step,
            to_owner=HandoffOwner.AGENT,
            to_step=current_step,
            intent=HandoffIntent.AWAIT_AGENT,
            source="chat.bootstrap",
        )
    return current_step, valid_steps, playbook_id


def _prepare_chat_environment(
    *,
    agent_cli: AgentCLI | object,
) -> None:
    """Install shared chat skills for the target CLI."""
    if not isinstance(agent_cli, AgentCLI):
        return

    loader = SkillLoader()
    loader.discover()
    bridge = NativeSkillBridge(loader)

    for skill_name in CHAT_SKILL_NAMES:
        bridge.install_skill(skill_name, agent_cli)


def _format_cli_specific_error(agent_cli: AgentCLI, stderr: str, stdout: str) -> Optional[str]:
    """Translate known CLI-specific failures into user-facing diagnostics."""
    combined = f"{stderr}\n{stdout}"
    if agent_cli == AgentCLI.CURSOR and CURSOR_NATIVE_MODULE_HINT in combined:
        return (
            "Cursor CLI is installed but broken: missing native module "
            "`@anysphere/file-service-darwin-x64`. Reinstall or repair Cursor CLI, "
            "or switch this role to another agent before opening chat."
        )
    return None


def _handle_chat_launch_failure(agent_cli: AgentCLI, result: subprocess.CompletedProcess[object]) -> int:
    """Print a concise launch failure and return the CLI's exit code."""
    stderr = (getattr(result, "stderr", None) or "").strip()
    stdout = (getattr(result, "stdout", None) or "").strip()
    specific_error = _format_cli_specific_error(agent_cli, stderr, stdout)
    if specific_error:
        print(f"\n⚠️  {specific_error}\n")
        return result.returncode

    detail = stderr or stdout
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        summary = lines[0] if lines else detail
        print(f"\n⚠️  Chat CLI exited with code {result.returncode}: {summary}\n")
    return result.returncode


def launch_chat_session(role: str, issue_name: str) -> int:
    """Launch an inline chat session with the agent for the given role.

    Resolves agent config from ConfigManager, loads the existing session,
    builds the CLI command, and invokes it via subprocess.run(). Returns
    when the user exits the chat. Errors are printed as warnings so the
    caller's prompt loop can continue.

    Args:
        role: Agent role ("pm", "developer", or "reviewer")
        issue_name: Current issue name (used to load issue-specific session)
    """
    issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name

    # Load configuration
    config_manager = ConfigManager()
    agent_config = _load_chat_role_config(config_manager, role, issue_dir=issue_dir)

    if agent_config is None:
        print(f"\n⚠️  No agent configured for role '{role}'. Skipping chat.\n")
        return 0

    agent_name = agent_config.get("name")
    agent_cli_str, agent_model = _resolve_chat_cli(
        issue_dir,
        role=role,
        agent_name=agent_name,
        role_config=agent_config,
    )

    if not agent_name or not agent_cli_str:
        print(f"\n⚠️  Invalid agent configuration for role '{role}'. Skipping chat.\n")
        return 0

    # Set up agent manager and load existing session
    agent_manager = AgentManager(issue_name=issue_name)
    try:
        agent_cli = AgentCLI(agent_cli_str)
    except ValueError:
        print(f"\n⚠️  Unknown CLI tool '{agent_cli_str}'. Skipping chat.\n")
        return 0

    agent_manager.register_agent(
        AgentConfig(
            name=agent_name,
            cli=agent_cli,
            model=agent_model,
        )
    )

    try:
        executor = agent_manager.get_agent(agent_name)
    except Exception as e:
        print(f"\n⚠️  Failed to get agent '{agent_name}': {e}. Skipping chat.\n")
        return 0
    cli_strategy = executor._get_cli_strategy()

    _current_step, _valid_steps, _playbook_id = _prepare_chat_handoff_state(issue_dir)

    _prepare_chat_environment(
        agent_cli=agent_cli,
    )
    session_id: Optional[str] = executor.config.session_id

    cli_command = [agent_cli_str]
    codex_history_start_ts = int(time.time())

    if agent_cli_str == "codex" and agent_model:
        cli_command.extend(["--model", agent_model])

    if session_id:
        if agent_cli_str in ("claude", "copilot", "gemini"):
            cli_command.extend(["--resume", session_id])
        elif agent_cli_str == "cursor-agent":
            cli_command.extend(["--resume", session_id])
        elif agent_cli_str == "codex":
            cli_command.extend(["resume", session_id])

    if agent_model:
        if agent_cli_str in ("claude", "copilot", "gemini"):
            cli_command.extend(["--model", agent_model])

    env = cli_strategy.build_environment()
    print(f"\nOpening chat with {role} ({agent_name})...")
    if session_id:
        print(f"Resuming session: {session_id}")
    print()

    # Execute interactive CLI (blocks until user exits)
    try:
        result = subprocess.run(cli_command, env=env)
    except FileNotFoundError:
        print(f"\n⚠️  CLI tool '{agent_cli_str}' not found. Please install it first.\n")
        return 1
    except Exception as e:
        print(f"\n⚠️  Failed to execute CLI: {e}\n")
        return 1

    if agent_cli == AgentCLI.CODEX:
        resolved_session_id = session_id or _extract_latest_codex_session_id(codex_history_start_ts)
        if resolved_session_id:
            executor.config.session_id = resolved_session_id
            agent_manager.session_manager.save_session(
                agent_name,
                agent_cli,
                resolved_session_id,
                issue_name,
            )

    if result.returncode != 0:
        return _handle_chat_launch_failure(agent_cli, result)

    store = BlackboardStore(issue_dir)
    # LEGACY: After chat CLI exit, reload blackboard allowing legacy
    # `next_step.txt` formats so older chat handoffs remain consumable.
    # New chat handoffs should always write structured JSON batons.
    blackboard = store.load_or_create(_current_step, allow_legacy_text=True)
    # LEGACY: Load handoff contract with legacy text fallback for the same
    # backward-compatibility reason.
    contract = store.load_handoff_contract(
        blackboard,
        allowed_steps=_valid_steps,
        allow_legacy_text=True,
    )
    if contract.source == "chat.bootstrap":
        print("\n⚠️  Chat ended without writing a next-step baton. The agent did not complete workflow handoff.\n")

    return result.returncode
