"""測試 phase 在遇到空回應或無 status code 時的日誌記錄功能。

此測試檔案確保：
1. 當 agent 回應為空（包括只有空白字元如 "\n"）時，系統能正確識別並記錄
2. 當 agent 回應缺少 status code 時，系統會嘗試呼叫 agent 分析並記錄整個流程
3. 所有相關資訊都會寫入 execution_error_{num}.log 以便除錯
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentConfig, AgentCLI, PhaseStatus, WorkflowMode
from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def temp_dir(tmp_path):
    """建立臨時目錄結構"""
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    history_dir = spec_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    
    # 建立初始 spec 檔案
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# 初始需求\n\n測試需求", encoding="utf-8")
    
    return tmp_path


@pytest.fixture
def mock_git_ops(temp_dir):
    """Mock GitOperations"""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager"""
    manager = MagicMock(spec=AgentManager)
    
    # Mock agent config
    agent_config = AgentConfig(
        name="Roger",
        cli=AgentCLI.COPILOT,
        session_id="test-session"
    )
    
    # Mock agent executor
    mock_executor = MagicMock()
    mock_executor.config = agent_config
    manager.get_agent.return_value = mock_executor
    manager.get_agent_config.return_value = agent_config
    
    return manager


@pytest.fixture
def mock_permission_handler():
    """Mock PermissionHandler"""
    return MagicMock(spec=PermissionHandler)


