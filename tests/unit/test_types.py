"""Tests for core type definitions."""

import os
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cafe.core.types import (
    AgentConfig,
    AgentCLI,
    PermissionAction,
    PermissionDenial,
    PermissionRequest,
    PhaseProgress,
    PhaseResult,
    PhaseStatus,
    SessionConfig,
    WorkflowMode,
)


class TestEnums:
    """Test enum types."""

    def test_workflow_mode_values(self) -> None:
        """測試 WorkflowMode enum 的值是否正確"""
        assert WorkflowMode.GITHUB == "github"
        assert WorkflowMode.LOCAL == "local"

    def test_agent_tool_values(self) -> None:
        """測試 AgentCLI enum 的值是否正確"""
        assert AgentCLI.CLAUDE == "claude"
        assert AgentCLI.GEMINI == "gemini"
        assert AgentCLI.CURSOR == "cursor-agent"

    def test_phase_status_values(self) -> None:
        """測試 PhaseStatus enum 的值是否正確"""
        assert PhaseStatus.PENDING == "pending"
        assert PhaseStatus.IN_PROGRESS == "in_progress"
        assert PhaseStatus.COMPLETED == "completed"
        assert PhaseStatus.FAILED == "failed"
        assert PhaseStatus.SKIPPED == "skipped"

    def test_permission_action_values(self) -> None:
        """測試 PermissionAction enum 的值是否正確 (y/t/s)"""
        assert PermissionAction.AUTHORIZE == "y"
        assert PermissionAction.DIALOG == "t"
        assert PermissionAction.SKIP == "s"


