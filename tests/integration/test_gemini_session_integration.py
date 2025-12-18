"""Integration tests for Gemini session management."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig
from cafe.core.session import SessionManager


class TestGeminiSessionIntegration:
    """Test Gemini session management with AgentManager."""

    def test_different_agents_use_separate_sessions(self, tmp_path: Path) -> None:
        """測試不同 Agent 使用各自 session"""
        # Setup
        session_manager = SessionManager(sessions_dir=str(tmp_path / ".cafe" / "sessions"))
        agent_manager = AgentManager(session_manager=session_manager)

        # Register two different agents with Gemini
        agent_manager.register_agent(AgentConfig(name="PM_Agent", cli=AgentCLI.GEMINI))
        agent_manager.register_agent(AgentConfig(name="Developer_Agent", cli=AgentCLI.GEMINI))

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # Mock PM_Agent execution
            mock_process_pm = MagicMock()
            mock_process_pm.stdout.readline.side_effect = [
                '{"type":"init","session_id":"pm-session-123","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"PM response"}\n',
                '{"response": "PM response"}\n',
                '',
            ]
            mock_process_pm.stderr.read.return_value = ""
            mock_process_pm.wait.return_value = 0

            # Mock Developer_Agent execution
            mock_process_dev = MagicMock()
            mock_process_dev.stdout.readline.side_effect = [
                '{"type":"init","session_id":"dev-session-456","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"Dev response"}\n',
                '{"response": "Dev response"}\n',
                '',
            ]
            mock_process_dev.stderr.read.return_value = ""
            mock_process_dev.wait.return_value = 0

            # Execute PM_Agent
            mock_popen.return_value = mock_process_pm
            pm_response, pm_usage, pm_denials, pm_cmd, pm_log = agent_manager.execute("PM_Agent", "PM prompt")
            assert pm_response == "PM response"

            # Execute Developer_Agent
            mock_popen.return_value = mock_process_dev
            dev_response, dev_usage, dev_denials, dev_cmd, dev_log = agent_manager.execute("Developer_Agent", "Dev prompt")
            assert dev_response == "Dev response"

            # Verify PM_Agent has its own session_id
            pm_executor = agent_manager.get_agent("PM_Agent")
            assert pm_executor.config.session_id == "pm-session-123"

            # Verify Developer_Agent has its own session_id
            dev_executor = agent_manager.get_agent("Developer_Agent")
            assert dev_executor.config.session_id == "dev-session-456"

            # Verify sessions are different
            assert pm_executor.config.session_id != dev_executor.config.session_id

    def test_same_agent_continues_session_across_executions(self, tmp_path: Path) -> None:
        """測試相同 Agent 連續執行能接續對話"""
        # Setup
        session_manager = SessionManager(sessions_dir=str(tmp_path / ".cafe" / "sessions"))
        agent_manager = AgentManager(session_manager=session_manager)

        # Register agent
        agent_manager.register_agent(AgentConfig(name="PM_Agent", cli=AgentCLI.GEMINI))

        with patch("subprocess.Popen") as mock_popen, \
             patch("sys.platform", "win32"):
            # First execution
            mock_process_1 = MagicMock()
            mock_process_1.stdout.readline.side_effect = [
                '{"type":"init","session_id":"continuous-session-789","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"First response"}\n',
                '{"response": "First response"}\n',
                '',
            ]
            mock_process_1.stderr.read.return_value = ""
            mock_process_1.wait.return_value = 0
            mock_popen.return_value = mock_process_1

            response1, _, _, _, _ = agent_manager.execute("PM_Agent", "First prompt")
            assert response1 == "First response"

            # Verify session_id was saved
            executor = agent_manager.get_agent("PM_Agent")
            assert executor.config.session_id == "continuous-session-789"

            # Second execution should use the same session
            mock_process_2 = MagicMock()
            mock_process_2.stdout.readline.side_effect = [
                '{"type":"init","session_id":"continuous-session-789","model":"auto"}\n',
                '{"type":"message","role":"assistant","content":"You said: First prompt"}\n',
                '{"response": "You said: First prompt"}\n',
                '',
            ]
            mock_process_2.stderr.read.return_value = ""
            mock_process_2.wait.return_value = 0
            mock_popen.return_value = mock_process_2

            response2, _, _, _, _ = agent_manager.execute("PM_Agent", "What did I say?")
            assert "First prompt" in response2 or "You said" in response2

            # Verify second call included --resume parameter
            second_call_args = mock_popen.call_args_list[1][0][0]
            assert "--resume" in second_call_args
            resume_index = second_call_args.index("--resume")
            assert second_call_args[resume_index + 1] == "continuous-session-789"
