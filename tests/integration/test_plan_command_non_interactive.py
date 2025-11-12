"""Integration tests for 'aaf plan --no-interactive' command.

使用 MockAgentExecutor 測試完整的 plan command flow，不呼叫真實 LLM API。
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.agents.manager import AgentManager
from aaf.agents.mock_executor import MockAgentExecutor
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from aaf.phases.plan_phase import PlanPhase


@pytest.fixture
def mock_env(monkeypatch):
    """啟用 mock agent mode"""
    monkeypatch.setenv("AAF_MOCK_AGENTS", "true")


@pytest.fixture
def temp_plan_dir(tmp_path):
    """創建臨時 plan 目錄結構"""
    # 創建完整的目錄結構: {tmp_path}/.aaf/issues/test-issue/plan/
    plan_dir = tmp_path / ".aaf" / "issues" / "test-issue" / "plan"
    plan_dir.mkdir(parents=True)
    
    # 創建 spec 目錄和 spec.md（plan 需要 spec 已存在）
    spec_dir = tmp_path / ".aaf" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格。")
    
    return plan_dir


@pytest.fixture
def temp_plan_with_template(tmp_path):
    """創建包含 template 的臨時環境"""
    # 創建 plan 目錄
    plan_dir = tmp_path / ".aaf" / "issues" / "test-issue" / "plan"
    plan_dir.mkdir(parents=True)
    
    # 創建 spec
    spec_dir = tmp_path / ".aaf" / "issues" / "test-issue" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec.md"
    spec_file.write_text("# 測試功能需求\n\n這是一個測試需求規格。")
    
    # 創建 template 目錄和預設 template
    template_dir = tmp_path / ".aaf" / "templates" / "plan"
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
    """測試 plan --no-interactive 第一輪（需要 --template）"""

    def test_first_round_without_template_should_fail(
        self, mock_env, temp_plan_dir
    ):
        """測試第一輪沒有提供 --template 應該失敗"""
        # Arrange
        spec_file = str(temp_plan_dir.parent / "spec" / "spec.md")
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - 第一輪沒有 plan.md，且沒有 template
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=None,  # 沒有提供 template
        )
        
        result = phase.execute()
        
        # Assert - 第一輪必須提供 template
        assert result.status == PhaseStatus.FAILED
        assert "template" in result.message.lower() or "required" in result.message.lower()

    def test_first_round_with_template_success(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試第一輪提供 template 並返回 CONFIRMED 成功"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 實作計畫\n\n這是測試計畫內容。"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - 第一輪提供 template
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
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
    ):
        """測試第一輪返回 NEED_MODIFICATION 在 non-interactive 模式應該失敗"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_NEED_CLARIFICATION\n\n請確認技術選型是否正確？"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        result = phase.execute()
        
        # Assert - non-interactive 無法處理 NEED_MODIFICATION
        assert result.status == PhaseStatus.FAILED
        assert "exceeded maximum iterations" in result.message


class TestPlanCommandNonInteractiveSubsequentRounds:
    """測試 plan --no-interactive 第二輪及之後（不需要 --template）"""

    def test_subsequent_round_without_template_success(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試第二輪及之後不需要 template，直接使用現有 plan.md"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        # 先創建 plan.md（模擬第一輪已完成）
        Path(plan_file).write_text("# 實作計畫\n\n初版計畫內容")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 實作計畫\n\n更新後的計畫內容。"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - 第二輪不提供 template（因為 plan.md 已存在）
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=None,  # 不提供 template
        )
        
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.COMPLETED
        # 驗證 plan.md 被更新
        updated_content = Path(plan_file).read_text()
        assert "更新後的計畫內容" in updated_content

    def test_subsequent_round_with_template_ignored(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試第二輪提供 template 會被忽略（使用現有 plan.md）"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        # 先創建 plan.md
        Path(plan_file).write_text("# 實作計畫\n\n初版計畫內容")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 實作計畫\n\n更新後的計畫內容。"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        # Act - 提供 template，但應該被忽略
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),  # 提供但會被忽略
        )
        
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.COMPLETED
        # 驗證 template 中的 placeholder 不在結果中（證明沒有用 template）
        updated_content = Path(plan_file).read_text()
        assert "{summary}" not in updated_content
        assert "{technical_approach}" not in updated_content


class TestPlanCommandNonInteractiveFiles:
    """測試檔案操作相關功能"""

    def test_plan_file_created_at_correct_path(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試 plan.md 在正確路徑創建"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        phase.execute()
        
        # Assert
        assert Path(plan_file).exists()
        assert Path(plan_file).is_file()

    def test_history_created(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試 history 目錄和檔案被創建"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        history_dir = plan_dir / "history"
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
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

    def test_rejected_should_fail(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試 agent 返回 REJECTED 應該失敗"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_REJECTED\n\n這個實作方案不可行。"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "rejected" in result.message.lower()

    def test_spec_file_not_exists_should_fail(
        self, mock_env, temp_plan_with_template
    ):
        """測試 spec.md 不存在應該失敗"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "nonexistent.md")  # 不存在的檔案
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        result = phase.execute()
        
        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "spec" in result.message.lower() or "not found" in result.message.lower()


class TestPlanCommandNonInteractiveAgentTracking:
    """測試 mock agent 的追蹤功能"""

    def test_agent_receives_spec_file(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試 agent 收到正確的 spec file 路徑"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        phase.execute()
        
        # Assert - 驗證 agent 被呼叫，且 prompt 包含 spec.md
        executor = agent_manager.get_agent("David")
        assert executor.call_count >= 1
        assert "spec.md" in executor.last_prompt

    def test_agent_called_once_for_confirmed(
        self, mock_env, temp_plan_with_template, monkeypatch
    ):
        """測試 CONFIRMED 狀態下 agent 只被呼叫一次"""
        # Arrange
        plan_dir, default_template = temp_plan_with_template
        plan_file = str(plan_dir / "plan.md")
        spec_file = str(plan_dir.parent / "spec" / "spec.md")
        
        monkeypatch.setenv(
            "AAF_MOCK_RESPONSE",
            "AAF_READY_FOR_REVIEW\n\n# 測試計畫"
        )
        
        agent_manager = AgentManager()
        agent_manager.register_agent(
            AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        )
        
        permission_handler = PermissionHandler()
        
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            
            spec_file=spec_file,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            template_path=str(default_template),
        )
        
        # Act
        phase.execute()
        
        # Assert
        executor = agent_manager.get_agent("David")
        assert executor.call_count == 1
