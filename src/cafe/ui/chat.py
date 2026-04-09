"""Reusable chat launcher for inline agent chat sessions.

Provides launch_chat_session() which can be called from interactive prompts
across any workflow phase to open a chat session with the relevant agent.
"""

import io
import json
import subprocess
import time
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
from typing import Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.workflow_instance import WorkflowInstance
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.config import ConfigManager


CHAT_SKILL_NAMES = [
    "common-chat-handoff",
    "chat-develop-change",
    "chat-spec-revision",
    "chat-plan-revision",
]

CHAT_HANDOFF_STATUS_CODE = "CHAT_ARTIFACT_UPDATED"


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


def _latest_phase_output(issue_dir: Path, phase_name: str) -> Optional[Path]:
    phase_dir = issue_dir / phase_name
    if not phase_dir.exists():
        return None
    files = sorted(phase_dir.glob("iteration_*/output.md"))
    return files[-1] if files else None


def _file_digest(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    return sha256(path.read_bytes()).hexdigest()


def _workspace_signature() -> str:
    result = subprocess.run(
        ["git", "status", "--short", ".", ":(exclude).cafe", ":(exclude).codex", ":(exclude).claude", ":(exclude).gemini", ":(exclude).copilot", ":(exclude).cursor-agent"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _capture_chat_state(issue_dir: Path) -> dict[str, str]:
    spec_output = _latest_phase_output(issue_dir, "spec")
    plan_output = _latest_phase_output(issue_dir, "plan")
    return {
        "spec_path": str(spec_output) if spec_output else "",
        "spec_digest": _file_digest(spec_output),
        "plan_path": str(plan_output) if plan_output else "",
        "plan_digest": _file_digest(plan_output),
        "workspace_signature": _workspace_signature(),
    }


def _resolve_chat_followup_step(before: dict[str, str], after: dict[str, str]) -> Optional[str]:
    if before.get("spec_digest") != after.get("spec_digest") or before.get("spec_path") != after.get("spec_path"):
        return "plan"
    if before.get("plan_digest") != after.get("plan_digest") or before.get("plan_path") != after.get("plan_path"):
        return "develop"
    if before.get("workspace_signature") != after.get("workspace_signature"):
        return "review"
    return None


def _persist_chat_followup(issue_dir: Path, target_step: Optional[str]) -> None:
    if not target_step:
        return

    instance = WorkflowInstance.load(issue_dir)
    if instance is None:
        return

    blackboard_store = BlackboardStore(issue_dir)
    blackboard = blackboard_store.load_or_create(instance.current_step)
    previous_step = instance.current_step

    blackboard_store.record_event(
        blackboard,
        "chat_handoff",
        {
            "step": previous_step,
            "from": previous_step,
            "to": target_step,
            "status_code": CHAT_HANDOFF_STATUS_CODE,
        },
    )
    blackboard_store.record_decision(
        blackboard,
        {
            "from": previous_step,
            "to": target_step,
            "status_code": CHAT_HANDOFF_STATUS_CODE,
        },
    )
    blackboard_store.set_current_step(blackboard, target_step)
    instance.transition_to(target_step, CHAT_HANDOFF_STATUS_CODE)


def _build_chat_seed_prompt(
    *,
    role: str,
    issue_name: str,
    invocations: dict[str, str],
) -> str:
    """Build the chat bootstrap prompt for one interactive session."""
    return (
        f"You are entering a `cafe chat` session for role `{role}` on issue `{issue_name}`.\n\n"
        "The following CLI-native skills are already installed for this session:\n"
        f"- Shared handoff: {invocations['common-chat-handoff']}\n"
        f"- Develop change: {invocations['chat-develop-change']}\n"
        f"- Spec revision: {invocations['chat-spec-revision']}\n"
        f"- Plan revision: {invocations['chat-plan-revision']}\n\n"
        "Use the shared handoff skill as the default workflow discipline for project-related chat.\n"
        "When the conversation turns into code changes, spec revisions, or plan revisions, explicitly use the matching chat skill.\n"
        "These skills apply to any agent role that encounters those situations.\n"
        "Do not emit the handoff closing block on every answer; only use it when you are wrapping up or summarizing the session.\n"
        "Do not hand the user a phase-specific command.\n"
        "For workflow-related chat, end by telling the user to exit chat and run `cafe make`."
    )


def _prepare_chat_environment(
    *,
    executor,
    agent_manager: AgentManager,
    agent_name: str,
    agent_cli: AgentCLI | object,
    role: str,
    issue_name: str,
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
        invocations=invocations,
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

    session_id: Optional[str] = executor.config.session_id
    issue_dir = Path.cwd() / ".cafe" / "issues" / issue_name
    chat_state_before = _capture_chat_state(issue_dir)

    _prepare_chat_environment(
        executor=executor,
        agent_manager=agent_manager,
        agent_name=agent_name,
        agent_cli=agent_cli,
        role=role,
        issue_name=issue_name,
    )
    session_id = executor.config.session_id

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
        result = subprocess.run(cli_command)
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

    chat_state_after = _capture_chat_state(issue_dir)
    _persist_chat_followup(
        issue_dir,
        _resolve_chat_followup_step(chat_state_before, chat_state_after),
    )

    return result.returncode
