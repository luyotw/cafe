"""測試 PlanPhase 使用版本化檔案。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from cafe.phases.plan_phase import PlanPhase
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
        "CAFE_CONFIRMED\n計畫已完成",
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
        name="David",
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


class TestPlanPhaseVersionedFiles:
    """測試 PlanPhase 的版本化檔案功能。"""

    def test_first_iteration_creates_plan_001(
        self, tmp_path, mock_agent_manager, mock_permission_handler, mock_git_ops
    ):
        """測試第一輪產生 plan_001.md。"""
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        spec_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)

        # Create spec_001.md as prerequisite
        spec_001 = spec_dir / "spec_001.md"
        spec_001.write_text("Test requirement", encoding="utf-8")

        # Create template file for first iteration
        template_file = tmp_path / "template.md"
        template_file.write_text("# Template content", encoding="utf-8")

        # Change to tmp directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create PlanPhase instance
            phase = PlanPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_001),
                workflow_mode=WorkflowMode.LOCAL,
                template_path=str(template_file),
                user_input="Initial plan",
                interactive=False,
            )

            # Execute phase
            result = phase.execute()

            # Debug output
            print(f"\nResult: {result.status}, Message: {result.message}")
            if plan_dir.exists():
                print(f"Files in plan_dir: {list(plan_dir.iterdir())}")

            # Verify plan_001.md was created
            plan_001 = plan_dir / "plan_001.md"
            assert plan_001.exists(), "plan_001.md should be created"

            # Verify the file has content with dev guide header
            content = plan_001.read_text(encoding="utf-8")
            assert content == "## 開發指南\n\nInitial plan\n"

            # In non-interactive mode, after 5 retries without status code, returns FAILED
            assert result.status == PhaseStatus.FAILED
            assert "Agent 在 5 次嘗試後仍未回傳有效的 status code" in result.message
        finally:
            os.chdir(original_cwd)

    def test_second_iteration_creates_plan_002_by_copying(
        self, tmp_path, mock_agent_manager, mock_permission_handler, mock_git_ops
    ):
        """測試第二輪產生 plan_002.md（內容為 plan_001.md 的複製）。"""
        # Setup issue directory
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_dir = issue_dir / "spec"
        plan_dir = issue_dir / "plan"
        spec_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)

        # Create spec_001.md as prerequisite
        spec_001 = spec_dir / "spec_001.md"
        spec_001.write_text("Test requirement", encoding="utf-8")

        # Create plan_001.md with dev guide section
        plan_001 = plan_dir / "plan_001.md"
        plan_001.write_text("## 開發指南\n\nFirst iteration content", encoding="utf-8")

        # Create history for first iteration
        history_dir = plan_dir / "history"
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

            # Create PlanPhase instance for second iteration
            phase = PlanPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_001),
                workflow_mode=WorkflowMode.LOCAL,
                user_input="Clarification answer",
                interactive=False,
            )

            # Execute phase
            result = phase.execute()

            # Verify plan_002.md was created
            plan_002 = plan_dir / "plan_002.md"
            assert plan_002.exists(), "plan_002.md should be created"

            # Verify it was copied from plan_001.md (should preserve the original content)
            # Since plan_001.md has "## 開發指南" section, plan_002 should just be a copy
            assert plan_002.read_text(encoding="utf-8") == "## 開發指南\n\nFirst iteration content"

            # In non-interactive mode, after 5 retries without status code, returns FAILED
            assert result.status == PhaseStatus.FAILED
            assert "Agent 在 5 次嘗試後仍未回傳有效的 status code" in result.message
        finally:
            os.chdir(original_cwd)