class TestAgentConfig:
    """Test AgentConfig model."""

    def test_create_agent_config_minimal(self) -> None:
        """測試只提供必要欄位時可以成功建立 AgentConfig"""
        config = AgentConfig(name="Roger", cli=AgentCLI.CLAUDE)
        assert config.name == "Roger"
        assert config.cli == AgentCLI.CLAUDE
        assert config.session_id is None

    def test_create_agent_config_full(self) -> None:
        """測試提供所有欄位時可以成功建立 AgentConfig"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="session-123",
        )
        assert config.name == "David"
        assert config.cli == AgentCLI.CLAUDE
        assert config.session_id == "session-123"

    def test_agent_config_validation_missing_name(self) -> None:
        """測試缺少 name 欄位時會拋出 ValidationError"""
        with pytest.raises(ValidationError):
            AgentConfig(cli=AgentCLI.CLAUDE)  # type: ignore

    def test_agent_config_validation_missing_tool(self) -> None:
        """測試缺少 tool 欄位時會拋出 ValidationError"""
        with pytest.raises(ValidationError):
            AgentConfig(name="Roger")  # type: ignore


class TestPhaseResult:
    """Test PhaseResult model."""

    def test_create_phase_result_minimal(self) -> None:
        """測試只提供 status 時可以成功建立 PhaseResult，其他欄位使用預設值"""
        result = PhaseResult(status=PhaseStatus.COMPLETED)
        assert result.status == PhaseStatus.COMPLETED
        assert result.message == ""
        assert result.data == {}

    def test_create_phase_result_with_message(self) -> None:
        """測試可以成功建立帶有錯誤訊息的 PhaseResult"""
        result = PhaseResult(
            status=PhaseStatus.FAILED, message="Something went wrong"
        )
        assert result.status == PhaseStatus.FAILED
        assert result.message == "Something went wrong"

    def test_create_phase_result_with_data(self) -> None:
        """測試可以成功建立帶有額外資料的 PhaseResult"""
        result = PhaseResult(
            status=PhaseStatus.COMPLETED,
            message="Success",
            data={"commits": 3, "files_changed": 5},
        )
        assert result.status == PhaseStatus.COMPLETED
        assert result.data["commits"] == 3
        assert result.data["files_changed"] == 5


class TestPermissionRequest:
    """Test PermissionRequest model."""

    def test_create_permission_request(self) -> None:
        """測試可以成功建立權限請求，包含工具名稱和輸入參數"""
        request = PermissionRequest(
            tool_name="Bash", tool_input={"command": "git status"}
        )
        assert request.tool_name == "Bash"
        assert request.tool_input["command"] == "git status"

    def test_permission_request_validation(self) -> None:
        """測試缺少 tool_input 時會拋出 ValidationError"""
        with pytest.raises(ValidationError):
            PermissionRequest(tool_name="Bash")  # type: ignore


class TestSessionConfig:
    """Test SessionConfig model."""

    def test_create_session_config_github_mode(self) -> None:
        """測試可以成功建立 GitHub 工作流程的 SessionConfig"""
        config = SessionConfig(workflow_mode=WorkflowMode.GITHUB, issue_id="123")
        assert config.workflow_mode == WorkflowMode.GITHUB
        assert config.issue_id == "123"
        assert config.spec_file is None
        assert config.sessions_dir == ".cafe/sessions"

    def test_create_session_config_local_mode(self) -> None:
        """測試可以成功建立 Local 工作流程的 SessionConfig"""
        config = SessionConfig(
            workflow_mode=WorkflowMode.LOCAL,
            spec_file="requirements.md",
        )
        assert config.workflow_mode == WorkflowMode.LOCAL
        assert config.spec_file == "requirements.md"
        assert config.issue_id is None

    def test_create_session_config_custom_dirs(self) -> None:
        """測試可以自訂 sessions 和 issues 目錄路徑"""
        config = SessionConfig(
            workflow_mode=WorkflowMode.GITHUB,
            issue_id="456",
            sessions_dir="/tmp/sessions",
            issue_dir="/tmp/issues",
        )
        assert config.sessions_dir == "/tmp/sessions"
        assert config.issue_dir == "/tmp/issues"


class TestPhaseProgress:
    """Test PhaseProgress model."""

    def test_create_phase_progress_minimal(self) -> None:
        """測試只提供必要欄位時可以成功建立 PhaseProgress"""
        now = datetime.now()
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            timestamp=now,
        )
        assert progress.phase == "spec"
        assert progress.status == PhaseStatus.COMPLETED
        assert progress.timestamp == now
        assert progress.status_code is None
        assert progress.iteration is None
        assert progress.message == ""

    def test_create_phase_progress_full(self) -> None:
        """測試提供所有欄位時可以成功建立 PhaseProgress"""
        now = datetime.now()
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            status_code="CONFIRMED",
            timestamp=now,
            iteration=2,
            message="Requirements clarified successfully",
        )
        assert progress.phase == "spec"
        assert progress.status == PhaseStatus.COMPLETED
        assert progress.status_code == "CONFIRMED"
        assert progress.timestamp == now
        assert progress.iteration == 2
        assert progress.message == "Requirements clarified successfully"

    def test_phase_progress_to_dict(self) -> None:
        """測試可以將 PhaseProgress 轉換為 dict"""
        now = datetime.now()
        progress = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            status_code="CONFIRMED",
            timestamp=now,
            iteration=2,
            message="Success",
        )
        data = progress.to_dict()
        assert data["phase"] == "spec"
        assert data["status"] == "completed"
        assert data["status_code"] == "CONFIRMED"
        assert data["timestamp"] == now.isoformat()
        assert data["iteration"] == 2
        assert data["message"] == "Success"

    def test_phase_progress_from_dict(self) -> None:
        """測試可以從 dict 建立 PhaseProgress"""
        now = datetime.now()
        data = {
            "phase": "spec",
            "status": "completed",
            "status_code": "CONFIRMED",
            "timestamp": now.isoformat(),
            "iteration": 2,
            "message": "Success",
        }
        progress = PhaseProgress.from_dict(data)
        assert progress.phase == "spec"
        assert progress.status == PhaseStatus.COMPLETED
        assert progress.status_code == "CONFIRMED"
        assert progress.timestamp == now
        assert progress.iteration == 2
        assert progress.message == "Success"

    def test_phase_progress_roundtrip(self) -> None:
        """測試 to_dict 和 from_dict 的往返轉換"""
        now = datetime.now()
        original = PhaseProgress(
            phase="spec",
            status=PhaseStatus.COMPLETED,
            status_code="CONFIRMED",
            timestamp=now,
            iteration=3,
        )
        data = original.to_dict()
        restored = PhaseProgress.from_dict(data)
        assert restored.phase == original.phase
        assert restored.status == original.status
        assert restored.status_code == original.status_code
        assert restored.timestamp == original.timestamp
        assert restored.iteration == original.iteration


class TestPermissionDenial:
    """Test PermissionDenial model."""

    def test_to_allowed_tool_pattern_with_absolute_path(self, tmp_path: Path) -> None:
        """測試絕對路徑應轉換成相對於專案根目錄的路徑"""
        # 模擬專案根目錄
        project_root = tmp_path / "project"
        project_root.mkdir()

        # 切換到專案目錄
        original_cwd = os.getcwd()
        try:
            os.chdir(project_root)

            # 測試絕對路徑
            file_path = str(project_root / "views" / "admin" / "topics.php")
            denial = PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": file_path}
            )

            # 應該轉換成相對路徑並加上 / 前綴（git ignore 規則）
            pattern = denial.to_allowed_tool_pattern()
            assert pattern == "edit(/views/admin/topics.php)"
        finally:
            os.chdir(original_cwd)

    def test_to_allowed_tool_pattern_with_relative_path(self, tmp_path: Path) -> None:
        """測試相對路徑應該保持原樣並加上 / 前綴"""
        denial = PermissionDenial(
            tool_name="Write",
            tool_input={"file_path": "src/main.py"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "write(/src/main.py)"

    def test_to_allowed_tool_pattern_with_bash_command(self) -> None:
        """測試 bash 命令使用前兩個詞作為 pattern"""
        denial = PermissionDenial(
            tool_name="Bash",
            tool_input={"command": "git --no-pager diff HEAD"}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "bash(git --no-pager)"

    def test_to_allowed_tool_pattern_without_params(self) -> None:
        """測試沒有參數時只返回工具名"""
        denial = PermissionDenial(
            tool_name="Read",
            tool_input={}
        )

        pattern = denial.to_allowed_tool_pattern()
        assert pattern == "read"
