"""測試 iteration history 應該包含完整的 metadata。

Iteration history 應該記錄執行時的完整上下文：
- CLI tool (例如 "copilot", "claude")
- Session ID
- Allowed tools
- Denied tools (如果有)
- Prompt
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import json

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.types import WorkflowMode, TokenUsage, SpecRigor, AgentCLI, AgentConfig, PhaseStatus
from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def setup_agent_manager_mock_for_spec(agent_manager: MagicMock, cli: str = "copilot", session_id: str = "test-session") -> None:
    """Setup agent_manager.get_agent() mock with specified CLI and session ID."""
    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name="Roger",
        cli=AgentCLI[cli.upper()],
        session_id=session_id
    )
    agent_manager.get_agent.return_value = mock_agent


class TestSpecPhaseIterationHistoryMetadata:
    """測試 SpecPhase iteration history 包含完整 metadata。"""

    def test_iteration_history_includes_agent_metadata(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試 iteration history 包含 CLI、session ID、allowed tools 等資訊"""
        issue_name = "test-metadata"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager, cli="copilot", session_id="test-session-123")
        agent_manager.execute.return_value = ("CAFE_READY_FOR_REVIEW\n需求已清楚", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
            user_input="confirm",  # Provide confirmation for non-interactive mode
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Check iteration history file was created
        history_file = spec_file.parent / "history" / "iteration_001.json"
        assert history_file.exists()

        # Load and check metadata
        history_data = json.loads(history_file.read_text())

        # Should include CLI tool
        assert history_data["cli"] == "copilot", "Should record CLI tool used"

        # Should include session ID
        assert history_data["session_id"] == "test-session-123", "Should record session ID"

        # Should include allowed tools (check that read and write are present)
        allowed_tools = history_data["allowed_tools"]
        assert "read" in allowed_tools, "Should include read in allowed tools"
        assert any("write" in tool for tool in allowed_tools), "Should include write in allowed tools"

        # Should include denied tools (empty in this case)
        assert history_data["denied_tools"] is None or history_data["denied_tools"] == [], "Should have denied_tools field"

        # Should include prompt
        assert history_data["prompt"] is not None, "Should record the prompt"
        assert len(history_data["prompt"]) > 0, "Prompt should not be empty"

        # Should include status code
        assert history_data["status_code"] == "CAFE_READY_FOR_REVIEW", "Should record status"

    def test_multiple_iterations_preserve_metadata(
        self, tmp_path: Path, mock_git_ops: MagicMock, monkeypatch
    ) -> None:
        """測試迭代時記錄完整 metadata（沒有 while loop，只執行一次）"""
        issue_name = "test-multi-metadata"
        mock_git_ops.get_current_branch.return_value = issue_name
        monkeypatch.chdir(tmp_path)

        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager, cli="claude", session_id="session-456")
        # 第一次執行得到 NEED_CLARIFICATION
        agent_manager.execute.return_value = ("CAFE_NEED_CLARIFICATION\n請補充資訊", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch('builtins.input', return_value='c'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        # 沒有 while loop，只有第一次迭代，NEED_CLARIFICATION 返回 COMPLETED
        assert result.status == PhaseStatus.COMPLETED

        # Check first iteration history file
        history_dir = spec_file.parent / "history"
        history_file_1 = history_dir / "iteration_001.json"

        assert history_file_1.exists()

        # Check first iteration metadata
        history_1 = json.loads(history_file_1.read_text())
        assert history_1["cli"] == "claude"
        assert history_1["session_id"] == "session-456"
        assert "read" in history_1["allowed_tools"]
        assert any("write" in tool for tool in history_1["allowed_tools"])
        assert history_1["status_code"] == "CAFE_NEED_CLARIFICATION"
