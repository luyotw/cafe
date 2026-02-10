"""Tests for PR phase output.md containing only todo list (not PR comments)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseStatus
from cafe.phases.pr_phase import PRPhase
from cafe.utils.github import GitHubOps


@pytest.fixture
def temp_issue_dir(tmp_path):
    """Create temporary issue directory structure."""
    issue_dir = tmp_path / ".cafe" / "issues" / "test_issue"
    issue_dir.mkdir(parents=True)

    # Create spec file
    spec_dir = issue_dir / "spec" / "iteration_001"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "output.md"
    spec_file.write_text("# Test Spec\n\nTest requirements")

    # Create plan file
    plan_dir = issue_dir / "plan" / "iteration_001"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "output.md"
    plan_file.write_text("# Test Plan\n\nTest plan content")

    # Create issue.yaml
    issue_yaml = issue_dir / "issue.yaml"
    issue_yaml.write_text("issue_id: '123'\nbase_branch: main\n")

    return issue_dir


@pytest.fixture
def pr_phase_instance(temp_issue_dir, tmp_path):
    """Create PRPhase instance for testing."""
    agent_manager = MagicMock(spec=AgentManager)
    permission_handler = MagicMock(spec=PermissionHandler)
    git_ops = MagicMock(spec=GitOperations)
    github_ops = MagicMock(spec=GitHubOps)

    spec_file = str(temp_issue_dir / "spec" / "iteration_001" / "output.md")

    with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
        pr_phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=spec_file,
            issue_id="123",
            issue_name="test_issue",
            interactive=False,
        )

    return pr_phase


class TestOutputMdTodoOnly:
    """Test output.md contains only todo list without original PR comments."""

    def test_output_md_does_not_contain_pr_comments_after_organization(self, pr_phase_instance, temp_issue_dir):
        """Test 1.1: Verify output.md does not contain original PR comment content after organization."""
        # Arrange: Create PR iteration directory with user_input.md containing PR comments
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        pr_comments_content = """================================================================================
📝 PR Review Comments (2 unresolved comments)
================================================================================

Comment #1 [ID: 123]
Author: reviewer
Location: src/test.py (line 10)
Created: 2024-01-01T12:00:00Z

This function needs better error handling.

--------------------------------------------------------------------------------

Comment #2 [ID: 124]
Author: reviewer
Created: 2024-01-01T12:05:00Z

Please add unit tests for this feature.

--------------------------------------------------------------------------------
"""
        user_input_file.write_text(pr_comments_content, encoding="utf-8")

        # Mock agent execution to write todo list only to output.md
        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            todo_content = """## Todo List

### Error Handling
- [ ] Add better error handling to test.py line 10

### Testing
- [ ] Add unit tests for the new feature
"""
            output_file.write_text(todo_content, encoding="utf-8")
            return ("Todo list created", PhaseStatusCode.NEEDS_CHANGES)

        pr_phase_instance._execute_agent_iteration = mock_execute_agent
        pr_phase_instance.iteration = 1
        pr_phase_instance.issue_dir = temp_issue_dir

        # Mock checklist validation
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate:
            mock_validate.return_value = MagicMock(is_complete=True)

            # Act: Call _organize_comments_to_todo_list
            result = pr_phase_instance._organize_comments_to_todo_list(
                pr_number=123,
                pr_url="https://github.com/test/repo/pull/123",
                branch_name="test-branch"
            )

        # Assert: output.md should NOT contain the original PR comments
        output_file = iteration_dir / "output.md"
        assert output_file.exists()
        output_content = output_file.read_text(encoding="utf-8")

        # Should NOT contain PR comment markers
        assert "📝 PR Review Comments" not in output_content
        assert "Comment #1" not in output_content
        assert "Comment #2" not in output_content
        assert "[ID: 123]" not in output_content
        assert "This function needs better error handling." not in output_content

        # Should contain only the todo list
        assert "## Todo List" in output_content
        assert "- [ ]" in output_content
        assert "Add better error handling" in output_content
        assert "Add unit tests" in output_content

    def test_output_md_created_empty_not_copied_from_user_input(self, pr_phase_instance, temp_issue_dir):
        """Test that output.md is created empty, not copied from user_input.md."""
        # Arrange
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        user_input_content = "PR Comment 1\nPR Comment 2"
        user_input_file.write_text(user_input_content, encoding="utf-8")

        # Mock agent execution
        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            # Check that output.md either doesn't exist or is empty before agent writes
            if output_file.exists():
                content = output_file.read_text(encoding="utf-8")
                # Should not be a copy of user_input.md
                assert content != user_input_content, "output.md should not be copied from user_input.md"

            # Agent writes todo list
            output_file.write_text("## Todo List\n- [ ] Item 1", encoding="utf-8")
            return ("Done", PhaseStatusCode.NEEDS_CHANGES)

        pr_phase_instance._execute_agent_iteration = mock_execute_agent
        pr_phase_instance.iteration = 1
        pr_phase_instance.issue_dir = temp_issue_dir

        # Mock checklist validation
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate:
            mock_validate.return_value = MagicMock(is_complete=True)

            # Act
            result = pr_phase_instance._organize_comments_to_todo_list(
                pr_number=123,
                pr_url="https://github.com/test/repo/pull/123",
                branch_name="test-branch"
            )

        # Assert: output.md should not equal user_input.md
        output_file = iteration_dir / "output.md"
        output_content = output_file.read_text(encoding="utf-8")
        assert output_content != user_input_content
        assert "## Todo List" in output_content
