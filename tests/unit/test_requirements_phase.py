"""Tests for RequirementsPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from aaf.phases.requirements_phase import RequirementsPhase
from aaf.agents.manager import AgentManager
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestRequirementsPhaseBasics:
    """Test basic RequirementsPhase functionality."""

    def test_init_requirements_phase(self) -> None:
        """測試初始化 RequirementsPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.requirements_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestLocalWorkflow:
    """Test local workflow requirements clarification."""

    def test_execute_local_workflow_single_iteration(self, tmp_path: Path) -> None:
        """測試執行 local workflow 單次迭代"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Initial requirements\n")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_backup_original_requirements(self, tmp_path: Path) -> None:
        """測試備份原始需求檔案"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Original requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        backup_file = Path(f"{requirements_file}.backup")
        assert backup_file.exists()
        assert backup_file.read_text() == "Original requirements"

    def test_multiple_iterations_until_confirmed(self, tmp_path: Path) -> None:
        """測試多次迭代直到確認"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Initial requirements")

        agent_manager = MagicMock(spec=AgentManager)
        # First two iterations ask questions, third confirms
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請問：需求問題 1",
            "NEED_CLARIFICATION\n請問：需求問題 2",
            "CONFIRMED\n需求已清楚。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 3


class TestGitHubWorkflow:
    """Test GitHub workflow requirements clarification."""

    def test_execute_github_workflow(self) -> None:
        """測試執行 GitHub workflow"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        # Should use gh issue view in prompt
        call_args = agent_manager.execute.call_args
        prompt = call_args[0][1]
        assert "gh issue view 123" in prompt

    def test_github_workflow_uses_issue_id(self) -> None:
        """測試 GitHub workflow 使用 issue ID"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        assert "456" in call_args[1]


class TestPromptGeneration:
    """Test prompt generation for different iterations."""

    def test_first_iteration_prompt(self, tmp_path: Path) -> None:
        """測試第一次迭代的 prompt"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "requirements.md" in prompt
        assert "第 1 輪" in prompt

    def test_subsequent_iteration_includes_history(self, tmp_path: Path) -> None:
        """測試後續迭代包含歷史記錄"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n問題 1",
            "CONFIRMED\n需求已清楚。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Check second call includes iteration info
        second_call = agent_manager.execute.call_args_list[1][0]
        prompt = second_call[1]
        assert "第 2 輪" in prompt


class TestAgentSelection:
    """Test PM agent selection."""

    def test_uses_pm_agent(self, tmp_path: Path) -> None:
        """測試使用 PM agent (Roger)"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n需求已清楚。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
        )

        phase.execute()

        # Check that Roger was used
        call_args = agent_manager.execute.call_args[0]
        assert call_args[0] == "Roger"


class TestErrorHandling:
    """Test error handling."""

    def test_missing_requirements_file_fails(self) -> None:
        """測試缺少需求檔案時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="/nonexistent/requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_github_mode_without_issue_id_fails(self) -> None:
        """測試 GitHub mode 沒有 issue_id 時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "issue_id" in result.message.lower()

    def test_agent_execution_error_fails_phase(self, tmp_path: Path) -> None:
        """測試 agent 執行錯誤時 phase 失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message


class TestConversationalRequirementsGeneration:
    """Test conversational requirements generation workflow."""

    def test_generate_requirements_from_scratch_local(self, tmp_path: Path) -> None:
        """測試從無到有以對話方式生成需求文件（Local mode）"""
        requirements_file = tmp_path / "requirements.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        # Simulate conversation: ask questions -> user responds -> generate document
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請問這個功能的目的是什麼？",
            "NEED_CLARIFICATION\n預期的使用場景有哪些？",
            "CONFIRMED\n需求文件已生成",
        ]
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        result = phase.execute()
        
        # Should complete after conversation
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["iterations"] == 3
        
        # Should create the requirements file
        assert requirements_file.exists()

    def test_generate_requirements_saves_to_file(self, tmp_path: Path) -> None:
        """測試生成的需求文件正確儲存"""
        requirements_file = tmp_path / "new_requirements.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n# 需求文件\n\n功能描述..."
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        result = phase.execute()
        
        assert result.status == PhaseStatus.COMPLETED
        # File should be created with content
        assert requirements_file.exists()
        content = requirements_file.read_text()
        assert "需求文件" in content or len(content) > 0

    def test_generate_requirements_github_creates_issue(self) -> None:
        """測試 GitHub mode 創建新 issue"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請問功能目的？",
            "CONFIRMED\n需求已完整",
        ]
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id=None,  # No existing issue - should create new one
        )
        
        with patch('aaf.phases.requirements_phase.create_github_issue') as mock_create:
            mock_create.return_value = "456"  # New issue ID
            
            result = phase.execute()
            
            assert result.status == PhaseStatus.COMPLETED
            # Should create a new issue
            mock_create.assert_called_once()
            assert result.data.get("issue_id") == "456"

    def test_prompt_includes_non_technical_emphasis(self, tmp_path: Path) -> None:
        """測試 prompt 包含不涉及技術細節的強調"""
        requirements_file = tmp_path / "requirements.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n完成"
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        phase.execute()
        
        # Check that prompt emphasizes non-technical approach
        call_args = agent_manager.execute.call_args[0]
        prompt = call_args[1]
        assert "不可涉及技術細節" in prompt or "不要提及實作方式" in prompt
        assert "對話方式" in prompt

    def test_no_existing_file_starts_conversation(self, tmp_path: Path) -> None:
        """測試沒有現有文件時，從對話開始"""
        requirements_file = tmp_path / "nonexistent.md"
        
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "NEED_CLARIFICATION\n請描述功能需求",
            "CONFIRMED\n需求已完整",
        ]
        
        permission_handler = MagicMock(spec=PermissionHandler)
        
        phase = RequirementsPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        result = phase.execute()
        
        # Should not fail when file doesn't exist
        assert result.status == PhaseStatus.COMPLETED
        # Should create the file
        assert requirements_file.exists()
