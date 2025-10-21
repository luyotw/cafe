"""Agent executor for running AI agents."""

import json
import subprocess
from typing import Any, Dict

from aaf.core.types import AgentConfig, AgentCLI


class AgentExecutionError(Exception):
    """Agent execution error."""

    pass


class AgentExecutor:
    """Executes AI agents and handles their responses."""

    def __init__(self, config: AgentConfig) -> None:
        """Initialize agent executor.

        Args:
            config: Agent configuration
        """
        self.config = config

    def execute(self, prompt: str) -> str:
        """Execute the agent with given prompt.

        Args:
            prompt: Prompt to send to the agent

        Returns:
            Agent's response

        Raises:
            AgentExecutionError: If agent execution fails
        """
        try:
            if self.config.cli == AgentCLI.CLAUDE:
                return self._execute_claude(prompt)
            elif self.config.cli == AgentCLI.GEMINI:
                return self._execute_gemini(prompt)
            elif self.config.cli == AgentCLI.CURSOR:
                return self._execute_cursor(prompt)
            else:
                raise AgentExecutionError(f"Unsupported agent CLI: {self.config.cli}")
        except AgentExecutionError:
            raise
        except Exception as e:
            raise AgentExecutionError(f"Agent execution failed: {e}") from e

    def _execute_claude(self, prompt: str) -> str:
        """Execute Claude agent.

        Args:
            prompt: Prompt to send to Claude

        Returns:
            Claude's response
        """
        # Build command: prompt must come first, then options
        cmd = ["claude", "--print", prompt]

        # Add session if available
        if self.config.session_id:
            cmd.extend(["--session-id", self.config.session_id])

        # Add output format last
        cmd.extend(["--output-format", "json"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            # Check if session is already in use
            if "already in use" in result.stderr:
                # Create a new session and retry
                new_session_id = self._create_new_session()
                self.config.session_id = new_session_id
                # Retry with new session
                return self._execute_claude(prompt)

            raise AgentExecutionError(
                f"Claude execution failed with code {result.returncode}: {result.stderr}"
            )

        # Parse JSON response
        try:
            response_data = json.loads(result.stdout)
            return response_data.get("result", result.stdout)
        except json.JSONDecodeError:
            # If not JSON, return raw output
            return result.stdout

    def _create_new_session(self) -> str:
        """Create a new Claude session.

        Returns:
            New session ID

        Raises:
            AgentExecutionError: If session creation fails
        """
        cmd = ["claude", "-p", "Say 'hi'", "--output-format", "json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise AgentExecutionError(
                f"Failed to create new session: {result.stderr}"
            )

        try:
            response_data = json.loads(result.stdout)
            session_id = response_data.get("session_id")
            if not session_id:
                raise AgentExecutionError("No session_id in response")
            return session_id
        except json.JSONDecodeError as e:
            raise AgentExecutionError(
                f"Failed to parse session creation response: {e}"
            ) from e

    def _execute_gemini(self, prompt: str) -> str:
        """Execute Gemini agent.

        Args:
            prompt: Prompt to send to Gemini

        Returns:
            Gemini's response
        """
        # Build command: use positional prompt
        cmd = ["gemini", prompt, "--output-format", "json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise AgentExecutionError(
                f"Gemini execution failed with code {result.returncode}: {result.stderr}"
            )

        # Parse JSON response
        try:
            response_data = json.loads(result.stdout)
            return response_data.get("response", result.stdout)
        except json.JSONDecodeError:
            # If not JSON, return raw output
            return result.stdout

    def _execute_cursor(self, prompt: str) -> str:
        """Execute Cursor agent.

        Args:
            prompt: Prompt to send to Cursor

        Returns:
            Cursor's response
        """
        # Placeholder for Cursor implementation
        raise NotImplementedError("Cursor execution not yet implemented")
