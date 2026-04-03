"""Reusable exec launcher for single-shot agent prompt execution.

Provides launch_exec_session() which executes a single prompt through the
configured role's agent CLI, without entering an interactive session.
"""

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

from cafe.agents.cli import ClaudeCLI, CodexCLI, CopilotCLI, CursorCLI, GeminiCLI
from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig
from cafe.utils.config import ConfigManager


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


def _get_cli_strategy(config: AgentConfig):
    """Get the CLI strategy instance for the given agent config.

    Args:
        config: Agent configuration

    Returns:
        CLI strategy instance
    """
    if config.cli == AgentCLI.CLAUDE:
        return ClaudeCLI(config)
    elif config.cli == AgentCLI.GEMINI:
        return GeminiCLI(config)
    elif config.cli == AgentCLI.CURSOR:
        return CursorCLI(config)
    elif config.cli == AgentCLI.CODEX:
        return CodexCLI(config)
    elif config.cli == AgentCLI.COPILOT:
        return CopilotCLI(config)
    else:
        raise ValueError(f"Unsupported agent CLI: {config.cli}")


def launch_exec_session(
    role: str,
    issue_name: str,
    prompt: str,
    display_only: bool = False,
) -> int:
    """Execute a single prompt through the agent CLI for the given role.

    Resolves agent config from ConfigManager, loads the existing session,
    builds the CLI command using the established CLI strategy, and either
    executes it or displays the command string (when display_only=True).

    Args:
        role: Agent role ("pm", "developer", or "reviewer")
        issue_name: Current issue name (used to load issue-specific session)
        prompt: Prompt text to send to the agent
        display_only: If True, print the CLI command instead of executing it
    """
    # Load configuration
    config_manager = ConfigManager()
    agent_config = config_manager.get(f"agents.{role}", None)

    if agent_config is None:
        print(f"\n⚠️  No agent configured for role '{role}'. Skipping exec.\n")
        return 0

    agent_name = agent_config.get("name")
    agent_cli_str = agent_config.get("cli")
    agent_model = agent_config.get("model")

    if not agent_name or not agent_cli_str:
        print(f"\n⚠️  Invalid agent configuration for role '{role}'. Skipping exec.\n")
        return 0

    # Set up agent manager and load existing session
    agent_manager = AgentManager(issue_name=issue_name)
    try:
        agent_cli = AgentCLI(agent_cli_str)
    except ValueError:
        print(f"\n⚠️  Unknown CLI tool '{agent_cli_str}'. Skipping exec.\n")
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
        print(f"\n⚠️  Failed to get agent '{agent_name}': {e}. Skipping exec.\n")
        return 0

    session_id: Optional[str] = executor.config.session_id
    codex_history_start_ts = int(time.time())

    # Build CLI command using the established CLI strategy
    try:
        cli_strategy = _get_cli_strategy(executor.config)
        cli_command = cli_strategy.build_command(prompt)
    except ValueError as e:
        print(f"\n⚠️  {e}. Skipping exec.\n")
        return 0

    if display_only:
        print(shlex.join(cli_command))
        return 0

    # Execute non-interactive CLI
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

    return result.returncode
