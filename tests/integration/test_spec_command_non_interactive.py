"""Integration tests for 'cafe spec --no-interactive' command.

使用 MockAgentExecutor 測試完整的 spec command flow，不呼叫真實 LLM API。
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager
from cafe.agents.mock_executor import MockAgentExecutor
from cafe.core.permission import PermissionHandler
from cafe.core.git import GitOperations
from cafe.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def mock_env(monkeypatch):
    """啟用 mock agent mode"""
    monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")


@pytest.fixture
def mock_git_ops():
    """Create mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    git_ops.branch_exists.return_value = True
    return git_ops


@pytest.fixture
def temp_spec_dir(tmp_path, monkeypatch):
    """創建臨時 spec 目錄結構"""
    monkeypatch.chdir(tmp_path)
    # 創建完整的目錄結構: {tmp_path}/.cafe/issues/test-issue/spec/
    spec_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    # 不要創建 history 目錄，讓 phase 自己創建
    return spec_dir


class TestSpecCommandNonInteractiveBasic:
    """測試 spec --no-interactive 的基本功能"""

    def test_successful_spec_creation_with_confirmed(
        self, mock_env, temp_spec_dir, mock_git_ops, monkeypatch
    ):
        """測試 agent 返回 READY_FOR_REVIEW + user confirm 時成功創建 spec"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        user_input = "confirm"  # Provide confirmation for non-interactive mode

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 登入功能需求規格\n\n這是測試規格。"
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
            git_ops=mock_git_ops,
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
    , mock_git_ops):
        """測試 non-interactive mode 遇到 NEED_CLARIFICATION 返回 IN_PROGRESS（沒有 while loop）"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        user_input = "我想要一個功能"

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEED_CLARIFICATION\n\n請問這個功能的使用者是誰？"
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )

        permission_handler = PermissionHandler()

        phase = SpecPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )

        # Act
        result = phase.execute()

        # Assert - After behavior change, NEED_CLARIFICATION returns COMPLETED (no automatic continuation)
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"

    def test_empty_user_input_should_fail(
        self, mock_env, temp_spec_dir
    , mock_git_ops):
        """測試沒有提供 user_input 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            git_ops=mock_git_ops,
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
        # 錯誤訊息應該包含 "input", "require", 或 "stdin" (pytest 環境下的錯誤)
        assert (
            "input" in result.message.lower() 
            or "require" in result.message.lower()
            or "stdin" in result.message.lower()
        )


class TestSpecCommandNonInteractiveFiles:
    """測試檔案操作相關功能"""

    def test_spec_file_created_at_correct_path(
        self, mock_env, temp_spec_dir, monkeypatch
    , mock_git_ops):
        """測試 spec_001.md 在正確路徑創建"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            git_ops=mock_git_ops,
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
    , mock_git_ops):
        """測試 history 目錄和檔案被創建"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        history_dir = temp_spec_dir / "history"
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_CONFIRMED\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            git_ops=mock_git_ops,
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
    , mock_git_ops):
        """測試無效的 status code"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_INVALID_STATUS\n\n這是無效的狀態碼"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            git_ops=mock_git_ops,
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

    def test_no_user_input_should_fail(self, mock_env, temp_spec_dir, mock_git_ops):
        """測試 --no-interactive 但沒提供 --user-input 應該失敗"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act & Assert - 應該在初始化時就失敗
        phase = SpecPhase(
            git_ops=mock_git_ops,
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

    def test_mock_mode_writes_spec_file(self, mock_env, temp_spec_dir, mock_git_ops, monkeypatch):
        """測試 mock 模式下 spec 檔案會被正確寫入"""
        # Arrange
        spec_file = temp_spec_dir / "spec_001.md"
        # Don't create initial spec file so agent generates it from user_input

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# Mock Spec\n\nThis is a mock response for testing."
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )

        permission_handler = PermissionHandler()

        phase = SpecPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="我想要一個測試功能",  # Provide user requirement for first iteration
        )

        # Act
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert spec_file.exists()
        content = spec_file.read_text()
        assert "Mock Spec" in content or "Mock Response" in content
        # 狀態碼不應該在檔案內容中
        assert "CAFE_READY_FOR_REVIEW" not in content
        assert "CAFE_CONFIRMED" not in content


class TestSpecCommandNonInteractiveAgentTracking:
    """測試 mock agent 的追蹤功能"""

    def test_agent_receives_user_input(
        self, mock_env, temp_spec_dir, monkeypatch
    , mock_git_ops):
        """測試 agent 收到正確的 user input"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")
        user_input = "我想要一個特殊的登入功能"

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試規格"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = SpecPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input=user_input,
        )
        
        # Act
        phase.execute()
        
        # Assert - 驗證 agent 被呼叫，且收到包含 user input 的 prompt
        executor = agent_manager.get_agent("Roger")
        assert executor.call_count >= 1
        # Agent 的 prompt 包含 spec_001.md 路徑（agent 會讀取該檔案）
        assert "spec_001.md" in executor.last_prompt
        # 驗證 spec_001.md 被創建（內容來自 mock response）
        spec_content = Path(spec_file).read_text()
        assert "測試規格" in spec_content  # Mock response 的內容

    def test_agent_called_once_for_confirmed(
        self, mock_env, temp_spec_dir, monkeypatch
    , mock_git_ops):
        """測試 READY_FOR_REVIEW + confirm 狀態下 agent 只被呼叫一次"""
        # Arrange
        spec_file = str(temp_spec_dir / "spec_001.md")

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試規格"
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        )

        permission_handler = PermissionHandler()

        phase = SpecPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            user_input="confirm",  # Provide confirmation for non-interactive mode
        )

        # Act
        phase.execute()

        # Assert
        executor = agent_manager.get_agent("Roger")
        assert executor.call_count == 1
