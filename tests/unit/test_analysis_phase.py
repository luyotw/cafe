"""Tests for AnalysisPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.analysis_phase import AnalysisPhase
from aaf.agents.manager import AgentManager
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestAnalysisPhaseBasics:
    """Test basic AnalysisPhase functionality."""

    def test_init_analysis_phase(self) -> None:
        """測試初始化 AnalysisPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file="requirements.md",
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="123",
        )

        assert phase.workflow_mode == WorkflowMode.GITHUB
        assert phase.issue_id == "123"


class TestLocalWorkflow:
    """Test local workflow implementation analysis."""

    def test_execute_local_workflow_with_dev_guide(self, tmp_path: Path) -> None:
        """測試執行 local workflow 有開發指南"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("""# Requirements

## 開發指南
Development guide here

## 實作分析
Implementation analysis here
""")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "實作分析狀態：已確認"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.called

    def test_missing_dev_guide_fails(self, tmp_path: Path) -> None:
        """測試缺少開發指南時失敗"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\nNo dev guide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "開發指南" in result.message

    def test_multiple_iterations_until_confirmed(self, tmp_path: Path) -> None:
        """測試多次迭代直到確認"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "分析中...",
            "實作分析狀態：已確認",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED
        assert agent_manager.execute.call_count == 2


class TestGitHubWorkflow:
    """Test GitHub workflow implementation analysis."""

    def test_execute_github_workflow(self) -> None:
        """測試執行 GitHub workflow"""
        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "實作分析狀態：已確認"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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
        agent_manager.execute.return_value = "實作分析狀態：已確認"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "實作分析狀態：已確認"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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
        """測試後續迭代包含迭代資訊"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = [
            "分析中",
            "實作分析狀態：已確認",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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


class TestConfirmationDetection:
    """Test implementation analysis confirmation detection."""

    def test_detect_confirmation_status(self, tmp_path: Path) -> None:
        """測試偵測確認狀態"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        # Test various confirmation formats
        assert phase.is_confirmed("實作分析狀態：已確認")
        assert phase.is_confirmed("> 實作分析狀態：已確認")
        assert phase.is_confirmed("some text\n實作分析狀態：已確認\n")

    def test_not_confirmed_without_keyword(self, tmp_path: Path) -> None:
        """測試沒有關鍵字時不算確認"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert not phase.is_confirmed("分析進行中")
        assert not phase.is_confirmed("還需要更多資訊")


class TestAgentSelection:
    """Test developer agent selection."""

    def test_uses_dev_agent(self, tmp_path: Path) -> None:
        """測試使用 Dev agent (David)"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "實作分析狀態：已確認"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
            dev_agent="David",
        )

        phase.execute()

        # Check that David was used
        call_args = agent_manager.execute.call_args[0]
        assert call_args[0] == "David"


class TestErrorHandling:
    """Test error handling."""

    def test_missing_requirements_file_fails(self) -> None:
        """測試缺少需求檔案時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
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

        phase = AnalysisPhase(
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
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.side_effect = Exception("Agent error")

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = AnalysisPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            requirements_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "Agent error" in result.message


class TestDevGuideDetection:
    """Test development guide detection."""

    def test_detect_dev_guide_various_formats(self, tmp_path: Path) -> None:
        """測試偵測各種格式的開發指南"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Test different heading formats
        formats = [
            "## 開發指南",
            "## Development Guide",
            "### 開發指南",
            "## development guide",
        ]

        for fmt in formats:
            requirements_file = tmp_path / f"req_{formats.index(fmt)}.md"
            requirements_file.write_text(f"# Requirements\n\n{fmt}\nContent")

            phase = AnalysisPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                requirements_file=str(requirements_file),
                workflow_mode=WorkflowMode.LOCAL,
            )

            assert phase.has_dev_guide(), f"Should detect: {fmt}"
