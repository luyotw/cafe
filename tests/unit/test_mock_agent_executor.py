"""Tests for MockAgentExecutor."""

import pytest
from aaf.agents.mock_executor import MockAgentExecutor
from aaf.core.types import AgentConfig, AgentCLI, TokenUsage


class TestMockAgentExecutor:
    """測試 MockAgentExecutor 的基本功能"""

    def test_default_response(self):
        """測試預設回應為 CONFIRMED"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config)
        
        # Act
        response, usage = executor.execute("test prompt")
        
        # Assert
        assert response == "CONFIRMED"
        assert isinstance(usage, TokenUsage)

    def test_custom_response(self):
        """測試自訂回應"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(
            config=config,
            response="NEED_CLARIFICATION\n請提供更多資訊"
        )
        
        # Act
        response, usage = executor.execute("test prompt")
        
        # Assert
        assert "NEED_CLARIFICATION" in response
        assert "請提供更多資訊" in response

    def test_tracks_call_count(self):
        """測試追蹤呼叫次數"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config)
        
        # Act
        executor.execute("prompt 1")
        executor.execute("prompt 2")
        executor.execute("prompt 3")
        
        # Assert
        assert executor.call_count == 3

    def test_saves_last_prompt(self):
        """測試儲存最後一次的 prompt"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config)
        
        # Act
        executor.execute("first prompt")
        executor.execute("second prompt")
        
        # Assert
        assert executor.last_prompt == "second prompt"

    def test_saves_last_tools(self):
        """測試儲存最後一次的 tools"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config)
        
        # Act
        executor.execute("prompt", tools=["bash", "read"])
        
        # Assert
        assert executor.last_tools == ["bash", "read"]

    def test_set_response_changes_response(self):
        """測試動態修改回應"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config, response="first")
        
        # Act
        response1, _ = executor.execute("prompt")
        executor.set_response("second")
        response2, _ = executor.execute("prompt")
        
        # Assert
        assert response1 == "first"
        assert response2 == "second"

    def test_custom_token_usage(self):
        """測試自訂 token usage"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        custom_usage = TokenUsage(input_tokens=100, output_tokens=50)
        executor = MockAgentExecutor(config=config, token_usage=custom_usage)
        
        # Act
        _, usage = executor.execute("prompt")
        
        # Assert
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_reset_clears_tracking(self):
        """測試 reset 清除追蹤資料"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        executor = MockAgentExecutor(config=config)
        executor.execute("prompt", tools=["bash"])
        
        # Act
        executor.reset()
        
        # Assert
        assert executor.call_count == 0
        assert executor.last_prompt is None
        assert executor.last_tools is None

    def test_config_accessible(self):
        """測試可以存取 config"""
        # Arrange
        config = AgentConfig(name="TestAgent", cli=AgentCLI.GEMINI)
        executor = MockAgentExecutor(config=config)
        
        # Assert
        assert executor.config.name == "TestAgent"
        assert executor.config.cli == AgentCLI.GEMINI
