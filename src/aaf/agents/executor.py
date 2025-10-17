"""Agent executor for running AI agents."""

import json
import subprocess
from typing import Any, Dict

from aaf.core.types import AgentConfig, AgentTool


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
            if self.config.tool == AgentTool.CLAUDE:
                return self._execute_claude(prompt)
            elif self.config.tool == AgentTool.GEMINI:
                return self._execute_gemini(prompt)
            elif self.config.tool == AgentTool.CURSOR:
                return self._execute_cursor(prompt)
            else:
                raise AgentExecutionError(f"Unsupported agent tool: {self.config.tool}")
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
        cmd = ["claude", "run"]

        # Add session if available
        if self.config.session_id:
            cmd.extend(["--session", self.config.session_id])

        # Add allowed tools
        for tool in self.config.allowed_tools:
            cmd.extend(["--allow", tool])

        # Add prompt
        cmd.append(prompt)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise AgentExecutionError(
                f"Claude execution failed with code {result.returncode}: {result.stderr}"
            )

        # Parse JSON response
        try:
            response_data = json.loads(result.stdout)
            return response_data.get("content", result.stdout)
        except json.JSONDecodeError:
            # If not JSON, return raw output
            return result.stdout

    def _execute_gemini(self, prompt: str) -> str:
        """Execute Gemini agent.

        Args:
            prompt: Prompt to send to Gemini

        Returns:
            Gemini's response
        """
        # Placeholder for Gemini implementation
        raise NotImplementedError("Gemini execution not yet implemented")

    def _execute_cursor(self, prompt: str) -> str:
        """Execute Cursor agent.

        Args:
            prompt: Prompt to send to Cursor

        Returns:
            Cursor's response
        """
        # Placeholder for Cursor implementation
        raise NotImplementedError("Cursor execution not yet implemented")
