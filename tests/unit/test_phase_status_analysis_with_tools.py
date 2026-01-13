"""測試 Phase  status code 分析功能（帶 allowed_tools）."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from cafe.core.phase import Phase
from cafe.core.types import PhaseStatus
from cafe.core.status_codes import PhaseStatusCode
from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.git import GitOperations


class TestPhaseStatusAnalysisWithTools:
    """測試 Phase  status code 分析功能在傳遞 allowed_tools 時行為."""

    def test_analyze_with_allowed_tools_passed_to_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試分析時會傳遞 allowed_tools 給 agent.
        
        情境：當 agent 回應沒有 status code 時, 系統呼叫分析
        驗證：分析呼叫時有傳遞 allowed_tools（包含 read 等工具）
        """
        monkeypatch.chdir(tmp_path)
        
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True)
        
        # 建立 spec 檔案
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test spec", encoding="utf-8")
        
        # Mock agent manager
        mock_agent_manager = MagicMock(spec=AgentManager)
        
        # 只設定分析執行返回值（測試只呼叫分析, 不呼叫原始執行）
        mock_agent_manager.execute.return_value = (
            "CAFE_READY_FOR_REVIEW", None, [], {}, [], None
        )
        
        # Mock other dependencies
        mock_permission_handler = MagicMock(spec=PermissionHandler)
        mock_git_ops = MagicMock(spec=GitOperations)
        
        # 創建一個簡單 Phase 子類來測試
        class TestPhase(Phase):
            def __init__(self, git_ops):
                super().__init__(interactive=True, git_ops=git_ops)
                self.agent_manager = mock_agent_manager
                self.permission_handler = mock_permission_handler
                self.phase_dir = spec_dir
                self.history_dir = history_dir
                self.iteration = 1
                self.spec_file = str(spec_file)
            
            def execute(self):
                return None
            
            def _get_status_analysis_prompt(self) -> str:
                return f"請讀取 {self.spec_file} 並分析狀態"
        
        phase = TestPhase(git_ops=mock_git_ops)
        
        # 測試分析功能
        valid_codes = [PhaseStatusCode.READY_FOR_REVIEW, PhaseStatusCode.NEED_CLARIFICATION]
        response, status_code = phase._analyze_missing_status_code_with_logging(
            agent_name="TestAgent",
            valid_status_codes=valid_codes,
            original_response="No status code",
        )
        
        # 驗證
        assert response == "CAFE_READY_FOR_REVIEW"
        assert status_code == PhaseStatusCode.READY_FOR_REVIEW
        
        # 驗證 agent.execute 被呼叫了一次（分析呼叫）
        assert mock_agent_manager.execute.call_count == 1
        
        # 驗證傳遞了 allowed_tools
        call_kwargs = mock_agent_manager.execute.call_args[1]
        assert "allowed_tools" in call_kwargs
        assert "read" in call_kwargs["allowed_tools"]
        assert "grep" in call_kwargs["allowed_tools"]
        assert "glob" in call_kwargs["allowed_tools"]
        assert "ls" in call_kwargs["allowed_tools"]

    def test_analyze_with_absolute_path_in_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試分析 prompt 使用絕對路徑.
        
        情境：在 worktree 環境中, 分析 prompt 應該使用絕對路徑
        驗證：分析 prompt 中檔案路徑是絕對路徑
        """
        monkeypatch.chdir(tmp_path)
        
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True)
        
        # 建立 spec 檔案（使用相對路徑）
        spec_file = Path(".cafe/issues/test-issue/spec/spec_001.md")
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Test spec", encoding="utf-8")
        
        # Mock agent manager
        mock_agent_manager = MagicMock(spec=AgentManager)
        mock_agent_manager.execute.return_value = (
            "CAFE_READY_FOR_REVIEW", None, [], {}, [], None
        )
        
        # Mock other dependencies
        mock_permission_handler = MagicMock(spec=PermissionHandler)
        mock_git_ops = MagicMock(spec=GitOperations)
        
        # 創建測試 Phase
        class TestPhase(Phase):
            def __init__(self, git_ops):
                super().__init__(interactive=True, git_ops=git_ops)
                self.agent_manager = mock_agent_manager
                self.permission_handler = mock_permission_handler
                self.phase_dir = spec_dir
                self.history_dir = history_dir
                self.iteration = 1
                # 使用相對路徑設定 spec_file
                self.spec_file = str(spec_file)
            
            def execute(self):
                return None
            
            def _get_status_analysis_prompt(self) -> str:
                # 使用絕對路徑
                from pathlib import Path
                spec_path = Path(self.spec_file)
                if not spec_path.is_absolute():
                    spec_path = spec_path.resolve()
                return f"請讀取 {spec_path} 並分析狀態"
        
        phase = TestPhase(git_ops=mock_git_ops)
        
        # 取得分析 prompt
        prompt = phase._get_status_analysis_prompt()
        
        # 驗證：prompt 中應該包含絕對路徑
        assert str(tmp_path) in prompt  # 絕對路徑應包含 tmp_path
        assert ".cafe/issues/test-issue/spec/spec_001.md" in prompt
        
        # 測試分析功能
        valid_codes = [PhaseStatusCode.READY_FOR_REVIEW]
        response, status_code = phase._analyze_missing_status_code_with_logging(
            agent_name="TestAgent",
            valid_status_codes=valid_codes,
            original_response="No status code",
        )
        
        # 驗證分析成功
        assert status_code == PhaseStatusCode.READY_FOR_REVIEW
        
        # 驗證傳遞給 agent  prompt 包含絕對路徑
        call_args = mock_agent_manager.execute.call_args[0]
        prompt_arg = call_args[1]  # 第二個參數是 prompt
        assert str(tmp_path) in prompt_arg

    def test_analyze_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試分析失敗時返回 None.
        
        情境：agent 執行拋出異常
        驗證：返回 (None, None) 並不會中斷程式
        """
        monkeypatch.chdir(tmp_path)
        
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True)
        
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test", encoding="utf-8")
        
        # Mock agent manager 拋出異常
        mock_agent_manager = MagicMock(spec=AgentManager)
        mock_agent_manager.execute.side_effect = Exception("Agent execution failed")
        
        mock_permission_handler = MagicMock(spec=PermissionHandler)
        mock_git_ops = MagicMock(spec=GitOperations)
        
        class TestPhase(Phase):
            def __init__(self, git_ops):
                super().__init__(interactive=True, git_ops=git_ops)
                self.agent_manager = mock_agent_manager
                self.permission_handler = mock_permission_handler
                self.phase_dir = spec_dir
                self.history_dir = history_dir
                self.iteration = 1
                self.spec_file = str(spec_file)
            
            def execute(self):
                return None
            
            def _get_status_analysis_prompt(self) -> str:
                return "分析狀態"
        
        phase = TestPhase(git_ops=mock_git_ops)
        
        # 測試分析（應該 catch 異常並返回 None）
        valid_codes = [PhaseStatusCode.READY_FOR_REVIEW]
        response, status_code = phase._analyze_missing_status_code_with_logging(
            agent_name="TestAgent",
            valid_status_codes=valid_codes,
            original_response="No status code",
        )
        
        # 驗證
        assert response is None
        assert status_code is None

    def test_analyze_extracts_status_code_from_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試分析能正確從回應中提取 status code.
        
        情境：agent 回應包含有效 status code
        驗證：正確提取並返回 status code
        """
        monkeypatch.chdir(tmp_path)
        
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True)
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True)
        
        spec_file = spec_dir / "spec_001.md"
        spec_file.write_text("# Test", encoding="utf-8")
        
        # Mock agent manager
        mock_agent_manager = MagicMock(spec=AgentManager)
        mock_agent_manager.execute.return_value = (
            "CAFE_NEED_CLARIFICATION\n\nThis spec needs more details.",
            None, [], {}, [], None
        )
        
        mock_permission_handler = MagicMock(spec=PermissionHandler)
        mock_git_ops = MagicMock(spec=GitOperations)
        
        class TestPhase(Phase):
            def __init__(self, git_ops):
                super().__init__(interactive=True, git_ops=git_ops)
                self.agent_manager = mock_agent_manager
                self.permission_handler = mock_permission_handler
                self.phase_dir = spec_dir
                self.history_dir = history_dir
                self.iteration = 1
                self.spec_file = str(spec_file)
            
            def execute(self):
                return None
            
            def _get_status_analysis_prompt(self) -> str:
                return "分析狀態"
        
        phase = TestPhase(git_ops=mock_git_ops)
        
        # 測試分析
        valid_codes = [
            PhaseStatusCode.READY_FOR_REVIEW,
            PhaseStatusCode.NEED_CLARIFICATION,
        ]
        response, status_code = phase._analyze_missing_status_code_with_logging(
            agent_name="TestAgent",
            valid_status_codes=valid_codes,
            original_response="No status code",
        )
        
        # 驗證
        assert "CAFE_NEED_CLARIFICATION" in response
        assert status_code == PhaseStatusCode.NEED_CLARIFICATION
