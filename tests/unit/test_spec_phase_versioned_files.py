"""測試 SpecPhase 使用版本化檔案。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from cafe.phases.spec_phase import SpecPhase
from cafe.core.types import WorkflowMode, PhaseStatus, TokenUsage, AgentConfig, AgentCLI
from cafe.core.status_codes import PhaseStatusCode


@pytest.fixture
def mock_agent_manager():
    """Mock agent manager."""
    manager = MagicMock()
    # Use real TokenUsage object instead of MagicMock
    token_usage = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        total_cost_usd=0.01,
    )
    # Mock execute to return a CONFIRMED response
    manager.execute.return_value = (
        "CAFE_CONFIRMED\n需求已明確",
        token_usage,
        [],  # permission_denials
        None,  # cli_command_args
        [],  # streaming_log
    )
    # Mock get_total_token_usage
    manager.get_total_token_usage.return_value = token_usage

    # Mock get_agent
    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name="Roger",
        cli=AgentCLI.COPILOT,
        session_id="test-session"
    )
    manager.get_agent.return_value = mock_agent

    return manager


@pytest.fixture
def mock_permission_handler():
    """Mock permission handler."""
    return MagicMock()


@pytest.fixture
def mock_git_ops():
    """Mock git operations."""
    ops = MagicMock()
    ops.get_current_branch.return_value = "test-issue"
    return ops


class TestSpecPhaseVersionedFiles:
    """測試 SpecPhase 的版本化檔案功能。"""

    def test_first_iteration_creates_spec_001(
        self, tmp_path, mock_agent_manager, mock_permission_handler, mock_git_ops
    ):
        """測試第一輪產生 spec_001.md。"""
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Change to tmp directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create SpecPhase instance
            phase = SpecPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                workflow_mode=WorkflowMode.LOCAL,
                user_input="Initial requirement",
                interactive=False,
            )

            # Execute phase
            result = phase.execute()

            # Debug output
            print(f"\nResult: {result.status}, Message: {result.message}")
            if spec_dir.exists():
                print(f"Files in spec_dir: {list(spec_dir.iterdir())}")

            # Verify spec_001.md was created
            spec_001 = spec_dir / "spec_001.md"
            assert spec_001.exists(), "spec_001.md should be created"

            # Verify the file has content
            content = spec_001.read_text(encoding="utf-8")
            assert content == "Initial requirement"

            # In non-interactive mode, phase returns IN_PROGRESS after one iteration
            assert result.status == PhaseStatus.IN_PROGRESS
        finally:
            os.chdir(original_cwd)

    def test_second_iteration_creates_spec_002_by_copying(
        self, tmp_path, mock_agent_manager, mock_permission_handler, mock_git_ops
    ):
        """測試第二輪產生 spec_002.md（內容為 spec_001.md 的複製）。"""
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Create spec_001.md with content
        spec_001 = spec_dir / "spec_001.md"
        spec_001.write_text("First iteration content", encoding="utf-8")

        # Create history for first iteration
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_001 = history_dir / "iteration_001.json"
        import json
        history_001.write_text(
            json.dumps({
                "iteration": 1,
                "response": "First response",
                "status_code": "CAFE_NEED_CLARIFICATION"
            }),
            encoding="utf-8"
        )

        # Change to tmp directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create SpecPhase instance for second iteration
            phase = SpecPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                workflow_mode=WorkflowMode.LOCAL,
                user_input="Clarification answer",
                interactive=False,
            )

            # Execute phase
            result = phase.execute()

            # Verify spec_002.md was created
            spec_002 = spec_dir / "spec_002.md"
            assert spec_002.exists(), "spec_002.md should be created"

            # Verify it was copied from spec_001.md
            assert spec_002.read_text(encoding="utf-8") == "First iteration content"

            # In non-interactive mode, phase returns IN_PROGRESS after one iteration
            assert result.status == PhaseStatus.IN_PROGRESS
        finally:
            os.chdir(original_cwd)

    def test_exceeds_999_iterations_raises_error(
        self, tmp_path, mock_agent_manager, mock_permission_handler, mock_git_ops
    ):
        """測試超過 999 輪時拋出錯誤。"""
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Create 999 spec files
        for i in range(1, 1000):
            (spec_dir / f"spec_{i:03d}.md").touch()

        # Create history files
        history_dir = spec_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 1000):
            history_file = history_dir / f"iteration_{i:03d}.json"
            import json
            history_file.write_text(
                json.dumps({"iteration": i, "response": f"Response {i}"}),
                encoding="utf-8"
            )

        # Change to tmp directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create SpecPhase instance
            phase = SpecPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                workflow_mode=WorkflowMode.LOCAL,
                user_input="New requirement",
                interactive=False,
            )

            # Execute should fail with ValueError
            result = phase.execute()

            # Should return FAILED status
            assert result.status == PhaseStatus.FAILED
            assert "不可超過 999" in result.message
        finally:
            os.chdir(original_cwd)
