"""Tests for AgentManager."""

import pytest
from unittest.mock import MagicMock, patch

from aaf.agents.manager import AgentManager, AgentNotFoundError
from aaf.agents.executor import AgentExecutor
from aaf.core.types import AgentConfig, AgentTool
from aaf.core.session import SessionManager


class TestAgentManagerBasics:
    """Test basic AgentManager functionality."""

    def test_init_agent_manager(self) -> None:
        """測試初始化 AgentManager"""
        manager = AgentManager()
        assert manager is not None
        assert manager.agents == {}

    def test_init_with_session_manager(self) -> None:
        """測試使用 SessionManager 初始化"""
        session_mgr = SessionManager()
        manager = AgentManager(session_manager=session_mgr)

        assert manager.session_manager == session_mgr

    def test_register_agent(self) -> None:
        """測試註冊 agent"""
        manager = AgentManager()
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)

        manager.register_agent(config)

        assert "Roger" in manager.agents
        assert manager.agents["Roger"].config.name == config.name
        assert manager.agents["Roger"].config.tool == config.tool
        # session_id should be automatically assigned
        assert manager.agents["Roger"].config.session_id is not None


class TestAgentRetrieval:
    """Test agent retrieval."""

    def test_get_agent_returns_executor(self) -> None:
        """測試取得 agent 回傳 AgentExecutor"""
        manager = AgentManager()
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert isinstance(executor, AgentExecutor)
        assert executor.config.name == "David"

    def test_get_agent_not_found_raises_error(self) -> None:
        """測試取得不存在的 agent 拋出錯誤"""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError, match="Agent 'Unknown' not found"):
            manager.get_agent("Unknown")

    def test_get_agent_creates_session(self) -> None:
        """測試取得 agent 時建立 session"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Should have session_id set
        assert executor.config.session_id is not None
        assert executor.config.session_id.startswith("Roger-")
        # Should have saved the session
        session_mgr.save_session.assert_called_once()


class TestAgentSwitching:
    """Test agent switching."""

    def test_switch_to_existing_agent(self) -> None:
        """測試切換到已存在的 agent"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", tool=AgentTool.CLAUDE))
        manager.register_agent(AgentConfig(name="David", tool=AgentTool.CLAUDE))

        manager.switch_agent("Roger")
        assert manager.current_agent_name == "Roger"

        manager.switch_agent("David")
        assert manager.current_agent_name == "David"

    def test_switch_to_nonexistent_agent_raises_error(self) -> None:
        """測試切換到不存在的 agent 拋出錯誤"""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError):
            manager.switch_agent("Unknown")

    def test_get_current_agent(self) -> None:
        """測試取得當前 agent"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", tool=AgentTool.CLAUDE))
        manager.switch_agent("Roger")

        current = manager.get_current_agent()

        assert current is not None
        assert current.config.name == "Roger"

    def test_get_current_agent_when_none_returns_none(self) -> None:
        """測試沒有當前 agent 時回傳 None"""
        manager = AgentManager()

        current = manager.get_current_agent()

        assert current is None


class TestAgentExecution:
    """Test agent execution through manager."""

    def test_execute_with_agent_name(self) -> None:
        """測試使用 agent 名稱執行"""
        manager = AgentManager()
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        with patch.object(AgentExecutor, "execute") as mock_execute:
            mock_execute.return_value = "Agent response"

            response = manager.execute("David", "Test prompt")

            assert response == "Agent response"
            mock_execute.assert_called_once_with("Test prompt")

    def test_execute_current_agent(self) -> None:
        """測試執行當前 agent"""
        manager = AgentManager()
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        manager.register_agent(config)
        manager.switch_agent("Roger")

        with patch.object(AgentExecutor, "execute") as mock_execute:
            mock_execute.return_value = "Current agent response"

            response = manager.execute_current("Test prompt")

            assert response == "Current agent response"

    def test_execute_current_when_no_current_raises_error(self) -> None:
        """測試沒有當前 agent 時執行拋出錯誤"""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError, match="No current agent selected"):
            manager.execute_current("Test prompt")


class TestSessionManagement:
    """Test session management through AgentManager."""

    def test_resume_existing_session(self) -> None:
        """測試恢復現有 session"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = "existing-session-456"

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert executor.config.session_id == "existing-session-456"
        session_mgr.load_session.assert_called_once_with("David")

    def test_create_new_session_when_none_exists(self) -> None:
        """測試不存在 session 時建立新的"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Should generate and save a session ID
        assert executor.config.session_id is not None
        assert executor.config.session_id.startswith("Roger-")
        session_mgr.save_session.assert_called_once()

    def test_delete_agent_session(self) -> None:
        """測試刪除 agent session"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None  # Mock to return None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE)
        manager.register_agent(config)

        manager.delete_session("David")

        session_mgr.delete_session.assert_called_once_with("David")


class TestMultipleAgents:
    """Test managing multiple agents."""

    def test_register_multiple_agents(self) -> None:
        """測試註冊多個 agents"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", tool=AgentTool.CLAUDE))
        manager.register_agent(AgentConfig(name="David", tool=AgentTool.CLAUDE))
        manager.register_agent(AgentConfig(name="Cursor", tool=AgentTool.CURSOR))

        assert len(manager.agents) == 3
        assert "Roger" in manager.agents
        assert "David" in manager.agents
        assert "Cursor" in manager.agents

    def test_list_agents(self) -> None:
        """測試列出所有 agents"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", tool=AgentTool.CLAUDE))
        manager.register_agent(AgentConfig(name="David", tool=AgentTool.CLAUDE))

        agent_names = manager.list_agents()

        assert len(agent_names) == 2
        assert "Roger" in agent_names
        assert "David" in agent_names

    def test_has_agent(self) -> None:
        """測試檢查 agent 是否存在"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", tool=AgentTool.CLAUDE))

        assert manager.has_agent("Roger")
        assert not manager.has_agent("Unknown")


class TestAgentConfiguration:
    """Test agent configuration management."""

    def test_update_agent_config(self) -> None:
        """測試更新 agent 設定"""
        manager = AgentManager()
        config = AgentConfig(name="David", tool=AgentTool.CLAUDE, allowed_tools=[])
        manager.register_agent(config)

        # Update allowed tools
        new_config = AgentConfig(
            name="David", tool=AgentTool.CLAUDE, allowed_tools=["Bash(git:*)"]
        )
        manager.register_agent(new_config)

        executor = manager.get_agent("David")
        assert executor.config.allowed_tools == ["Bash(git:*)"]

    def test_get_agent_config(self) -> None:
        """測試取得 agent 設定"""
        manager = AgentManager()
        config = AgentConfig(
            name="Roger", tool=AgentTool.CLAUDE, allowed_tools=["Read(*)"]
        )
        manager.register_agent(config)

        retrieved_config = manager.get_agent_config("Roger")

        assert retrieved_config.name == "Roger"
        assert retrieved_config.allowed_tools == ["Read(*)"]