class TestEmptyResponseLogging:
    """測試空回應的日誌記錄"""
    
    def test_empty_response_with_only_newline(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：當 agent 回應只有 newline 時，應該識別為空回應並記錄日誌"""
        monkeypatch.chdir(temp_dir)
        
        # Mock agent execute to return only newline
        mock_agent_manager.execute.return_value = (
            "\n",  # response - only newline
            MagicMock(),  # token_usage
            [],  # permission_denials
            ["-p", "test prompt"],  # cli_command_args
            None,  # streaming_log
        )
        
        # Create phase
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        # Execute phase
        result = phase.execute()
        
        # 驗證結果
        assert result.status == PhaseStatus.FAILED
        assert "no response" in result.message.lower()
        
        # 驗證 iteration history 有記錄空回應
        history_file = temp_dir / ".cafe/issues/test-issue/spec/history/iteration_001.json"
        assert history_file.exists()
        
        with open(history_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        
        assert history_data["response"] == "\n"
        assert history_data["status_code"] == "CAFE_NO_RESPONSE"
    
    def test_empty_response_with_only_spaces(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：當 agent 回應只有空白字元時，應該識別為空回應"""
        monkeypatch.chdir(temp_dir)
        
        # Mock agent execute to return only spaces
        mock_agent_manager.execute.return_value = (
            "   \n  \t\n",  # response - only whitespace
            MagicMock(),  # token_usage
            [],  # permission_denials
            ["-p", "test prompt"],  # cli_command_args
            None,  # streaming_log
        )
        
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        result = phase.execute()
        
        assert result.status == PhaseStatus.FAILED
        assert "no response" in result.message.lower()


class TestMissingStatusCodeLogging:
    """測試缺少 status code 時的日誌記錄"""
    
    def test_missing_status_code_logs_analysis_process(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：當 agent 回應缺少 status code 時，應該記錄分析流程到日誌"""
        monkeypatch.chdir(temp_dir)
        
        # First call: response without status code
        # Second call: analysis call that returns status code
        mock_agent_manager.execute.side_effect = [
            (
                "這是一個沒有 status code 的回應",  # response without status code
                MagicMock(),  # token_usage
                [],  # permission_denials
                ["-p", "test prompt"],  # cli_command_args
                None,  # streaming_log
            ),
            (
                "CAFE_NEED_CLARIFICATION",  # analysis response with status code
                MagicMock(),  # token_usage
                [],  # permission_denials
                ["-p", "analysis prompt"],  # cli_command_args
                None,  # streaming_log
            ),
        ]
        
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        # Mock _get_status_analysis_prompt to return a prompt
        with patch.object(phase, '_get_status_analysis_prompt') as mock_prompt:
            mock_prompt.return_value = "請分析狀態"
            
            result = phase.execute()
        
        # 驗證 execute 被呼叫兩次（第一次正常執行，第二次分析）
        assert mock_agent_manager.execute.call_count == 2
        
        # 驗證 history 有記錄分析後的 status code
        history_file = temp_dir / ".cafe/issues/test-issue/spec/history/iteration_001.json"
        assert history_file.exists()
        
        with open(history_file, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        
        # 應該記錄原始回應（沒有 status code 的）
        assert history_data["response"] == "這是一個沒有 status code 的回應"
        # 應該記錄分析後的 status code
        assert history_data["status_code"] == "CAFE_NEED_CLARIFICATION"
        # 應該有標記說明 status code 是通過分析得到的
        assert "status_code_analyzed" in history_data
        assert history_data["status_code_analyzed"] is True
    
    def test_missing_status_code_logs_to_error_log(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：當回應缺少 status code 時，應該寫入 execution_error_{num}.log"""
        monkeypatch.chdir(temp_dir)
        
        # Response without status code, and analysis also fails
        mock_agent_manager.execute.side_effect = [
            (
                "沒有 status code 的回應",
                MagicMock(),
                [],
                ["-p", "test prompt"],
                None,
            ),
            (
                "分析結果也沒有 status code",  # Analysis also returns no status code
                MagicMock(),
                [],
                ["-p", "analysis prompt"],
                None,
            ),
        ]
        
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        with patch.object(phase, '_get_status_analysis_prompt') as mock_prompt:
            mock_prompt.return_value = "請分析狀態"
            
            result = phase.execute()
        
        # 驗證應該有 error log
        error_log = temp_dir / ".cafe/issues/test-issue/spec/history/execution_error_001.log"
        assert error_log.exists()
        
        error_content = error_log.read_text(encoding="utf-8")
        
        # 驗證 log 內容包含關鍵資訊
        assert "Timestamp:" in error_content
        assert "Missing status code" in error_content or "no status code" in error_content.lower()
        assert "Original response:" in error_content
        assert "沒有 status code 的回應" in error_content
        assert "Analysis attempted: True" in error_content
        assert "Analysis response:" in error_content
        assert "分析結果也沒有 status code" in error_content
    
    def test_multiple_status_codes_logs_to_error_log(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：當回應包含多個 status code 時，應該嘗試分析並寫入 execution_error_{num}.log"""
        monkeypatch.chdir(temp_dir)
        
        # First call: response with multiple status codes (use valid codes for spec phase)
        # Second call: analysis call that returns a single status code
        mock_agent_manager.execute.side_effect = [
            (
                "CAFE_READY_FOR_REVIEW\n這是內容\nCAFE_NEED_CLARIFICATION",  # Multiple status codes
                MagicMock(),
                [],
                ["-p", "test prompt"],
                None,
            ),
            (
                "CAFE_NEED_CLARIFICATION",  # Analysis returns single status code
                MagicMock(),
                [],
                ["-p", "analysis prompt"],
                None,
            ),
        ]
        
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        # Mock _get_status_analysis_prompt to return a prompt
        with patch.object(phase, '_get_status_analysis_prompt') as mock_prompt:
            mock_prompt.return_value = "請分析狀態"
            
            result = phase.execute()
        
        # 驗證 execute 被呼叫兩次（第一次正常執行，第二次分析）
        assert mock_agent_manager.execute.call_count == 2
        
        # 驗證應該有 error log
        error_log = temp_dir / ".cafe/issues/test-issue/spec/history/execution_error_001.log"
        assert error_log.exists()
        
        error_content = error_log.read_text(encoding="utf-8")
        
        # 驗證 log 內容包含關鍵資訊
        assert "Multiple status codes" in error_content
        assert "CAFE_READY_FOR_REVIEW" in error_content
        assert "CAFE_NEED_CLARIFICATION" in error_content
        assert "Analysis attempted: True" in error_content


class TestErrorLogFormat:
    """測試 error log 的格式和內容"""
    
    def test_error_log_includes_all_required_fields(
        self,
        temp_dir,
        mock_git_ops,
        mock_agent_manager,
        mock_permission_handler,
        monkeypatch,
    ):
        """測試：error log 應該包含所有必要的除錯資訊"""
        monkeypatch.chdir(temp_dir)
        
        # Response without status code
        mock_agent_manager.execute.side_effect = [
            (
                "測試回應內容",
                MagicMock(),
                [],
                ["-p", "prompt content", "--allow-tool", "write"],
                None,
            ),
            (
                "分析回應",
                MagicMock(),
                [],
                ["-p", "analysis"],
                None,
            ),
        ]
        
        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            user_input="測試輸入",
        )
        
        with patch.object(phase, '_get_status_analysis_prompt') as mock_prompt:
            mock_prompt.return_value = "請分析"
            
            result = phase.execute()
        
        error_log = temp_dir / ".cafe/issues/test-issue/spec/history/execution_error_001.log"
        assert error_log.exists()
        
        error_content = error_log.read_text(encoding="utf-8")
        
        # 必要欄位檢查
        required_fields = [
            "Timestamp:",
            "Issue:",
            "Iteration:",
            "Phase:",
            "Error type:",
            "Original response:",
            "Valid status codes:",
            "Analysis attempted:",
            "CLI command args:",
        ]
        
        for field in required_fields:
            assert field in error_content, f"Missing required field: {field}"
