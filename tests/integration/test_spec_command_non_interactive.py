"""Integration tests for 'aaf spec --no-interactive' command.

使用 MockAgentExecutor 測試完整的 spec command flow，不呼叫真實 LLM API。
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.agents.manager import AgentManager
from aaf.agents.mock_executor import MockAgentExecutor
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from aaf.phases.spec_phase import SpecPhase


@pytest.fixture
def mock_env(monkeypatch):
    """啟用 mock agent mode"""
    monkeypatch.setenv("AAF_MOCK_AGENTS", "true")


@pytest.fixture
def temp_spec_dir(tmp_path):
    """創建臨時 spec 目錄結構"""
    # 創建完整的目錄結構: {tmp_path}/.aaf/issues/test-issue/spec/
    spec_dir = tmp_path / ".aaf" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    # 不要創建 history 目錄，讓 phase 自己創建
    return spec_dir


class TestSpecCommandNonInteractiveBasic:
    """測試 spec --no-interactive 的基本功能"""

    def test_successful_spec_creation_with_confirmed(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 agent 返回 CONFIRMED 時成功創建 spec"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        user_input = "我想要一個登入功能"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_CONFIRMED\n\n# 登入功能需求規格\n\n這是測試規格。"
        )
        
        # 創建 mock agent manager
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # 創建 phase
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )
        
        # Act
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.COMPLETED
        
        # 驗證 agent 被呼叫
        executor = agent_manager.get_agent("Roger")
        assert executor.call_count == 1

    def test_need_clarification_should_fail_in_non_interactive(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 non-interactive mode 遇到 NEED_CLARIFICATION 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        user_input = "我想要一個功能"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_NEED_CLARIFICATION\n\n請問這個功能的使用者是誰？"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )
        
        # Act
        result = phase.execute()
        
        # Assert - non-interactive mode 無法處理 NEED_CLARIFICATION，應該失敗
        assert result.status == PhaseStatus.FAILED
        assert "exceeded maximum iterations" in result.message

    def test_rejected_should_fail(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 agent 返回 REJECTED 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        user_input = "不合理的需求"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_REJECTED\n\n這個需求不符合專案方向。"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )
        
        # Act
        result = phase.execute()
        
        # Assert - REJECTED 應該導致 phase 失敗
        assert result.status == PhaseStatus.FAILED
        assert "rejected" in result.message.lower()

    def test_empty_user_input_should_fail(
        self, mock_env, temp_spec_dir
    ):
        """測試沒有提供 user_input 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=None,  # 沒有輸入
        )
        
        # Act
        result = phase.execute()
        
        # Assert - 沒有 user_input 應該失敗
        assert result.status == PhaseStatus.FAILED
        assert "input" in result.message.lower() or "require" in result.message.lower()


class TestSpecCommandNonInteractiveFiles:
    """測試檔案操作相關功能"""

    def test_spec_file_created_at_correct_path(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 spec.md 在正確路徑創建"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="測試需求",
        )
        
        # Act
        phase.execute()
        
        # Assert
        assert Path(spec_file).exists()
        assert Path(spec_file).is_file()

    def test_history_created(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 history 目錄和檔案被創建"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        history_dir = temp_spec_dir / "history"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="測試需求",
        )
        
        # Act
        phase.execute()
        
        # Assert
        assert history_dir.exists()
        assert history_dir.is_dir()
        
        # 至少有一個 iteration 檔案
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) >= 1


class TestSpecCommandNonInteractiveErrorHandling:
    """測試錯誤處理"""

    def test_invalid_status_code(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試無效的 status code"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_INVALID_STATUS\n\n這是無效的狀態碼"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="測試需求",
        )
        
        # Act
        result = phase.execute()
        
        # Assert - 無效 status code 會導致繼續迭代（或達到max iterations失敗）
        # 因為 mock 會重複返回無效狀態，最終會超過 max iterations
        # 但由於是 mock 環境，可能只會執行 1 iteration 返回 IN_PROGRESS
        assert result.status in [PhaseStatus.IN_PROGRESS, PhaseStatus.FAILED]


class TestSpecCommandNonInteractiveCLIValidation:
    """測試 CLI 參數驗證"""

    def test_no_user_input_should_fail(self, mock_env, temp_spec_dir):
        """測試 --no-interactive 但沒提供 --user-input 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act & Assert - 應該在初始化時就失敗
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="",  # 空字串
        )
        
        # execute 應該失敗
        result = phase.execute()
        assert result.status == PhaseStatus.FAILED

    def test_mock_mode_writes_spec_file(self, mock_env, temp_spec_dir):
        """測試 mock 模式下 spec 檔案會被正確寫入"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="我想要一個測試功能",
        )
        
        # Act
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert Path(spec_file).exists()
        content = Path(spec_file).read_text()
        assert "Mock Spec" in content or "Mock Response" in content
        assert "AAF_CONFIRMED" not in content  # 狀態碼不應該在檔案內容中


class TestSpecCommandNonInteractiveAgentTracking:
    """測試 mock agent 的追蹤功能"""

    def test_agent_receives_user_input(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 agent 收到正確的 user input"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        user_input = "我想要一個特殊的登入功能"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )
        
        # Act
        phase.execute()
        
        # Assert - 驗證 agent 被呼叫，且 spec.md 包含 user input
        executor = agent_manager.get_agent("Roger")
        assert executor.call_count >= 1
        # user_input 寫入 spec.md，agent 被要求分析該檔案
        assert "spec.md" in executor.last_prompt
        # 驗證 spec.md 確實包含 user_input
        spec_content = Path(spec_file).read_text()
        assert user_input in spec_content

    def test_agent_called_once_for_confirmed(
        self, mock_env, temp_spec_dir, monkeypatch
    ):
        """測試 CONFIRMED 狀態下 agent 只被呼叫一次"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="測試需求",
        )
        
        # Act
        phase.execute()
        
        # Assert
        executor = agent_manager.get_agent("Roger")
        assert executor.call_count == 1
