"""Reusable chat launcher for inline agent chat sessions."""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.playbook import resolve_playbook_skills
from cafe.core.types import AgentCLI, AgentConfig, CliEntry
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.config import ConfigManager
from cafe.utils.git_utils import get_git_toplevel, get_repo_root
from cafe.utils.phase_config import load_phase_step_model

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


def _load_chat_role_config(
    config_manager: ConfigManager, role: str, issue_dir: Optional[Path] = None
) -> Optional[dict]:
    """Load the active step's sole execution chain from phases.yaml."""
    if issue_dir is None:
        return None
    execution_step = _load_chat_execution_step(issue_dir)
    resolution = load_phase_step_model(
        step_name=execution_step,
        local_path=get_git_toplevel() / ".cafe" / "phases.yaml",
        repo_path=get_repo_root() / ".cafe" / "phases.yaml",
    )
    if resolution.role and resolution.role != role:
        return None
    return {
        "name": resolution.name or execution_step,
        "role": resolution.role or role,
        "clis": [{"cli": cli, "model": model} for cli, model in resolution.clis],
    }


def _load_chat_execution_step(issue_dir: Path) -> str:
    """Resolve the agent step whose execution chain should launch paused chat."""
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    current_step = blackboard.current_step
    if current_step != "user":
        return current_step

    contract = blackboard.handoff_contract
    if (
        contract is None
        or contract.to_owner != HandoffOwner.USER
        or contract.to_step != "user"
        or not contract.from_step
        or contract.from_step == "user"
    ):
        raise ValueError(
            "invalid paused chat handoff: current_step='user': "
            "field='handoff_contract.from_step': expected originating agent step"
        )
    return contract.from_step


def _phase_entries(role_config: dict) -> list[CliEntry]:
    raw_entries = role_config.get("clis")
    if not isinstance(raw_entries, list):
        return []
    entries: list[CliEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        try:
            entries.append(CliEntry(cli=AgentCLI(item.get("cli")), model=item.get("model")))
        except (TypeError, ValueError):
            continue
    return entries


def _configured_cli_values(role_config: dict) -> set[str]:
    chain = _phase_entries(role_config)
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
    for entry in _phase_entries(role_config):
        if entry.cli.value == cli:
            return entry.resolve_model(phase_name)

    if role_config.get("cli") == cli:
        model = role_config.get("model")
        return model if isinstance(model, str) else None
    return None


def _resolve_primary_chat_cli(role_config: dict) -> tuple[Optional[str], Optional[str]]:
    chain = _phase_entries(role_config)
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

    # An explicit phase-chain primary change invalidates the sticky record:
    # ignore it unless the recorded configured_primary still matches (or the
    # sticky CLI already is the current primary). Mirrors AgentManager.
    chain = _phase_entries(role_config)
    current_primary = chain[0].cli.value if chain else None
    recorded_primary = record.get("configured_primary")
    if current_primary is not None and cli != current_primary:
        if not isinstance(recorded_primary, str) or recorded_primary != current_primary:
            return None

    model = record.get("model")
    if not isinstance(model, str):
        step_name = record.get("step_name")
        model = _configured_model_for_cli(
            role_config,
            cli,
            step_name if isinstance(step_name, str) else None,
        )

    recorded_chain = record.get("chain")
    if isinstance(recorded_chain, list):
        def _extract_cli_value(item):
            if isinstance(item, dict):
                return item.get("cli")
            return item

        recorded_chain_values = [_extract_cli_value(i) for i in recorded_chain]
        current_chain = [entry.cli.value for entry in _phase_entries(role_config)]
        if recorded_chain_values != current_chain:
            return None

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
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
    playbook_id = getattr(blackboard, "playbook_id", "standard") or "standard"
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
    blackboard = store.load_or_create(current_step)
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
    playbook: dict,
    role: str,
    step_name: str,
) -> None:
    """Install chat skills resolved from the active playbook."""
    if not isinstance(agent_cli, AgentCLI):
        return

    loader = SkillLoader()
    loader.discover()
    bridge = NativeSkillBridge(loader)

    bridge.synchronize_skills(
        resolve_playbook_skills(
            playbook,
            channel="chat",
            role=role,
            step_name=step_name,
        ),
        agent_cli,
    )


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


def _handle_chat_launch_failure(
    agent_cli: AgentCLI, result: subprocess.CompletedProcess[object]
) -> int:
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


def launch_chat_session(
    role: str,
    issue_name: str,
    *,
    chat_mode: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    initial_prompt: Optional[str] = None,
) -> int:
    """Launch an inline chat session with the agent for the given role.

    Resolves agent config from ConfigManager, loads the existing session,
    builds the CLI command, and invokes it via subprocess.run(). Returns
    when the user exits the chat. Errors are printed as warnings so the
    caller's prompt loop can continue.

    Args:
        role: Agent role ("pm", "developer", or "reviewer")
        issue_name: Current issue name (used to load issue-specific session)
        initial_prompt: Optional first message to send when the interactive CLI supports it.
    """
    issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name

    playbook_id = "standard"
    try:
        _current_step, _valid_steps, playbook_id = _load_chat_workflow_context(issue_dir)
        chat_playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
    except Exception as exc:
        print(
            f"\n⚠️  Chat cannot start because playbook validation failed for "
            f"'{playbook_id}': {exc}. Fix the declaration and retry.\n"
        )
        return 1

    # Load configuration
    config_manager = ConfigManager()
    try:
        agent_config = _load_chat_role_config(config_manager, role, issue_dir=issue_dir)
    except Exception as exc:
        print(
            f"\n⚠️  Chat cannot start because phase configuration failed for "
            f"role '{role}': {exc}. Fix .cafe/phases.yaml and retry.\n"
        )
        return 1

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
            clis=_phase_entries(agent_config),
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
        playbook=chat_playbook,
        role=role,
        step_name=_current_step,
    )
    session_id: Optional[str] = executor.config.session_id
    codex_history_start_ts = int(time.time())
    cli_command = cli_strategy.build_interactive_command(initial_prompt=initial_prompt)

    env = cli_strategy.build_environment()
    env["CAFE_ISSUE_NAME"] = issue_name
    env["CAFE_ISSUE_DIR"] = str(issue_dir)
    env["CAFE_CHAT_CURRENT_STEP"] = _current_step
    env["CAFE_CHAT_PLAYBOOK_ID"] = _playbook_id
    if initial_prompt:
        env["CAFE_CHAT_INITIAL_PROMPT"] = initial_prompt
    if chat_mode:
        env["CAFE_CHAT_MODE"] = chat_mode
    if extra_env:
        for key, value in extra_env.items():
            env[str(key)] = str(value)
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
    blackboard = store.load_or_create(_current_step)
    contract = store.load_handoff_contract(
        blackboard,
        allowed_steps=_valid_steps,
    )
    if contract.source == "chat.bootstrap":
        print(
            "\n⚠️  Chat ended without writing a next-step baton. "
            "The agent did not complete workflow handoff.\n"
        )

    return result.returncode
