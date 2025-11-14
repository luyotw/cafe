"""Mock agent executor for testing."""

from typing import Callable, List, Optional, Tuple

from cafe.core.types import AgentConfig, AgentResponse, TokenUsage


class MockAgentExecutor:
    """Mock agent executor that returns predefined responses.
    
    用於測試時 mock agent 的行為，避免實際呼叫 LLM API。
    
    Example:
        # 創建 mock executor
        executor = MockAgentExecutor(
            config=AgentConfig(name="TestAgent", cli="claude"),
            response="CONFIRMED\n這是測試回應"
        )
        
        # 使用 mock executor 替換真實的 executor
        agent_manager.agents["TestAgent"] = executor
    """

    def __init__(
        self,
        config: AgentConfig,
        response: str = "CAFE_CONFIRMED\n\n# Mock Response\n\nThis is a mock response.",
        token_usage: Optional[TokenUsage] = None,
    ):
        """Initialize mock executor.
        
        Args:
            config: Agent configuration
            response: Predefined response (default: CAFE_CONFIRMED with mock content)
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
    ) -> AgentResponse:
        """Execute with predefined response.

        Args:
            prompt: Prompt text (saved but not used)
            tools: Tool names (saved but not used)
            json_content_extractor: JSON extractor (not used)

        Returns:
            AgentResponse with response, token_usage, and permission_denials
        """
        self.call_count += 1
        self.last_prompt = prompt
        self.last_tools = tools
        return AgentResponse(
            response=self._response,
            token_usage=self._token_usage,
            permission_denials=[]
        )

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
