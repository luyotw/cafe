"""Tests for PR phase posting todo list as PR comment in GitHub mode."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseStatus, PhaseResult
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


class TestPostTodoListComment:
    """Test posting todo list as PR comment in GitHub mode."""

    def test_add_pr_comment_called_with_todo_list_in_github_mode(self, pr_phase_instance, temp_issue_dir):
        """Test 2.1: Verify add_pr_comment is called with todo list content after successful organization in GitHub mode."""
        # Arrange
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("PR Comment 1\nPR Comment 2", encoding="utf-8")

        todo_content = """## Todo List

### Testing
- [ ] Add unit tests
- [ ] Add integration tests
"""

        # Mock agent execution
        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            output_file.write_text(todo_content, encoding="utf-8")
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

        # Assert: add_pr_comment should be called with todo list content
        pr_phase_instance.github_ops.add_pr_comment.assert_called_once()
        call_args = pr_phase_instance.github_ops.add_pr_comment.call_args
        assert call_args[0][0] == "123"  # PR number
        comment_body = call_args[0][1]

        # Check comment contains todo list
        assert "## Todo List" in comment_body
        assert "- [ ] Add unit tests" in comment_body
        assert "- [ ] Add integration tests" in comment_body

        # Check comment contains user_input.md reference
        assert "user_input.md" in comment_body

    def test_add_pr_comment_not_called_in_local_mode(self, pr_phase_instance, temp_issue_dir):
        """Test 2.2: Verify add_pr_comment is NOT called in local mode (pr_number=0)."""
        # Arrange
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("PR Comment 1", encoding="utf-8")

        # Mock agent execution
        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            output_file.write_text("## Todo List\n- [ ] Item 1", encoding="utf-8")
            return ("Done", PhaseStatusCode.NEEDS_CHANGES)

        pr_phase_instance._execute_agent_iteration = mock_execute_agent
        pr_phase_instance.iteration = 1
        pr_phase_instance.issue_dir = temp_issue_dir

        # Mock checklist validation
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate:
            mock_validate.return_value = MagicMock(is_complete=True)

            # Act: Call with pr_number=0 (local mode)
            result = pr_phase_instance._organize_comments_to_todo_list(
                pr_number=0,
                pr_url="local",
                branch_name="local"
            )

        # Assert: add_pr_comment should NOT be called in local mode
        pr_phase_instance.github_ops.add_pr_comment.assert_not_called()

    def test_comment_includes_user_input_md_file_path_reference(self, pr_phase_instance, temp_issue_dir):
        """Test 2.3: Verify the comment includes the user_input.md file path reference."""
        # Arrange
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("PR Comment", encoding="utf-8")

        # Mock agent execution
        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            output_file.write_text("## Todo List\n- [ ] Fix bug", encoding="utf-8")
            return ("Done", PhaseStatusCode.NEEDS_CHANGES)

        pr_phase_instance._execute_agent_iteration = mock_execute_agent
        pr_phase_instance.iteration = 1
        pr_phase_instance.issue_dir = temp_issue_dir

        # Mock checklist validation
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate:
            mock_validate.return_value = MagicMock(is_complete=True)

            # Mock to_cwd_relative_path to return a predictable path
            with patch('cafe.utils.git_utils.to_cwd_relative_path') as mock_path:
                mock_path.return_value = ".cafe/issues/test_issue/pr/iteration_001/user_input.md"

                # Act
                result = pr_phase_instance._organize_comments_to_todo_list(
                    pr_number=456,
                    pr_url="https://github.com/test/repo/pull/456",
                    branch_name="feature-branch"
                )

        # Assert: Comment should reference user_input.md file path
        pr_phase_instance.github_ops.add_pr_comment.assert_called_once()
        comment_body = pr_phase_instance.github_ops.add_pr_comment.call_args[0][1]

        assert "user_input.md" in comment_body
        assert ".cafe/issues/test_issue/pr/iteration_001/user_input.md" in comment_body or "iteration_001/user_input.md" in comment_body
