"""Integration tests for 'cafe plan --no-interactive' command.

使用 MockAgentExecutor 測試完整 plan command flow, 不呼叫真實 LLM API.
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
from cafe.phases.plan_phase import PlanPhase


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
def temp_plan_dir(tmp_path, monkeypatch):
    """創建臨時 plan 目錄結構"""
    monkeypatch.chdir(tmp_path)
    # 創建完整目錄結構: {tmp_path}/.cafe/issues/test-issue/plan/
    plan_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "plan"
    plan_dir.mkdir(parents=True)

    # 創建 spec 目錄and spec_001.md（plan 需要 spec 已存在）
    spec_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格.")

    return plan_dir


@pytest.fixture
def temp_plan_with_template(tmp_path, monkeypatch):
    """創建包含 template 臨時環境"""
    monkeypatch.chdir(tmp_path)
    # 創建 plan 目錄
    plan_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "plan"
    plan_dir.mkdir(parents=True)

    # 創建 spec
    spec_dir = tmp_path / ".cafe" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格.")
    
    # 創建 template 目錄and預設 template
    template_dir = tmp_path / ".cafe" / "templates" / "plan"
    template_dir.mkdir(parents=True)
    default_template = template_dir / "default.md"
    default_template.write_text("""# 實作計畫

## 概要
{summary}

## 技術方案
{technical_approach}

## 開發指南
{development_guide}
""")
    
    return plan_dir, default_template


class TestPlanCommandNonInteractiveFirstRound:
    """測試 plan --no-interactive Round 1（需要 --template）"""

    def test_first_round_without_template_uses_auto_mode(
        self, mock_env, temp_plan_dir
    , mock_git_ops):
        """測試Round 1沒有提供 --template 時預設使用 auto 模式"""
        # Arrange
        spec_file = str(temp_plan_dir.parent / "spec" / "spec_001.md")

        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )

        permission_handler = PermissionHandler()

        # Act - Round 1沒有 plan.md, 且沒有 template (預設使用 auto 模式)
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=None,  # 沒有提供 template，使用預設 auto 模式
            user_input="這是開發指南內容",
        )

        result = phase.execute()

        # Assert - Round 1預設使用 auto 模式，應該成功
        assert result.status == PhaseStatus.COMPLETED
        assert phase.template_mode == "auto"

    def test_first_round_with_template_success(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試Round 1提供 template 並返回 CONFIRMED 成功"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 實作計畫\n\n這是測試計畫內容."
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - Round 1提供 template and user_input (dev guide)
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )
        
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert Path(plan_file).exists()
        
        # 驗證 agent 被呼叫
        executor = agent_manager.get_agent("David")
        assert executor.call_count == 1

    def test_first_round_need_modification_should_fail_in_non_interactive(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試Round 1返回 NEED_MODIFICATION 在 non-interactive 模式應該失敗"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_NEED_CLARIFICATION\n\n請確認技術選型是否正確？"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        result = phase.execute()

        # Assert - After behavior change, NEED_CLARIFICATION returns COMPLETED immediately (no automatic continuation)
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_CLARIFICATION"


class TestPlanCommandNonInteractiveSubsequentRounds:
    """測試 plan --no-interactive Round 2及之後（不需要 --template）"""

    def test_subsequent_round_without_template_success(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試Round 2及之後不需要 template, 直接使用現有 plan_001.md"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")

        # 先創建 plan.md（模擬Round 1已完成, 包含開發指南）
        Path(plan_file).write_text("## 開發指南\n\n原始開發指南\n\n## 實作計畫\n\n初版計畫內容")

        # 創建 history 檔案（模擬Round 1已完成）
        history_dir = plan_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        import json
        history_file = history_dir / "iteration_001.json"
        history_file.write_text(json.dumps({
            "iteration": 1,
            "user_input": "## 開發指南\n\n原始開發指南",
            "response": "CAFE_READY_FOR_REVIEW\n\n## 實作計畫\n\n初版計畫內容",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 實作計畫\n\n更新後計畫內容."
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - Round 2不提供 template（因為 plan.md 已存在）
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=None,  # 不提供 template
            user_input="confirm",  # agent 返回 READY_FOR_REVIEW 後自動 confirm
        )
        
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        # 注意：mock agent 不會實際執行 Write 工具, 所以 plan.md 不會被更新
        # 只驗證 phase 成功完成即可

    def test_subsequent_round_with_template_ignored(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試Round 2提供 template 會被忽略（使用現有 plan_001.md）"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")

        # 先創建 plan.md（包含開發指南）
        Path(plan_file).write_text("## 開發指南\n\n原始開發指南\n\n## 實作計畫\n\n初版計畫內容")

        # 創建 history 檔案（模擬Round 1已完成）
        history_dir = plan_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        import json
        history_file = history_dir / "iteration_001.json"
        history_file.write_text(json.dumps({
            "iteration": 1,
            "user_input": "## 開發指南\n\n原始開發指南",
            "response": "CAFE_READY_FOR_REVIEW\n\n## 實作計畫\n\n初版計畫內容",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 實作計畫\n\n更新後計畫內容."
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - 提供 template, 但應該被忽略
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),  # 提供但會被忽略
            user_input="confirm",  # agent 返回 READY_FOR_REVIEW 後自動 confirm
        )
        
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        # 注意：mock agent 不會實際執行 Write 工具, 所以 plan.md 不會被更新
        # 只驗證 phase 成功完成即可


class TestPlanCommandNonInteractiveFiles:
    """測試檔案操作相關功能"""

    def test_plan_file_created_at_correct_path(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試 plan_001.md 在正確路徑創建"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()

        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        phase.execute()

        # Assert
        assert Path(plan_file).exists()
        assert Path(plan_file).is_file()

    def test_history_created(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試 history 目錄and檔案被創建"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        history_dir = plan_dir / "history"
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        phase.execute()

        # Assert
        assert history_dir.exists()
        assert history_dir.is_dir()
        
        # 至少有一個 iteration 檔案
        iteration_files = list(history_dir.glob("iteration_*.json"))
        assert len(iteration_files) >= 1


class TestPlanCommandNonInteractiveErrorHandling:
    """測試錯誤處理"""

    def test_spec_file_not_exists_should_fail(
        self, mock_env, temp_plan_with_template
    , mock_git_ops):
        """測試 spec_001.md 不存在應該失敗"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "nonexistent.md")  # 不存在檔案
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "spec" in result.message.lower() or "not found" in result.message.lower()


class TestPlanCommandNonInteractiveAgentTracking:
    """測試 mock agent 追蹤功能"""

    def test_agent_receives_spec_file(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試 agent 收到正確 spec file 路徑"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        phase.execute()

        # Assert - 驗證 agent 被呼叫, 且 prompt 包含 spec_001.md
        executor = agent_manager.get_agent("David")
        assert executor.call_count >= 1
        assert "spec_001.md" in executor.last_prompt

    def test_agent_called_once_for_confirmed(
        self, mock_env, temp_plan_with_template, monkeypatch
    , mock_git_ops):
        """測試 CONFIRMED 狀態下 agent 只被呼叫一次"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan_001.md")
        spec_file = str(plan_dir.parent / "spec" / "spec_001.md")
        
        monkeypatch.setenv(
            "CAFE_MOCK_RESPONSE",
            "CAFE_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            git_ops=mock_git_ops,
            agent_manager=agent_manager,
            permission_handler=permission_handler,

            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
            user_input="這是開發指南內容",
        )

        # Act
        phase.execute()

        # Assert
        executor = agent_manager.get_agent("David")
        assert executor.call_count == 1
