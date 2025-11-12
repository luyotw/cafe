"""Example integration test using MockAgentExecutor.

這個檔案示範如何使用 MockAgentExecutor 來測試完整的 CLI flow。
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from aaf.agents.mock_executor import MockAgentExecutor
from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode
from aaf.phases.spec_phase import SpecPhase


class TestSpecPhaseWithMock:
    """示範如何使用 MockAgentExecutor 測試 SpecPhase"""

    def test_spec_phase_with_confirmed_response(self, tmp_path):
        """測試 spec phase 在 agent 返回 CONFIRMED 時的行為"""
        # Arrange - 準備 mock agent
        mock_executor = MockAgentExecutor(
            config=AgentConfig(name="Roger", cli=AgentCLI.CLAUDE),
            response="CONFIRMED\n\n這是一個測試需求文件。"
        )
        
        # 創建 agent manager 並注入 mock executor
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent.return_value = mock_executor
        agent_manager.get_or_create_session.return_value = "mock-session-id"
        
        # 準備其他依賴
        permission_handler = PermissionHandler()
        spec_file = str(tmp_path / "spec.md")
        
        # 創建 phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,  # non-interactive mode
            user_input="我想要一個測試功能",
        )
        
        # Act - 執行 phase (這裡會呼叫 mock executor)
        # result = phase.execute()
        
        # Assert - 驗證 mock executor 被呼叫
        # assert mock_executor.call_count > 0
        # assert "測試功能" in mock_executor.last_prompt
        
        # Note: 完整測試需要處理更多細節（如檔案系統、session 管理等）
        # 這裡只是展示如何 mock agent executor
        pass

    def test_spec_phase_with_clarification_needed(self, tmp_path):
        """測試 spec phase 在 agent 需要澄清時的行為"""
        # Arrange - mock agent 返回 NEED_CLARIFICATION
        mock_executor = MockAgentExecutor(
            config=AgentConfig(name="Roger", cli=AgentCLI.CLAUDE),
            response="NEED_CLARIFICATION\n\n請問這個功能的使用者是誰？"
        )
        
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.get_agent.return_value = mock_executor
        
        # ... 後續測試邏輯
        pass


class TestMockAgentExecutorPatterns:
    """展示 MockAgentExecutor 的各種使用模式"""

    def test_sequential_responses(self):
        """展示如何模擬多輪對話"""
        # Arrange
        mock_executor = MockAgentExecutor(
            config=AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE)
        )
        
        # Act & Assert - 第一輪：需要澄清
        mock_executor.set_response("NEED_CLARIFICATION\n請問目標使用者是誰？")
        response1, _ = mock_executor.execute("實作登入功能")
        assert "NEED_CLARIFICATION" in response1
        assert mock_executor.call_count == 1
        
        # 第二輪：確認
        mock_executor.set_response("CONFIRMED\n\n# 需求規格\n登入功能...")
        response2, _ = mock_executor.execute("目標使用者是一般用戶")
        assert "CONFIRMED" in response2
        assert mock_executor.call_count == 2

    def test_track_prompts_for_verification(self):
        """展示如何追蹤並驗證傳給 agent 的 prompt"""
        # Arrange
        mock_executor = MockAgentExecutor(
            config=AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE),
            response="CONFIRMED"
        )
        
        # Act
        mock_executor.execute("請根據以下需求生成規格...")
        
        # Assert - 驗證 prompt 內容
        assert mock_executor.last_prompt is not None
        assert "需求" in mock_executor.last_prompt
        assert "規格" in mock_executor.last_prompt

    def test_verify_tool_usage(self):
        """展示如何驗證 agent 使用的 tools"""
        # Arrange
        mock_executor = MockAgentExecutor(
            config=AgentConfig(name="TestAgent", cli=AgentCLI.CLAUDE),
            response="執行完成"
        )
        
        # Act
        mock_executor.execute("執行開發任務", tools=["bash", "read", "write"])
        
        # Assert - 驗證正確的 tools 被傳遞
        assert mock_executor.last_tools == ["bash", "read", "write"]
