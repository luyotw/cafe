"""Integration tests for permission denial storage in actual phase execution.

These tests verify that response and permission_denials are correctly saved
to iteration history JSON files when phases execute with real agent interactions.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import (
    AgentConfig,
    AgentCLI,
    PermissionDenial,
    PhaseStatus,
    SpecRigor,
    TokenUsage,
    WorkflowMode,
)
from cafe.phases.develop_phase import DevelopPhase
from cafe.phases.plan_phase import PlanPhase
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.review_phase import ReviewPhase


def setup_agent_manager_mock(agent_name: str = "TestAgent", cli: AgentCLI = AgentCLI.CLAUDE) -> MagicMock:
    """Setup a properly configured agent manager mock."""
    agent_manager = MagicMock(spec=AgentManager)

    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name=agent_name,
        cli=cli,
        session_id="test-session-id"
    )
    agent_manager.get_agent.return_value = mock_agent
    agent_manager.get_total_token_usage.return_value = TokenUsage()

    return agent_manager


class TestSpecPhasePermissionDenialStorage:
    """Test SpecPhase saves response and permission_denials correctly."""

    def test_spec_phase_saves_permission_denials_with_need_clarification(self, tmp_path: Path):
        """測試 SpecPhase 在 NEED_CLARIFICATION 時正確儲存 response 和 permission_denials"""
        issue_name = "test-spec-permission-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nInitial requirements")

        agent_manager = setup_agent_manager_mock("Roger", AgentCLI.CLAUDE)

        # Mock agent returns NEED_CLARIFICATION with permission denials
        permission_denials = [
            PermissionDenial(
                tool_name="Read",
                tool_input={"file_path": "/etc/passwd"}
            )
        ]
        agent_manager.execute.return_value = (
            "CAFE_NEED_CLARIFICATION\n我需要讀取 /etc/passwd 來分析需求。",
            TokenUsage(),
            permission_denials,
            None  # cli_command_args
        )

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
            rigor=SpecRigor.MEDIUM,
        )

        # Mock user input
        with patch('builtins.print'), \
             patch.object(phase.display, 'get_multiline_input', side_effect=["初始需求", "補充資訊"]):
            # Execute one iteration
            result = phase.execute()

        # Verify iteration history was saved with response and permission_denials
        history_dir = spec_file.parent / "history"
        iteration_file = history_dir / "iteration_001.json"

        assert iteration_file.exists(), f"Iteration file should exist at {iteration_file}"

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        # Verify all expected fields are present
        assert "response" in iteration_data, "iteration_data must contain 'response'"
        assert "permission_denials" in iteration_data, "iteration_data must contain 'permission_denials'"
        assert "prompt" in iteration_data, "iteration_data must contain 'prompt'"
        assert "cli" in iteration_data, "iteration_data must contain 'cli'"
        assert "session_id" in iteration_data, "iteration_data must contain 'session_id'"
        assert "allowed_tools" in iteration_data, "iteration_data must contain 'allowed_tools'"
        assert "status_code" in iteration_data, "iteration_data must contain 'status_code'"

        # Verify content
        assert "CAFE_NEED_CLARIFICATION" in iteration_data["response"]
        assert len(iteration_data["permission_denials"]) == 1
        assert iteration_data["permission_denials"][0]["tool_name"] == "Read"
        assert iteration_data["permission_denials"][0]["tool_input"]["file_path"] == "/etc/passwd"
        assert iteration_data["status_code"] == "CAFE_NEED_CLARIFICATION"
        assert iteration_data["cli"] == "claude"

    def test_spec_phase_saves_empty_permission_denials_with_confirmed(self, tmp_path: Path):
        """測試 SpecPhase 在 CONFIRMED 時正確儲存空的 permission_denials"""
        issue_name = "test-spec-confirmed-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nClear requirements")

        agent_manager = setup_agent_manager_mock("Roger", AgentCLI.CLAUDE)

        # Mock agent returns CONFIRMED without permission denials
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\n需求已經很清楚了。",
            TokenUsage(),
            [],  # No permission denials
            None  # cli_command_args
        )

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
             patch.object(phase.display, 'get_multiline_input', return_value="清楚的需求"):
            result = phase.execute()

        # Verify iteration history
        history_dir = spec_file.parent / "history"
        iteration_file = history_dir / "iteration_001.json"

        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "response" in iteration_data
        assert "permission_denials" in iteration_data
        assert iteration_data["permission_denials"] == []
        assert "CAFE_CONFIRMED" in iteration_data["response"]
        assert iteration_data["status_code"] == "CAFE_CONFIRMED"


class TestPlanPhasePermissionDenialStorage:
    """Test PlanPhase saves response and permission_denials correctly."""

    def test_plan_phase_saves_permission_denials_with_need_clarification(self, tmp_path: Path):
        """測試 PlanPhase 在 NEED_CLARIFICATION 時正確儲存 response 和 permission_denials"""
        issue_name = "test-plan-permission-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_dir = tmp_path / ".cafe" / "issues" / issue_name / "plan"
        history_dir = plan_dir / "history"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest requirements")

        # Create plan.md with dev guide (simulates already created from first iteration)
        plan_file = plan_dir / "plan.md"
        plan_file.write_text("# Dev Guide\nDev guide content\n\n# Implementation Plan\n")

        # Create a previous iteration to simulate this is not the first iteration
        iteration_001 = history_dir / "iteration_001.json"
        iteration_001.write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Initial plan prompt",
            "response": "CAFE_READY_FOR_REVIEW\nInitial plan",
            "status_code": "CAFE_READY_FOR_REVIEW",
            "permission_denials": []
        }))

        agent_manager = setup_agent_manager_mock("David", AgentCLI.CLAUDE)

        # Mock agent returns NEED_CLARIFICATION with permission denials
        permission_denials = [
            PermissionDenial(
                tool_name="Bash",
                tool_input={"command": "git status"}
            )
        ]
        agent_manager.execute.return_value = (
            "CAFE_NEED_CLARIFICATION\n我需要執行 git status 來了解專案狀態。",
            TokenUsage(),
            permission_denials,
            None  # cli_command_args
        )

        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name=issue_name,
            interactive=False,
            template_path=None,
            user_input="需求細節",
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Verify iteration history - should now be iteration 2
        iteration_file = history_dir / "iteration_002.json"

        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "response" in iteration_data
        assert "permission_denials" in iteration_data
        assert len(iteration_data["permission_denials"]) == 1
        assert iteration_data["permission_denials"][0]["tool_name"] == "Bash"
        assert iteration_data["permission_denials"][0]["tool_input"]["command"] == "git status"
        assert "CAFE_NEED_CLARIFICATION" in iteration_data["response"]


class TestDevelopPhasePermissionDenialStorage:
    """Test DevelopPhase saves response and permission_denials correctly."""

    def test_develop_phase_saves_permission_denials_with_need_permission(self, tmp_path: Path):
        """測試 DevelopPhase 在 NEED_PERMISSION 時正確儲存 response 和 permission_denials"""
        issue_name = "test-develop-permission-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Implementation Plan\nPlan content")

        agent_manager = setup_agent_manager_mock("David", AgentCLI.CLAUDE)

        # Mock agent returns NEED_PERMISSION with permission denials
        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/home/user/app/config.php"}
            ),
            PermissionDenial(
                tool_name="Write",
                tool_input={"file_path": "/home/user/app/new_feature.php"}
            )
        ]
        agent_manager.execute.return_value = (
            "CAFE_NEED_PERMISSION\n\n我需要請求以下權限：\n- Edit: /home/user/app/config.php\n- Write: /home/user/app/new_feature.php",
            TokenUsage(),
            permission_denials,
            None  # cli_command_args
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False
        git_ops.get_current_branch.return_value = "main"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Verify phase returned IN_PROGRESS
        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("status_code") == "CAFE_NEED_PERMISSION"

        # Verify iteration history was saved with response and permission_denials
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        iteration_file = history_dir / "iteration_001.json"

        assert iteration_file.exists(), f"Iteration file should exist at {iteration_file}"

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        # This is the critical test - verify all fields are present
        assert "response" in iteration_data, "iteration_data MUST contain 'response'"
        assert "permission_denials" in iteration_data, "iteration_data MUST contain 'permission_denials'"
        assert "prompt" in iteration_data
        assert "cli" in iteration_data
        assert "session_id" in iteration_data
        assert "allowed_tools" in iteration_data
        assert "status_code" in iteration_data

        # Verify content
        assert "CAFE_NEED_PERMISSION" in iteration_data["response"]
        assert len(iteration_data["permission_denials"]) == 2
        assert iteration_data["permission_denials"][0]["tool_name"] == "Edit"
        assert iteration_data["permission_denials"][1]["tool_name"] == "Write"
        assert iteration_data["status_code"] == "CAFE_NEED_PERMISSION"

    def test_develop_phase_saves_response_with_confirmed(self, tmp_path: Path):
        """測試 DevelopPhase 在 CONFIRMED 時正確儲存 response（無 permission_denials）"""
        issue_name = "test-develop-confirmed-issue"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Implementation Plan\nPlan content")

        agent_manager = setup_agent_manager_mock("David", AgentCLI.CLAUDE)

        # Mock agent returns CONFIRMED without permission denials
        agent_manager.execute.return_value = (
            "CAFE_CONFIRMED\n\n開發工作已完成。所有功能都已實作並測試通過。",
            TokenUsage(),
            [],  # No permission denials
            None  # cli_command_args
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False
        git_ops.get_current_branch.return_value = "main"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Verify phase completed
        assert result.status == PhaseStatus.COMPLETED

        # Verify iteration history
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        iteration_file = history_dir / "iteration_001.json"

        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert "response" in iteration_data
        assert "permission_denials" in iteration_data
        assert iteration_data["permission_denials"] == []
        assert "CAFE_CONFIRMED" in iteration_data["response"]
        assert iteration_data["status_code"] == "CAFE_CONFIRMED"


class TestMultiplePermissionDenials:
    """Test phases handle multiple permission denials correctly."""

    def test_multiple_permission_denials_are_all_saved(self, tmp_path: Path):
        """測試多個 permission denials 都被正確儲存"""
        issue_name = "test-multiple-denials"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Implementation Plan\nPlan content")

        agent_manager = setup_agent_manager_mock("David", AgentCLI.CLAUDE)

        # Mock agent returns NEED_PERMISSION with 5 different permission denials
        permission_denials = [
            PermissionDenial(tool_name="Read", tool_input={"file_path": "/file1.php"}),
            PermissionDenial(tool_name="Write", tool_input={"file_path": "/file2.php"}),
            PermissionDenial(tool_name="Edit", tool_input={"file_path": "/file3.php"}),
            PermissionDenial(tool_name="Bash", tool_input={"command": "git status"}),
            PermissionDenial(tool_name="Bash", tool_input={"command": "npm install"}),
        ]
        agent_manager.execute.return_value = (
            "CAFE_NEED_PERMISSION\n需要多個權限",
            TokenUsage(),
            permission_denials,
            None  # cli_command_args
        )

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = False
        git_ops.get_current_branch.return_value = "main"

        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=True,
        )

        with patch('builtins.print'):
            result = phase.execute()

        # Verify all permission denials are saved
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        iteration_file = history_dir / "iteration_001.json"

        assert iteration_file.exists()

        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)

        assert len(iteration_data["permission_denials"]) == 5
        assert iteration_data["permission_denials"][0]["tool_name"] == "Read"
        assert iteration_data["permission_denials"][1]["tool_name"] == "Write"
        assert iteration_data["permission_denials"][2]["tool_name"] == "Edit"
        assert iteration_data["permission_denials"][3]["tool_name"] == "Bash"
        assert iteration_data["permission_denials"][4]["tool_name"] == "Bash"
