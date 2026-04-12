"""Reusable chat launcher for inline agent chat sessions."""

import io
import json
import subprocess
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, AgentConfig
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.config import ConfigManager


CHAT_SKILL_NAMES = [
    "common-chat-handoff",
    "chat-develop-change",
    "chat-spec-revision",
    "chat-plan-revision",
]


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


def _load_chat_workflow_context(issue_dir: Path) -> tuple[str, list[str], str]:
    blackboard = BlackboardStore(issue_dir).load_or_create("spec")
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
    BlackboardStore(issue_dir).load_or_create(current_step)
    next_step_path = get_chat_next_step_path(issue_dir)
    if next_step_path.exists():
        next_step_path.unlink()
    return current_step, valid_steps, playbook_id


def _build_chat_seed_prompt(
    *,
    role: str,
    issue_name: str,
    blackboard_path: Path,
    next_step_path: Path,
    current_step: str,
    playbook_id: str,
) -> str:
    """Build the chat bootstrap prompt for one interactive session."""
    return (
        f"`cafe chat` context for role `{role}` on issue `{issue_name}`.\n"
        f"Current playbook: `{playbook_id}`. Current workflow step: `{current_step}`.\n"
        f"Shared blackboard path: `{blackboard_path}`.\n"
        f"Workflow baton path: `{next_step_path}`."
    )


def _prepare_chat_environment(
    *,
    executor,
    agent_manager: AgentManager,
    agent_name: str,
    agent_cli: AgentCLI | object,
    role: str,
    issue_name: str,
    issue_dir: Path,
    current_step: str,
    valid_steps: list[str],
    playbook_id: str,
) -> None:
    """Install shared chat skills and seed the interactive session context."""
    if not isinstance(agent_cli, AgentCLI):
        return

    loader = SkillLoader()
    loader.discover()
    bridge = NativeSkillBridge(loader)

    invocations: dict[str, str] = {}
    for skill_name in CHAT_SKILL_NAMES:
        bridge.install_skill(skill_name, agent_cli)
        invocations[skill_name] = bridge.get_invocation(skill_name, agent_cli)
    prompt = _build_chat_seed_prompt(
        role=role,
        issue_name=issue_name,
        blackboard_path=issue_dir / "blackboard.json",
        next_step_path=get_chat_next_step_path(issue_dir),
        current_step=current_step,
        playbook_id=playbook_id,
    )

    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            agent_manager.execute(agent_name, prompt)
    except Exception as exc:
        print(f"\n⚠️  Failed to seed chat skills for {agent_name}: {exc}\n")


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
    # Load configuration
    config_manager = ConfigManager()
    agent_config = config_manager.get(f"agents.{role}", None)

    if agent_config is None:
        print(f"\n⚠️  No agent configured for role '{role}'. Skipping chat.\n")
        return 0

    agent_name = agent_config.get("name")
    agent_cli_str = agent_config.get("cli")
    agent_model = agent_config.get("model")

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

    issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name
    current_step, valid_steps, playbook_id = _prepare_chat_handoff_state(issue_dir)

    _prepare_chat_environment(
        executor=executor,
        agent_manager=agent_manager,
        agent_name=agent_name,
        agent_cli=agent_cli,
        role=role,
        issue_name=issue_name,
        issue_dir=issue_dir,
        current_step=current_step,
        valid_steps=valid_steps,
        playbook_id=playbook_id,
    )
    session_id: Optional[str] = executor.config.session_id

    print(f"\nOpening chat with {role} ({agent_name})...")
    if session_id:
        print(f"Resuming session: {session_id}")
    print()

    # Build CLI command
    cli_command = [agent_cli_str]
    codex_history_start_ts = int(time.time())

    if agent_cli_str == "codex" and agent_model:
        cli_command.extend(["--model", agent_model])

    if session_id:
        if agent_cli_str in ("claude", "copilot", "gemini"):
            cli_command.extend(["--resume", session_id])
        elif agent_cli_str == "cursor-agent":
            cli_command.extend(["--session", session_id])
        elif agent_cli_str == "codex":
            cli_command.extend(["resume", session_id])

    if agent_model:
        if agent_cli_str in ("claude", "copilot", "gemini"):
            cli_command.extend(["--model", agent_model])

    # Execute interactive CLI (blocks until user exits)
    try:
        result = subprocess.run(cli_command, env=cli_strategy.build_environment())
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

    if not get_chat_next_step_path(issue_dir).exists():
        print("\n⚠️  Chat ended without writing a next-step baton. The agent did not complete workflow handoff.\n")

    return result.returncode
