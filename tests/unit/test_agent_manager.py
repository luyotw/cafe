"""Tests for AgentManager."""

import pytest
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager, AgentNotFoundError
from cafe.agents.executor import AgentExecutor
from cafe.core.types import AgentConfig, AgentCLI
from cafe.core.session import SessionManager


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
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)

        manager.register_agent(config)

        assert "Roger" in manager.agents
        assert manager.agents["Roger"].config.name == config.name
        assert manager.agents["Roger"].config.cli == config.cli
        # session_id is None until first execution (lazy creation)
        assert manager.agents["Roger"].config.session_id is None


class TestAgentRetrieval:
    """Test agent retrieval."""

    def test_get_agent_returns_executor(self) -> None:
        """測試取得 agent 回傳 AgentExecutor"""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert isinstance(executor, AgentExecutor)
        assert executor.config.name == "David"

    def test_get_agent_not_found_raises_error(self) -> None:
        """測試取得不存在的 agent 拋出錯誤"""
        manager = AgentManager()

        with pytest.raises(AgentNotFoundError, match="Agent 'Unknown' not found"):
            manager.get_agent("Unknown")

    def test_get_agent_no_session_until_execute(self) -> None:
        """測試取得 agent 時 session 尚未建立（延遲創建）"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Session should be None until first execution (lazy creation)
        assert executor.config.session_id is None
        # Should not have saved any session yet
        session_mgr.save_session.assert_not_called()


class TestAgentSwitching:
    """Test agent switching."""

    def test_switch_to_existing_agent(self) -> None:
        """測試切換到已存在的 agent"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))

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
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
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
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage, AgentResponse
            mock_execute.return_value = AgentResponse(
                response="Agent response",
                token_usage=TokenUsage()
            )

            response, token_usage, permission_denials, cli_command_args = manager.execute("David", "Test prompt")

            assert response == "Agent response"
            mock_execute.assert_called_once_with("Test prompt", None)

    def test_execute_returns_tuple_with_token_usage(self) -> None:
        """測試 execute 回傳 4-tuple (response, token_usage, permission_denials, cli_command_args)"""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage, AgentResponse
            expected_token_usage = TokenUsage(input_tokens=100, output_tokens=50)
            mock_execute.return_value = AgentResponse(
                response="Agent response",
                token_usage=expected_token_usage
            )

            result = manager.execute("David", "Test prompt")

            # Should return 4-tuple (response, token_usage, permission_denials, cli_command_args)
            assert isinstance(result, tuple)
            assert len(result) == 4
            response, token_usage, permission_denials, cli_command_args = result
            assert response == "Agent response"
            assert token_usage.input_tokens == 100
            assert permission_denials == []
            assert cli_command_args is None
            assert token_usage.output_tokens == 50

    def test_execute_current_agent(self) -> None:
        """測試執行當前 agent"""
        manager = AgentManager()
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)
        manager.switch_agent("Roger")

        with patch.object(AgentExecutor, "execute") as mock_execute:
            from cafe.core.types import TokenUsage
            mock_execute.return_value = ("Current agent response", TokenUsage())

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
        from cafe.core.types import SessionData
        from datetime import datetime

        session_mgr = MagicMock(spec=SessionManager)
        # Return SessionData instead of string
        session_data = SessionData(
            agent_name="David",
            cli=AgentCLI.CLAUDE,
            session_id="existing-session-456",
            created_at=datetime.now(),
            last_used_at=datetime.now(),
        )
        session_mgr.load_session.return_value = session_data

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("David")

        assert executor.config.session_id == "existing-session-456"
        session_mgr.load_session.assert_called_once_with("David", AgentCLI.CLAUDE, None)

    def test_session_lazy_creation(self) -> None:
        """測試 session 延遲創建（在首次執行時）"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        executor = manager.get_agent("Roger")

        # Session is None until first execution (lazy creation by executor)
        assert executor.config.session_id is None
        # No session created at registration time
        session_mgr.save_session.assert_not_called()

    def test_create_claude_session_calls_cli(self) -> None:
        """測試 _create_claude_session 呼叫 Claude CLI 並解析 session ID"""
        import subprocess
        import json

        session_mgr = MagicMock(spec=SessionManager)
        manager = AgentManager(session_manager=session_mgr)

        # Mock subprocess.run to return a JSON response with session_id
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "session_id": "0603a149-90f6-4bc0-b687-3610aae4e082",
            "result": "Hi! How can I help you today?"
        })

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            session_id = manager._create_claude_session()

            # Should call claude with correct arguments
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "claude"
            assert "-p" in args or "--print" in args
            assert "--output-format" in args
            assert "json" in args

            # Should return the session_id from JSON response
            assert session_id == "0603a149-90f6-4bc0-b687-3610aae4e082"

    def test_create_claude_session_handles_error(self) -> None:
        """測試 _create_claude_session 處理錯誤"""
        import subprocess

        session_mgr = MagicMock(spec=SessionManager)
        manager = AgentManager(session_manager=session_mgr)

        # Mock subprocess.run to fail
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: API key not found"

        with patch('subprocess.run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="Failed to create Claude session"):
                manager._create_claude_session()

    def test_delete_agent_session(self) -> None:
        """測試刪除 agent session"""
        session_mgr = MagicMock(spec=SessionManager)
        session_mgr.load_session.return_value = None  # Mock to return None

        manager = AgentManager(session_manager=session_mgr)
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        manager.delete_session("David")

        session_mgr.delete_session.assert_called_once_with("David", AgentCLI.CLAUDE, None)


class TestMultipleAgents:
    """Test managing multiple agents."""

    def test_register_multiple_agents(self) -> None:
        """測試註冊多個 agents"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="Cursor", cli=AgentCLI.CURSOR))

        assert len(manager.agents) == 3
        assert "Roger" in manager.agents
        assert "David" in manager.agents
        assert "Cursor" in manager.agents

    def test_list_agents(self) -> None:
        """測試列出所有 agents"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))

        agent_names = manager.list_agents()

        assert len(agent_names) == 2
        assert "Roger" in agent_names
        assert "David" in agent_names

    def test_has_agent(self) -> None:
        """測試檢查 agent 是否存在"""
        manager = AgentManager()
        manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))

        assert manager.has_agent("Roger")
        assert not manager.has_agent("Unknown")


class TestAgentConfiguration:
    """Test agent configuration management."""

    def test_update_agent_config(self) -> None:
        """測試更新 agent 設定"""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        manager.register_agent(config)

        # Update to different CLI
        new_config = AgentConfig(
            name="David", cli=AgentCLI.GEMINI
        )
        manager.register_agent(new_config)

        executor = manager.get_agent("David")
        assert executor.config.cli == AgentCLI.GEMINI

    def test_get_agent_config(self) -> None:
        """測試取得 agent 設定"""
        manager = AgentManager()
        config = AgentConfig(
            name="Roger", cli=AgentCLI.CLAUDE
        )
        manager.register_agent(config)

        retrieved_config = manager.get_agent_config("Roger")

        assert retrieved_config.name == "Roger"
        assert retrieved_config.cli == AgentCLI.CLAUDE
