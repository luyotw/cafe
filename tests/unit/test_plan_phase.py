"""Tests for PlanPhase."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aaf.phases.plan_phase import PlanPhase
from aaf.agents.manager import AgentManager
from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode
from aaf.core.permission import PermissionHandler


class TestPlanPhaseBasics:
    """Test basic PlanPhase functionality."""

    def test_init_plan_phase(self) -> None:
        """測試初始化 PlanPhase"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        assert phase.agent_manager == agent_manager
        assert phase.permission_handler == permission_handler
        assert phase.spec_file == "requirements.md"
        assert phase.workflow_mode == WorkflowMode.LOCAL

    def test_init_with_github_mode(self) -> None:
        """測試使用 GitHub mode 初始化"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
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
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
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
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
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
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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
            "CONFIRMED\n實作分析已完成。",
        ]

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
            workflow_mode=WorkflowMode.LOCAL,
        )

        phase.execute()

        # Check second call includes iteration info
        second_call = agent_manager.execute.call_args_list[1][0]
        prompt = second_call[1]
        assert "第 2 輪" in prompt


class TestAgentSelection:
    """Test developer agent selection."""

    def test_uses_dev_agent(self, tmp_path: Path) -> None:
        """測試使用 Dev agent (David)"""
        requirements_file = tmp_path / "requirements.md"
        requirements_file.write_text("# Requirements\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        agent_manager.execute.return_value = "CONFIRMED\n實作分析已完成。"

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="/nonexistent/requirements.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "not found" in result.message.lower()

    def test_github_mode_without_issue_id_fails(self) -> None:
        """測試 GitHub mode 沒有 issue_id 時失敗"""
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
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

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(requirements_file),
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

            phase = PlanPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                spec_file=str(requirements_file),
                workflow_mode=WorkflowMode.LOCAL,
            )

            assert phase.has_dev_guide(), f"Should detect: {fmt}"


class TestPlanPhaseHistory:
    """Test history recording and loading functionality (TDD)."""

    def test_init_creates_history_dir_and_attributes(self, tmp_path: Path) -> None:
        """測試 __init__ 創建 history_dir 和 conversation_history 屬性"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        # Should have history_dir attribute
        assert hasattr(phase, 'history_dir')
        assert phase.history_dir == spec_file.parent.parent / "plan" / "history"

        # Should have conversation_history attribute
        assert hasattr(phase, 'conversation_history')
        assert isinstance(phase.conversation_history, list)
        assert len(phase.conversation_history) == 0

    def test_save_history_creates_json_file(self, tmp_path: Path) -> None:
        """測試 _save_history() 創建 JSON 檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 1
        phase._save_history(
            prompt="Test prompt",
            response="Test response",
            status_code=PhaseStatusCode.NEED_CLARIFICATION,
        )

        # Check history file was created
        history_file = phase.history_dir / "001.json"
        assert history_file.exists()

        # Check content
        import json
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["iteration"] == 1
        assert data["prompt"] == "Test prompt"
        assert data["response"] == "Test response"
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert "timestamp" in data

    def test_load_history_reads_existing_files(self, tmp_path: Path) -> None:
        """測試 _load_history() 讀取現有歷史檔案"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        # Create history files
        history_dir = spec_file.parent.parent / "plan" / "history"
        history_dir.mkdir(parents=True)

        import json
        history1 = {
            "iteration": 1,
            "timestamp": "2025-10-31T10:00:00",
            "prompt": "Prompt 1",
            "response": "Response 1 [STATUS:NEED_CLARIFICATION]",
            "status_code": "NEED_CLARIFICATION",
        }

        with open(history_dir / "001.json", 'w', encoding='utf-8') as f:
            json.dump(history1, f)

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        # _load_history() should be called in __init__
        assert len(phase.conversation_history) == 1
        assert phase.conversation_history[0]["iteration"] == 1
        assert phase.iteration == 1


class TestPlanPhaseProgressTracking:
    """Test progress tracking functionality (TDD)."""

    def test_save_progress_creates_status_json(self, tmp_path: Path) -> None:
        """測試 _save_progress() 創建 status.json"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        phase.iteration = 2
        phase._save_progress(PhaseStatusCode.NEED_CLARIFICATION)

        status_file = phase.history_dir.parent / "status.json"
        assert status_file.exists()

        import json
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert data["phase"] == "plan"
        assert data["status"] == "in_progress"
        assert data["status_code"] == "NEED_CLARIFICATION"
        assert data["iteration"] == 2

    def test_load_progress_returns_none_when_no_file(self, tmp_path: Path) -> None:
        """測試 _load_progress() 在沒有檔案時返回 None"""
        spec_file = tmp_path / ".aaf" / "issues" / "test-issue" / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Spec\n\n## 開發指南\nGuide")

        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
        )

        progress = phase._load_progress()
        assert progress is None
