"""測試 SpecPhase 所有 return 語句都包含 spec_file。

確保 CLI 可以正確顯示完整的檔案路徑。
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.types import PhaseStatus, PhaseResult, WorkflowMode, TokenUsage
from cafe.phases.spec_phase import SpecPhase, MAX_CLARIFICATION_ITERATIONS


@pytest.fixture
def mock_agent_manager():
    """Mock AgentManager"""
    manager = MagicMock()
    # Return proper TokenUsage object instead of empty dict
    manager.execute.return_value = (
        "CAFE_CONFIRMED\n\nRequirements confirmed",
        TokenUsage(),  # Use TokenUsage object instead of {}
        [],
        [],
        ""
    )

    # Mock get_agent to return a proper mock executor with config
    mock_executor = MagicMock()
    mock_config = MagicMock()
    mock_config.cli.value = "copilot"  # Use a string, not MagicMock
    mock_config.session_id = "test-session-123"  # Use a string, not MagicMock
    mock_executor.config = mock_config
    manager.get_agent.return_value = mock_executor

    # Mock get_total_token_usage to return proper TokenUsage object
    manager.get_total_token_usage.return_value = TokenUsage()

    return manager


@pytest.fixture
def mock_permission():
    """Mock PermissionHandler"""
    permission = MagicMock()
    permission.check.return_value = (True, [])
    return permission


@pytest.fixture
def mock_git():
    """Mock GitOperations"""
    git_ops = MagicMock()
    # Return "test-issue" to match the test directory structure
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


@pytest.fixture
def spec_phase_setup(tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
    """設置 SpecPhase 測試環境"""
    # 設置測試目錄
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(tmp_path)

    phase = SpecPhase(
        agent_manager=mock_agent_manager,
        permission_handler=mock_permission,
        git_ops=mock_git,
        workflow_mode=WorkflowMode.LOCAL,
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
        user_input="Test requirements",
    )

    # Patch the analysis method to prevent it from calling agent again with MagicMocks
    with patch.object(phase, '_analyze_missing_status_code_with_logging', return_value=("", None)):
        yield phase, spec_dir, mock_agent_manager


class TestSpecPhaseReturnData:
    """確保所有 PhaseResult 都包含 spec_file"""

    def test_exceeded_iterations_includes_spec_file(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """超過最大 iterations (999) 時應包含 spec_file"""
        # Setup: 創建超過 999 個歷史檔案
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        # 創建 999 個假檔案（模擬已達上限）
        for i in range(1, 1000):
            (spec_dir / f"spec_{i:03d}.md").touch()

        monkeypatch.chdir(tmp_path)

        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            issue_name="test-issue",
            user_input="Test requirements",
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.FAILED
        # The error message is in Chinese: "不可超過 999"
        assert "spec_file" in result.data
        # spec_file should be None since we exceeded iterations before setting it
        assert result.data["spec_file"] is None or result.data["spec_file"] == ""

    def test_exception_handler_includes_spec_file(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """Exception 處理應包含 spec_file"""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(tmp_path)

        # Mock agent 拋出 exception
        mock_agent_manager.execute.side_effect = RuntimeError("Agent execution failed")

        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            issue_name="test-issue",
            user_input="Test requirements",
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.FAILED
        assert "failed" in result.message.lower()
        # 關鍵斷言：應包含 spec_file（即使發生 exception）
        # 注意：在 exception 時，spec_file 可能尚未設定，所以可能是 None
        # 但 data 字典應該包含這個 key（即使值為 None）
        assert "spec_file" in result.data

    @pytest.mark.parametrize("status_code,expected_status", [
        ("CAFE_READY_FOR_REVIEW", PhaseStatus.COMPLETED),
        ("CAFE_NEED_CLARIFICATION", PhaseStatus.COMPLETED),  # NEED_CLARIFICATION returns COMPLETED (not IN_PROGRESS)
        ("CAFE_REJECTED", PhaseStatus.FAILED),
    ])
    def test_all_success_paths_include_spec_file(self, status_code, expected_status, spec_phase_setup):
        """所有成功路徑都應包含 spec_file"""
        phase, spec_dir, mock_agent_manager = spec_phase_setup

        # Mock agent 返回特定 status code
        mock_agent_manager.execute.return_value = (
            f"{status_code}\n\nTest response",
            TokenUsage(),  # Use TokenUsage object
            [],
            [],
            ""
        )

        # Execute
        result = phase.execute()

        # Assert (print result for debugging if test fails)
        if result.status != expected_status:
            print(f"\nUnexpected status: {result.status}")
            print(f"Message: {result.message}")
            print(f"Data: {result.data}")
        assert result.status == expected_status
        assert "spec_file" in result.data
        assert result.data["spec_file"] is not None
        # 驗證路徑格式正確（應該是 spec_001.md）
        assert "spec_001.md" in result.data["spec_file"]

    def test_keyboard_interrupt_includes_spec_file(self, tmp_path, mock_agent_manager, mock_permission, mock_git, monkeypatch):
        """KeyboardInterrupt 處理應包含 spec_file（已由 commit b602bab 修復）"""
        # Setup
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(tmp_path)

        # Mock agent 拋出 KeyboardInterrupt
        mock_agent_manager.execute.side_effect = KeyboardInterrupt()

        phase = SpecPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission,
            git_ops=mock_git,
            workflow_mode=WorkflowMode.LOCAL,
            pm_agent="Roger",
            interactive=False,
            issue_name="test-issue",
            user_input="Test requirements",
        )

        # Execute
        result = phase.execute()

        # Assert
        assert result.status == PhaseStatus.IN_PROGRESS
        assert "Paused" in result.message or "paused" in result.message
        # 這個已經由 b602bab 修復，應該通過
        assert "spec_file" in result.data
        assert result.data["spec_file"] is not None
