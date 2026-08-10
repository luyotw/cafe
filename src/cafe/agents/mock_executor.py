"""Mock agent executor for testing."""

import re
from pathlib import Path
from typing import Callable, List, Optional

from cafe.core.types import AgentConfig, AgentResponse, TokenUsage


class MockAgentExecutor:
    """Mock agent executor that returns predefined responses.

    Used to mock agent behavior during testing to avoid actual LLM API calls.

    Example:
        # Create mock executor
        executor = MockAgentExecutor(
            config=AgentConfig(name="TestAgent", cli="claude"),
            response="CONFIRMED\nThis is a test response"
        )

        # Use mock executor to replace real executor
        agent_manager.agents["TestAgent"] = executor
    """

    def __init__(
        self,
        config: AgentConfig,
        response: str = "ready_for_review\n\n# Mock Response\n\nThis is a mock response.",
        token_usage: Optional[TokenUsage] = None,
    ):
        """Initialize mock executor.

        Args:
            config: Agent configuration
            response: Predefined response (default: ready_for_review with mock content)
            token_usage: Predefined token usage (default: empty)
        """
        self.config = config
        self._response = response
        self._token_usage = token_usage or TokenUsage()
        self.call_count = 0
        self.last_prompt = None
        self.last_tools = None

    def execute(
        self,
        prompt: str,
        tools: Optional[List[str]] = None,
        json_content_extractor: Optional[Callable] = None,
        streaming_output_file: Optional[str] = None,
    ) -> AgentResponse:
        """Execute with predefined response.

        Args:
            prompt: Prompt text (saved but not used)
            tools: Tool names (saved but not used)
            json_content_extractor: JSON extractor (not used)
            streaming_output_file: Streaming output file (not used in mock)

        Returns:
            AgentResponse with response, token_usage, and permission_denials
        """
        self.call_count += 1
        self.last_prompt = prompt
        self.last_tools = tools
        self._write_runtime_output_file(prompt)
        return AgentResponse(
            response=self._response,
            token_usage=self._token_usage,
            permission_denials=[],
            cli=self.config.cli,
            session_id=self.config.session_id,
        )

    def preview_cli_command_args(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Return deterministic preview args for workflow logging/tests."""
        return [
            "--mock-agent",
            self.config.name,
            "--prompt",
            prompt,
        ]

    def preview_cli_environment(self) -> dict[str, str]:
        """Return mock execution environment."""
        return {}

    def _write_runtime_output_file(self, prompt: str) -> None:
        match = re.search(r"^output_file=(.+)$", prompt, flags=re.MULTILINE)
        if not match:
            return
        output_file = Path(match.group(1).strip())
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(self._response, encoding="utf-8")

    def set_response(self, response: str):
        """Set response for next execution."""
        self._response = response

    def set_token_usage(self, token_usage: TokenUsage):
        """Set token usage for next execution."""
        self._token_usage = token_usage

    def reset(self):
        """Reset call tracking."""
        self.call_count = 0
        self.last_prompt = None
        self.last_tools = None
