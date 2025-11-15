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
from cafe.core.permission import PermissionHandler
from cafe.core.types import WorkflowMode, TokenUsage, SpecRigor, AgentCLI, AgentConfig
from cafe.phases.spec_phase import SpecPhase


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

    def test_iteration_history_includes_agent_metadata(self, tmp_path: Path) -> None:
        """測試 iteration history 包含 CLI、session ID、allowed tools 等資訊"""
        issue_name = "test-metadata"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager, cli="copilot", session_id="test-session-123")
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n需求已清楚", TokenUsage(), [], None)
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            rigor=SpecRigor.MEDIUM,
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

        # Should include allowed tools
        assert history_data["allowed_tools"] == ["write", "read"], "Should record allowed tools"

        # Should include denied tools (empty in this case)
        assert history_data["denied_tools"] is None or history_data["denied_tools"] == [], "Should have denied_tools field"

        # Should include prompt
        assert history_data["prompt"] is not None, "Should record the prompt"
        assert len(history_data["prompt"]) > 0, "Prompt should not be empty"

        # Should include status code
        assert history_data["status_code"] == "CAFE_CONFIRMED", "Should record status"

    def test_multiple_iterations_preserve_metadata(self, tmp_path: Path) -> None:
        """測試多次迭代時每次都記錄完整 metadata"""
        issue_name = "test-multi-metadata"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mock_for_spec(agent_manager, cli="claude", session_id="session-456")
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_CLARIFICATION\n請補充資訊", TokenUsage()),
            ("CAFE_CONFIRMED\n需求已清楚", TokenUsage()),
        ]
        agent_manager.get_total_token_usage.return_value = TokenUsage()

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', return_value="補充資訊"):
            result = phase.execute()

        # Check both iteration history files
        history_dir = spec_file.parent / "history"
        history_file_1 = history_dir / "iteration_001.json"
        history_file_2 = history_dir / "iteration_002.json"

        assert history_file_1.exists()
        assert history_file_2.exists()

        # Check first iteration
        history_1 = json.loads(history_file_1.read_text())
        assert history_1["cli"] == "claude"
        assert history_1["session_id"] == "session-456"
        assert history_1["allowed_tools"] == ["write", "read"]
        assert history_1["status_code"] == "CAFE_NEED_CLARIFICATION"

        # Check second iteration
        history_2 = json.loads(history_file_2.read_text())
        assert history_2["cli"] == "claude"
        assert history_2["session_id"] == "session-456"
        assert history_2["allowed_tools"] == ["write", "read"]
        assert history_2["status_code"] == "CAFE_CONFIRMED"
