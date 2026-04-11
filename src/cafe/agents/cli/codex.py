"""Codex CLI tool implementation."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_git_dir


class CodexCLI(AbstractCLI):
    """Concrete implementation of Codex CLI tool."""

    def build_environment(self) -> dict[str, str]:
        """Point Codex at the repo-local skill and state directory."""
        env = dict(os.environ)
        env["CODEX_HOME"] = str(Path.cwd().resolve() / ".codex")
        return env

    @staticmethod
    def extract_turn_usages(output_lines: List[str]) -> List[Dict[str, Any]]:
        """Extract per-turn token usage from Codex JSONL output."""
        turn_usages: List[Dict[str, Any]] = []

        for line in output_lines:
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if data.get("type") != "turn.completed":
                continue

            usage_data = data.get("usage", {})
            if not usage_data:
                continue

            turn_usages.append(
                {
                    "turn": len(turn_usages) + 1,
                    "input_tokens": usage_data.get("input_tokens", 0),
                    "output_tokens": usage_data.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage_data.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage_data.get("cached_input_tokens", 0),
                }
            )

        return turn_usages

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Codex CLI command line arguments."""
        cwd = Path.cwd().resolve()
        cmd = ["codex", "-C", str(cwd), "-a", "never"]

        if self.config.session_id:
            cmd.extend(["exec", "resume"])
        else:
            cmd.append("exec")

        if self.config.model:
            cmd.extend(["--model", self.config.model])

        cmd.extend(self.get_output_format())

        if allowed_directories and not self.config.session_id:
            cmd = self.add_directories(cmd, self._expand_initial_allowed_directories(allowed_directories, cwd))

        if self.config.session_id:
            cmd.append(self.config.session_id)

        cmd.append(prompt)
        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Codex CLI's JSONL event stream."""
        response_text = ""
        token_usage = TokenUsage()
        permission_denials: List[PermissionDenial] = []
        turn_usages = self.extract_turn_usages(output_lines)

        for line in output_lines:
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if data.get("type") == "item.completed":
                item = data.get("item", {})
                if item.get("type") == "agent_message":
                    response_text = item.get("text", "")

            if data.get("type") == "turn.completed":
                usage_data = data.get("usage", {})
                token_usage = TokenUsage(
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    cache_read_input_tokens=usage_data.get("cached_input_tokens", 0),
                    cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                    turn_usages=turn_usages,
                )

        return response_text, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Return an empty list because Codex CLI does not expose tool allowlists."""
        return []

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add allowed directories to command line arguments."""
        for directory in directories:
            cmd.extend(["--add-dir", directory])
        return cmd

    def _expand_initial_allowed_directories(self, directories: List[str], cwd: Path) -> List[str]:
        """Expand initial exec directories for worktrees.

        In worktree mode, git writes lock files under the main repo's
        `.git/worktrees/<name>` directory, which is outside the worktree cwd.
        Include that directory on fresh `codex exec` runs so commit/stage commands
        have a chance to succeed without host-side fallback.
        """
        expanded = list(directories)

        try:
            git_dir = get_git_dir(cwd)
        except ValueError:
            return expanded

        git_dir_str = str(git_dir)
        if not git_dir.is_relative_to(cwd) and git_dir_str not in expanded:
            expanded.append(git_dir_str)

        return expanded

    def get_output_format(self) -> List[str]:
        """Get Codex CLI's output format parameters."""
        return ["--json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output."""
        for line in output_lines:
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if data.get("type") == "thread.started" and "thread_id" in data:
                return data["thread_id"]
            if "session_id" in data:
                return data["session_id"]

        return None

    def create_session(self) -> str:
        """Create a new Codex exec session and return its thread ID."""
        cwd = Path.cwd().resolve()
        cmd = ["codex", "-C", str(cwd), "-a", "never", "exec"]

        if self.config.model:
            cmd.extend(["--model", self.config.model])

        cmd.extend(self.get_output_format())
        cmd.append("Say 'hi'")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=self.build_environment(),
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create Codex session: {result.stderr}")

        session_id = self.extract_session_id(result.stdout.splitlines())
        if not session_id:
            raise RuntimeError("Could not extract Codex session ID from output")

        return session_id
