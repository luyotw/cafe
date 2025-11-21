"""Tests for AgentManager mock mode."""

import os
import pytest
from unittest.mock import MagicMock

from cafe.agents.manager import AgentManager
from cafe.agents.mock_executor import MockAgentExecutor
from cafe.core.types import AgentConfig, AgentCLI


class TestAgentManagerMockMode:
    """測試 AgentManager 的 mock mode 功能"""

    def test_mock_mode_disabled_by_default(self):
        """測試預設不啟用 mock mode"""
        # Arrange & Act
        manager = AgentManager()
        
        # Assert
        assert manager._use_mock is False

    def test_mock_mode_enabled_with_env_var_true(self, monkeypatch):
        """測試 CAFE_MOCK_AGENTS=true 啟用 mock mode"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        
        # Act
        manager = AgentManager()
        
        # Assert
        assert manager._use_mock is True

    def test_mock_mode_enabled_with_env_var_1(self, monkeypatch):
        """測試 CAFE_MOCK_AGENTS=1 啟用 mock mode"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "1")
        
        # Act
        manager = AgentManager()
        
        # Assert
        assert manager._use_mock is True

    def test_mock_mode_enabled_with_env_var_yes(self, monkeypatch):
        """測試 CAFE_MOCK_AGENTS=yes 啟用 mock mode"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "yes")
        
        # Act
        manager = AgentManager()
        
        # Assert
        assert manager._use_mock is True

    def test_register_agent_creates_mock_executor_when_enabled(self, monkeypatch):
        """測試啟用 mock mode 時註冊 agent 會創建 MockAgentExecutor"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        manager = AgentManager()
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        
        # Act
        manager.register_agent(config)
        
        # Assert
        assert "TestAgent" in manager.agents
        assert isinstance(manager.agents["TestAgent"], MockAgentExecutor)

    def test_mock_executor_uses_default_response(self, monkeypatch):
        """測試 mock executor 使用預設回應 READY_FOR_REVIEW"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        manager = AgentManager()
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        # Act
        executor = manager.get_agent("TestAgent")
        agent_response = executor.execute("test prompt")

        # Assert
        assert "CAFE_READY_FOR_REVIEW" in agent_response.response

    def test_mock_executor_uses_custom_response_from_env(self, monkeypatch):
        """測試 mock executor 可以從環境變數自訂回應"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        monkeypatch.setenv("CAFE_MOCK_RESPONSE", "NEED_CLARIFICATION\n請提供更多資訊")
        manager = AgentManager()
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)
        
        # Act
        executor = manager.get_agent("TestAgent")
        agent_response = executor.execute("test prompt")

        # Assert
        assert "NEED_CLARIFICATION" in agent_response.response
        assert "請提供更多資訊" in agent_response.response

    def test_mock_mode_does_not_create_real_executor(self, monkeypatch):
        """測試 mock mode 不會創建真實的 AgentExecutor"""
        # Arrange
        monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")
        manager = AgentManager()
        config = AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        
        # Act
        manager.register_agent(config)
        executor = manager.agents["TestAgent"]
        
        # Assert
        assert type(executor).__name__ == "MockAgentExecutor"
        assert not type(executor).__name__ == "AgentExecutor"
