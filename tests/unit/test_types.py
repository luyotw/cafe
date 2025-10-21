"""Tests for core type definitions."""

import pytest
from pydantic import ValidationError

from aaf.core.types import (
    AgentConfig,
    AgentCLI,
    PermissionAction,
    PermissionRequest,
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
        assert config.tool == AgentCLI.CLAUDE
        assert config.session_id is None
        assert config.allowed_tools == []

    def test_create_agent_config_full(self) -> None:
        """測試提供所有欄位時可以成功建立 AgentConfig"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="session-123",
            allowed_tools=["Bash(git:*)", "Read(*)"],
        )
        assert config.name == "David"
        assert config.tool == AgentCLI.CLAUDE
        assert config.session_id == "session-123"
        assert len(config.allowed_tools) == 2
        assert "Bash(git:*)" in config.allowed_tools

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
        assert config.requirements_file is None
        assert config.sessions_dir == ".aaf/sessions"

    def test_create_session_config_local_mode(self) -> None:
        """測試可以成功建立 Local 工作流程的 SessionConfig"""
        config = SessionConfig(
            workflow_mode=WorkflowMode.LOCAL,
            requirements_file="requirements.md",
        )
        assert config.workflow_mode == WorkflowMode.LOCAL
        assert config.requirements_file == "requirements.md"
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
