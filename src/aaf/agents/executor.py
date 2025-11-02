"""Agent executor for running AI agents."""

import json
import subprocess
from typing import List, Optional, Tuple

from aaf.core.types import AgentConfig, AgentCLI, TokenUsage


class AgentExecutionError(Exception):
    """Agent execution error."""

    pass


class AgentExecutor:
    """Executes AI agents and handles their responses."""

    # Tool name mapping from Claude syntax to other CLIs
    # Reference: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/file-system.md
    TOOL_NAME_MAP = {
        AgentCLI.CLAUDE: {
            # Claude uses these names (standard)
            "bash": "Bash",
            "read": "Read",
            "write": "Write",
            "edit": "Edit",
            "grep": "Grep",
            "glob": "Glob",
        },
        AgentCLI.GEMINI: {
            # Gemini tool name translations
            "bash": "bash",
            "read": "read_file",
            "write": "write_file",
            "edit": "replace",
            "grep": "search_file_content",
            "glob": "glob",
        },
        AgentCLI.CURSOR: {
            # Cursor tool name translations (TBD)
            "bash": "bash",
            "read": "read",
            "write": "write",
            "edit": "edit",
            "grep": "grep",
            "glob": "glob",
        },
        AgentCLI.COPILOT: {
            # GitHub Copilot CLI tool name translations
            # Reference: https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli#using-the-approval-options
            "bash": "shell",
            "read": "write",  # Copilot uses 'write' for all file operations
            "write": "write",
            "edit": "write",
            "grep": "shell",
            "glob": "shell",
        },
    }

    def __init__(self, config: AgentConfig) -> None:
        """Initialize agent executor.

        Args:
            config: Agent configuration
        """
        self.config = config
        self._total_token_usage = TokenUsage()

    def _translate_tool_names(self, tools: Optional[List[str]]) -> Optional[List[str]]:
        """Translate tool names from Claude convention to current CLI convention.

        Args:
            tools: List of tool names in Claude convention

        Returns:
            List of tool names translated for current CLI, or None if no tools
        """
        if not tools:
            return None

        tool_map = self.TOOL_NAME_MAP.get(self.config.cli, {})
        return [tool_map.get(tool, tool) for tool in tools]

    def execute(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute the agent with given prompt.

        Args:
            prompt: Prompt to send to the agent
            allowed_tools: List of allowed tools (using Claude naming convention)

        Returns:
            Tuple of (agent's response, token usage statistics)

        Raises:
            AgentExecutionError: If agent execution fails
        """
        # Translate tool names to the appropriate CLI convention
        translated_tools = self._translate_tool_names(allowed_tools)

        try:
            if self.config.cli == AgentCLI.CLAUDE:
                response, token_usage = self._execute_claude(prompt, translated_tools)
            elif self.config.cli == AgentCLI.GEMINI:
                response, token_usage = self._execute_gemini(prompt, translated_tools)
            elif self.config.cli == AgentCLI.CURSOR:
                response, token_usage = self._execute_cursor(prompt, translated_tools)
            elif self.config.cli == AgentCLI.COPILOT:
                response, token_usage = self._execute_copilot(prompt, translated_tools)
            else:
                raise AgentExecutionError(f"Unsupported agent CLI: {self.config.cli}")

            # Accumulate token usage
            self._total_token_usage.input_tokens += token_usage.input_tokens
            self._total_token_usage.output_tokens += token_usage.output_tokens
            self._total_token_usage.cache_creation_input_tokens += token_usage.cache_creation_input_tokens
            self._total_token_usage.cache_read_input_tokens += token_usage.cache_read_input_tokens
            self._total_token_usage.total_cost_usd += token_usage.total_cost_usd

            return response, token_usage
        except AgentExecutionError:
            raise
        except Exception as e:
            raise AgentExecutionError(f"Agent execution failed: {e}") from e

    def get_total_token_usage(self) -> TokenUsage:
        """Get total accumulated token usage across all execute() calls.

        Returns:
            Total token usage statistics
        """
        return self._total_token_usage

    def _execute_claude(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute Claude agent.

        Args:
            prompt: Prompt to send to Claude
            allowed_tools: List of allowed tools (already translated)

        Returns:
            Tuple of (Claude's response, token usage)
        """
        # Build command: prompt must come first, then options
        cmd = ["claude", "--print", prompt]

        # Add allowed tools if specified
        if allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(allowed_tools)])

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
            response = response_data.get("result", result.stdout)

            # Parse token usage
            usage_data = response_data.get("usage", {})
            token_usage = TokenUsage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
                total_cost_usd=response_data.get("total_cost_usd", 0.0)
            )

            return response, token_usage
        except json.JSONDecodeError:
            # If not JSON, return raw output with empty token usage
            return result.stdout, TokenUsage()

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

    def _execute_gemini(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute Gemini agent.

        Args:
            prompt: Prompt to send to Gemini
            allowed_tools: List of allowed tools (already translated)

        Returns:
            Tuple of (Gemini's response, token usage)
        """
        # Build command: use positional prompt
        cmd = ["gemini", prompt]

        # Add allowed tools if specified
        if allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(allowed_tools)])

        cmd.extend(["--output-format", "json"])

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
            response = response_data.get("response", result.stdout)

            # Parse token usage (Gemini format may differ from Claude)
            # TODO: Update when Gemini CLI provides token usage info
            token_usage = TokenUsage()

            return response, token_usage
        except json.JSONDecodeError:
            # If not JSON, return raw output with empty token usage
            return result.stdout, TokenUsage()

    def _execute_cursor(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute Cursor agent.

        Args:
            prompt: Prompt to send to Cursor
            allowed_tools: List of allowed tools (already translated)

        Returns:
            Tuple of (Cursor's response, token usage)
        """
        # Placeholder for Cursor implementation
        raise NotImplementedError("Cursor execution not yet implemented")

    def _execute_copilot(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> Tuple[str, TokenUsage]:
        """Execute GitHub Copilot CLI agent.

        Args:
            prompt: Prompt to send to Copilot
            allowed_tools: List of allowed tools (already translated)

        Returns:
            Tuple of (Copilot's response, token usage)
        """
        # Build command: copilot -p "prompt" --allow-all-tools or --allow-tool
        cmd = ["copilot", "-p", prompt]

        # Add allowed tools if specified
        if allowed_tools:
            # Use --allow-tool for each tool
            for tool in allowed_tools:
                cmd.extend(["--allow-tool", tool])
        else:
            # Use --allow-all-tools for automatic approval
            cmd.append("--allow-all-tools")

        # Add session if configured
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise AgentExecutionError(
                f"Copilot execution failed with code {result.returncode}: {result.stderr}"
            )

        # Copilot doesn't provide JSON output format yet, return raw output
        # TODO: Update when Copilot CLI provides structured output
        response = result.stdout
        token_usage = TokenUsage()

        return response, token_usage
