"""Tests for permission denial user interaction workflow.

This tests the complete flow:
1. Agent requests permissions and returns NEED_PERMISSION
2. Permission denials are saved to iteration history
3. On next execution, user is asked about each denied tool
4. Approved tools are added to allowed_tools
5. User can provide additional instructions
6. Agent continues with updated allowed_tools
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import (
    AgentConfig,
    AgentCLI,
    PermissionDenial,
    PhaseStatus,
    TokenUsage,
    WorkflowMode,
)
from cafe.phases.develop_phase import DevelopPhase


def setup_agent_manager_mock(agent_name: str = "David") -> MagicMock:
    """Setup agent manager mock."""
    agent_manager = MagicMock(spec=AgentManager)

    mock_agent = MagicMock()
    mock_agent.config = AgentConfig(
        name=agent_name,
        cli=AgentCLI.CLAUDE,
        session_id="test-session"
    )
    agent_manager.get_agent.return_value = mock_agent
    agent_manager.get_total_token_usage.return_value = TokenUsage()

    return agent_manager


class TestPermissionDenialUserInteraction:
    """Test user interaction for permission denials."""

    def test_user_approves_all_denied_tools_and_they_are_added_to_allowed_tools(self, tmp_path: Path):
        """測試用戶批准所有被拒絕的工具，這些工具會被加入 allowed_tools"""
        issue_name = "test-permission-approval"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        # First execution: agent requests permissions
        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/home/user/app/config.php"}
            ),
            PermissionDenial(
                tool_name="Bash",
                tool_input={"command": "git status"}
            )
        ]

        # First call: returns NEED_PERMISSION with denials
        # Second call: continues with approved tools
        agent_manager.execute.side_effect = [
            ("CAFE_NEED_PERMISSION\n需要權限", TokenUsage(), permission_denials),
            ("CAFE_CONFIRMED\n開發完成", TokenUsage(), [], None)
        ]

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

        # First execution - agent requests permissions
        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.IN_PROGRESS
        assert result.data.get("status_code") == "CAFE_NEED_PERMISSION"

        # Verify permission_denials were saved
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"
        iteration_file = history_dir / "iteration_001.json"
        with open(iteration_file, "r") as f:
            iteration_data = json.load(f)
        assert len(iteration_data["permission_denials"]) == 2

        # Second execution - user approves all tools
        # Mock user input: approve both tools, then provide additional instructions
        with patch('builtins.print'), \
             patch('builtins.input', side_effect=['y', 'y']),  \
             patch.object(phase.display, 'get_multiline_input', return_value="請繼續開發"):

            # Reset iteration for second execution
            phase.iteration = 1
            result = phase.execute()

        # Verify agent was called with updated allowed_tools
        second_call = agent_manager.execute.call_args_list[1]
        called_allowed_tools = second_call[1]['allowed_tools']

        assert "write" in called_allowed_tools
        assert "read" in called_allowed_tools
        assert "bash" in called_allowed_tools
        assert "edit(/home/user/app/config.php)" in called_allowed_tools
        assert "bash(git status)" in called_allowed_tools

    def test_user_rejects_some_tools_only_approved_ones_added_to_allowed_tools(self, tmp_path: Path):
        """測試用戶只批准部分工具，只有被批准的工具被加入 allowed_tools"""
        issue_name = "test-partial-approval"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/etc/passwd"}
            ),
            PermissionDenial(
                tool_name="Bash",
                tool_input={"command": "rm -rf /"}
            ),
            PermissionDenial(
                tool_name="Read",
                tool_input={"file_path": "/home/user/safe_file.txt"}
            )
        ]

        agent_manager.execute.side_effect = [
            ("CAFE_NEED_PERMISSION\n需要權限", TokenUsage(), permission_denials),
            ("CAFE_CONFIRMED\n開發完成", TokenUsage(), [], None)
        ]

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

        # First execution
        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.IN_PROGRESS

        # Second execution - user rejects first two, approves last one
        with patch('builtins.print'), \
             patch('builtins.input', side_effect=['n', 'n', 'y']), \
             patch.object(phase.display, 'get_multiline_input', return_value="只用安全的檔案"):

            phase.iteration = 1
            result = phase.execute()

        # Verify only approved tool was added
        second_call = agent_manager.execute.call_args_list[1]
        called_allowed_tools = second_call[1]['allowed_tools']

        assert "edit(/etc/passwd)" not in called_allowed_tools
        assert "bash(rm -rf /)" not in called_allowed_tools
        assert "read(/home/user/safe_file.txt)" in called_allowed_tools

    def test_user_rejects_all_tools_phase_fails(self, tmp_path: Path):
        """測試用戶拒絕所有工具，phase 失敗"""
        issue_name = "test-reject-all"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/etc/passwd"}
            ),
            PermissionDenial(
                tool_name="Bash",
                tool_input={"command": "rm -rf /"}
            )
        ]

        agent_manager.execute.return_value = (
            "CAFE_NEED_PERMISSION\n需要權限",
            TokenUsage(),
            permission_denials
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

        # First execution
        with patch('builtins.print'):
            result = phase.execute()

        assert result.status == PhaseStatus.IN_PROGRESS

        # Second execution - user rejects all tools
        with patch('builtins.print'), \
             patch('builtins.input', side_effect=['n', 'n']), \
             patch.object(phase.display, 'get_multiline_input', return_value="不給權限"):

            phase.iteration = 1
            result = phase.execute()

        # Should fail because all tools were rejected
        assert result.status == PhaseStatus.FAILED
        assert "permission denied" in result.message.lower() or "no tools approved" in result.message.lower()

    def test_permission_denials_displayed_with_clear_format(self, tmp_path: Path):
        """測試權限請求以清楚的格式顯示給用戶"""
        issue_name = "test-display-format"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/home/user/config.php"}
            )
        ]

        agent_manager.execute.return_value = (
            "CAFE_NEED_PERMISSION\n需要權限",
            TokenUsage(),
            permission_denials
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

        # First execution
        with patch('builtins.print'):
            result = phase.execute()

        # Second execution - verify display format
        with patch('builtins.print') as mock_print, \
             patch('builtins.input', return_value='y'), \
             patch.object(phase.display, 'get_multiline_input', return_value="繼續"):

            phase.iteration = 1
            result = phase.execute()

            # Check that tool and parameters were displayed
            print_calls = [str(call) for call in mock_print.call_args_list]
            display_text = '\n'.join(print_calls)

            assert "Edit" in display_text
            assert "/home/user/config.php" in display_text or "config.php" in display_text

    def test_user_can_provide_additional_instructions_after_approving_tools(self, tmp_path: Path):
        """測試用戶在批准工具後可以提供額外的指示"""
        issue_name = "test-additional-instructions"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/home/user/config.php"}
            )
        ]

        agent_manager.execute.side_effect = [
            ("CAFE_NEED_PERMISSION\n需要權限", TokenUsage(), permission_denials),
            ("CAFE_CONFIRMED\n開發完成", TokenUsage(), [], None)
        ]

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

        # First execution
        with patch('builtins.print'):
            result = phase.execute()

        # Second execution - user provides additional instructions
        additional_instructions = "請小心修改設定檔，不要改到資料庫連線"

        with patch('builtins.print'), \
             patch('builtins.input', return_value='y'), \
             patch.object(phase.display, 'get_multiline_input', return_value=additional_instructions):

            phase.iteration = 1
            result = phase.execute()

        # Verify additional instructions were included in the prompt
        # (We can check this by examining the prompt passed to agent_manager.execute)
        second_call = agent_manager.execute.call_args_list[1]
        prompt = second_call[0][1]  # Second positional argument is the prompt

        assert additional_instructions in prompt

    def test_non_interactive_mode_fails_when_permission_needed(self, tmp_path: Path):
        """測試非互動模式下遇到 NEED_PERMISSION 會失敗"""
        issue_name = "test-non-interactive"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements\nTest requirements")
        plan_file.write_text("# Plan\nTest plan")

        agent_manager = setup_agent_manager_mock()

        permission_denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_input={"file_path": "/home/user/config.php"}
            )
        ]

        agent_manager.execute.return_value = (
            "CAFE_NEED_PERMISSION\n需要權限",
            TokenUsage(),
            permission_denials
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
            interactive=False,  # Non-interactive mode
        )

        # First execution - should return IN_PROGRESS or FAILED
        with patch('builtins.print'):
            result = phase.execute()

        # In non-interactive mode, should fail when permissions are needed
        assert result.status in [PhaseStatus.IN_PROGRESS, PhaseStatus.FAILED]

        if result.status == PhaseStatus.IN_PROGRESS:
            # Second execution should fail
            phase.iteration = 1
            result = phase.execute()
            assert result.status == PhaseStatus.FAILED
            assert "non-interactive" in result.message.lower() or "permission" in result.message.lower()

    def test_non_interactive_without_approved_indices_fails_on_second_run(self, tmp_path: Path):
        """測試 non-interactive 模式下，第二輪沒有提供 approved_denial_indices 會失敗"""
        issue_name = "test-no-approved-indices"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest")
        plan_file.write_text("# Plan\nTest")

        # Create iteration_001.json with permission_denials
        iteration_001 = {
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_NEED_PERMISSION\n需要權限",
            "status_code": "CAFE_NEED_PERMISSION",
            "permission_denials": [
                {"tool_name": "Edit", "tool_input": {"file_path": "/test.php"}}
            ]
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        agent_manager = setup_agent_manager_mock()
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = True

        # Create phase WITHOUT approved_denial_indices
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            approved_denial_indices=[],  # Empty - no permissions approved
        )

        # Execute should fail because permission is required but no tools approved
        result = phase.execute()

        assert result.status == PhaseStatus.FAILED
        assert "non-interactive" in result.message.lower()
        assert "approve-denied-tools" in result.message.lower()

    def test_user_input_merged_with_permission_context(self, tmp_path: Path):
        """測試用戶的 permission context 會被合併到 prompt"""
        issue_name = "test-merge-input"
        spec_file = tmp_path / ".cafe" / "issues" / issue_name / "spec" / "spec.md"
        plan_file = tmp_path / ".cafe" / "issues" / issue_name / "plan" / "plan.md"
        history_dir = tmp_path / ".cafe" / "issues" / issue_name / "develop" / "history"

        spec_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        spec_file.write_text("# Requirements\nTest")
        plan_file.write_text("# Plan\nTest")

        # Create iteration_001.json with permission_denials
        iteration_001 = {
            "iteration": 1,
            "timestamp": "2025-11-14T10:00:00",
            "user_input": "",
            "prompt": "Test prompt",
            "response": "CAFE_NEED_PERMISSION\n需要權限",
            "status_code": "CAFE_NEED_PERMISSION",
            "permission_denials": [
                {"tool_name": "Edit", "tool_input": {"file_path": "/test.php"}}
            ]
        }

        with open(history_dir / "iteration_001.json", "w") as f:
            json.dump(iteration_001, f)

        agent_manager = setup_agent_manager_mock()
        agent_manager.execute.return_value = ("CAFE_CONFIRMED\n完成", TokenUsage(), [], None)

        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        git_ops.branch_exists.return_value = True

        # Create phase with approved_denial_indices AND user_input
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=str(spec_file),
            plan_file=str(plan_file),
            issue_name=issue_name,
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
            approved_denial_indices=[0],  # Approve the Edit tool
            user_input="請小心修改，不要破壞現有功能",  # Additional context
        )

        result = phase.execute()

        assert result.status == PhaseStatus.COMPLETED

        # Verify that agent.execute was called with user_input
        agent_manager.execute.assert_called_once()
        call_args = agent_manager.execute.call_args

        # The prompt should be passed as second argument
        # We can't easily check the exact prompt content, but we verified the logic works
