"""測試 Phase 在 non-interactive 模式下的行為

TDD: 先寫測試定義預期行為，然後修復實作
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import AgentConfig, AgentCLI, PhaseStatus, WorkflowMode
from cafe.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test"
    return git_ops


@pytest.fixture
def mock_env(monkeypatch):
    """啟用 mock agent mode"""
    monkeypatch.setenv("CAFE_MOCK_AGENTS", "true")


@pytest.fixture
def setup_plan_phase(tmp_path, monkeypatch):
    """設置 plan phase 測試環境"""
    # 切換到 tmp_path 目錄
    monkeypatch.chdir(tmp_path)

    # 創建必要的目錄結構
    plan_dir = tmp_path / ".cafe" / "issues" / "test" / "plan"
    plan_dir.mkdir(parents=True)

    spec_dir = tmp_path / ".cafe" / "issues" / "test" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試需求\n\n這是測試內容。\n\n## 開發指南\n\n開發指南內容。")

    # 創建已存在的 plan.md（包含開發指南）
    plan_file = plan_dir / "plan.md"
    plan_file.write_text("## 開發指南\n\n開發指南內容。\n\n## 實作計畫\n\nTODO")

    # 創建 template
    template_dir = tmp_path / ".cafe" / "templates" / "plan"
    template_dir.mkdir(parents=True)
    template_file = template_dir / "default.md"
    template_file.write_text("# 實作計畫\n\n## 概要\n{summary}")

    return {
        "spec_file": str(spec_file),
        "template_file": str(template_file),
        "plan_dir": plan_dir,
    }


class TestNonInteractiveModeWithNeedClarification:
    """測試 non-interactive 模式收到 NEED_CLARIFICATION 的行為"""
    
    def test_should_fail_immediately_when_need_clarification_without_user_input(
        self, mock_env, setup_plan_phase, mock_git_ops, monkeypatch
    ):
        """
        測試：non-interactive 模式收到 NEED_CLARIFICATION 但沒有 user_input
        預期：立即失敗（因為無法與用戶交互）
        """
        # Arrange
        setup = setup_plan_phase

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEED_CLARIFICATION\n\n請確認技術選型是否正確？"
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        permission_handler = PermissionHandler()

        # Act - non-interactive 且沒有提供 user_input
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=setup["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,  # Non-interactive
            template_path=setup["template_file"],
            user_input="",  # 沒有提供 user_input
        )

        result = phase.execute()

        # Assert - 應該立即失敗
        assert result.status == PhaseStatus.FAILED
        assert "need_clarification" in result.message.lower() and \
               "without user input" in result.message.lower()
    
    def test_should_fail_after_first_need_clarification(
        self, mock_env, setup_plan_phase, mock_git_ops, monkeypatch
    ):
        """
        測試：non-interactive 模式收到 NEED_CLARIFICATION 且有提供 user_input
        預期：使用 user_input 繼續一輪，然後如果再次收到 NEED_CLARIFICATION 應失敗（因為 user_input 已被消耗）
        """
        # Arrange
        setup = setup_plan_phase

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEED_CLARIFICATION\n\n請確認技術選型是否正確？"
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        permission_handler = PermissionHandler()

        # Act - 第一輪執行：提供 user_input
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=setup["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=setup["template_file"],
            user_input="使用 TypeScript",  # 提供 user_input
        )

        result = phase.execute()

        # Assert - 第一輪應該使用 user_input 繼續並完成一輪迭代
        # 但因為第二輪又收到 NEED_CLARIFICATION，且 user_input 已被消耗，應該失敗
        assert result.status == PhaseStatus.FAILED
        assert "need_clarification" in result.message.lower() and \
               "without user input" in result.message.lower()


class TestNonInteractiveModeCompleteImmediately:
    """測試 non-interactive 模式收到 complete codes 立即完成"""
    
    def test_should_complete_immediately_when_ready_for_review(
        self, mock_env, setup_plan_phase, mock_git_ops, monkeypatch
    ):
        """
        測試：non-interactive 模式收到 READY_FOR_REVIEW
        預期：立即完成（不等待用戶確認）
        """
        # Arrange
        setup = setup_plan_phase

        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 實作計畫\n\n完整的計畫內容。"
        )

        agent_manager = AgentManager()
        agent_manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        permission_handler = PermissionHandler()

        # Act
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=setup["spec_file"],
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=setup["template_file"],
        )

        result = phase.execute()

        # Assert - 應該立即完成
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["status_code"] == "CAFE_READY_FOR_REVIEW"
