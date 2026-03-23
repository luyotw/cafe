"""Tests for PR phase posting PR todo list as PR comment at PR creation/update."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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

    # Create plan file with all items checked
    plan_dir = issue_dir / "plan" / "iteration_001"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "output.md"
    plan_file.write_text(
        "# Test Plan\n\n- [x] Task 1\n- [x] Task 2\n",
        encoding="utf-8",
    )

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


def _make_pr_phase(temp_issue_dir, post_todo_list=None):
    """Helper to create a PRPhase instance."""
    agent_manager = MagicMock(spec=AgentManager)
    permission_handler = MagicMock(spec=PermissionHandler)
    git_ops = MagicMock(spec=GitOperations)
    github_ops = MagicMock(spec=GitHubOps)

    spec_file = str(temp_issue_dir / "spec" / "iteration_001" / "output.md")

    kwargs = dict(
        agent_manager=agent_manager,
        permission_handler=permission_handler,
        git_ops=git_ops,
        github_ops=github_ops,
        spec_file=spec_file,
        issue_id="123",
        issue_name="test_issue",
        interactive=False,
    )
    if post_todo_list is not None:
        kwargs["post_todo_list"] = post_todo_list

    with patch.object(PRPhase, "_get_issue_dir", return_value=temp_issue_dir):
        pr_phase = PRPhase(**kwargs)

    return pr_phase


class TestPostTodoListComment:
    """Test posting todo list as PR comment after organizing reviewer comments."""

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
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate, \
             patch.object(pr_phase_instance, '_print_token_usage_summary'):
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
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate, \
             patch.object(pr_phase_instance, '_print_token_usage_summary'):
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
        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate, \
             patch.object(pr_phase_instance, '_print_token_usage_summary'), \
             patch('cafe.utils.git_utils.to_cwd_relative_path') as mock_path:
            mock_validate.return_value = MagicMock(is_complete=True)
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


class TestPostPrTodoList:
    """Test _post_pr_todo_list method behaviour."""

    def _setup_pr_todo_list(self, temp_issue_dir, todo_content="## Todo List\n\n- [x] Fix bug\n- [x] Add test\n"):
        """Helper to create a PR iteration with user_input.md and output.md."""
        pr_dir = temp_issue_dir / "pr" / "iteration_001"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "user_input.md").write_text("reviewer comment 1\nreviewer comment 2", encoding="utf-8")
        (pr_dir / "output.md").write_text(todo_content, encoding="utf-8")
        return pr_dir

    def test_all_items_checked_calls_add_pr_comment(self, temp_issue_dir):
        """When all PR todo items are [x], add_pr_comment is called."""
        self._setup_pr_todo_list(temp_issue_dir)
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_called_once()
        call_args = pr_phase.github_ops.add_pr_comment.call_args
        assert call_args[0][0] == "42"
        assert "Fix bug" in call_args[0][1]
        assert "Add test" in call_args[0][1]

    def test_unchecked_item_does_not_call_add_pr_comment(self, temp_issue_dir):
        """When any PR todo item is unchecked, add_pr_comment is NOT called."""
        self._setup_pr_todo_list(temp_issue_dir, "## Todo List\n\n- [x] Done\n- [ ] Not done\n")
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_post_todo_list_false_does_not_call_add_pr_comment(self, temp_issue_dir):
        """When post_todo_list=False, add_pr_comment is NOT called."""
        self._setup_pr_todo_list(temp_issue_dir)
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=False)
        pr_phase.issue_dir = temp_issue_dir

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_no_pr_dir_does_not_raise(self, temp_issue_dir):
        """When pr dir does not exist, no error is raised and add_pr_comment is NOT called."""
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_add_pr_comment_failure_prints_warning_and_does_not_raise(self, temp_issue_dir):
        """When add_pr_comment raises an exception, a warning is shown and no error is raised."""
        self._setup_pr_todo_list(temp_issue_dir)
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir
        pr_phase.github_ops.add_pr_comment.side_effect = RuntimeError("network error")

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_called_once()

    def test_picks_latest_iteration_with_user_input(self, temp_issue_dir):
        """When multiple iterations exist, picks the latest one with user_input.md."""
        # iteration_001: has user_input + output (old, unchecked)
        pr_dir_1 = temp_issue_dir / "pr" / "iteration_001"
        pr_dir_1.mkdir(parents=True)
        (pr_dir_1 / "user_input.md").write_text("old comments", encoding="utf-8")
        (pr_dir_1 / "output.md").write_text("## Todo List\n\n- [ ] Old item\n", encoding="utf-8")

        # iteration_002: PR content only (no user_input.md)
        pr_dir_2 = temp_issue_dir / "pr" / "iteration_002"
        pr_dir_2.mkdir(parents=True)
        (pr_dir_2 / "output.md").write_text("PR title and body", encoding="utf-8")

        # iteration_003: has user_input + output (latest, all checked)
        pr_dir_3 = temp_issue_dir / "pr" / "iteration_003"
        pr_dir_3.mkdir(parents=True)
        (pr_dir_3 / "user_input.md").write_text("new comments", encoding="utf-8")
        (pr_dir_3 / "output.md").write_text("## Todo List\n\n- [x] New item done\n", encoding="utf-8")

        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        pr_phase._post_pr_todo_list("42")

        pr_phase.github_ops.add_pr_comment.assert_called_once()
        comment_body = pr_phase.github_ops.add_pr_comment.call_args[0][1]
        assert "New item done" in comment_body


class TestPostPrTodoListIntegration:
    """Test _post_pr_todo_list is called from _create_or_update_pr."""

    def _run_create_or_update_pr(self, pr_phase, existing_pr, branch_name="feature-branch"):
        """Helper to invoke _create_or_update_pr with mocked internals."""
        with patch.object(pr_phase, "_prepare_pr_content", return_value=(None, ("Test PR", "PR body"))), \
             patch.object(pr_phase, "_display_pr_success"), \
             patch.object(pr_phase, "_save_progress"), \
             patch.object(pr_phase, "_update_iteration_history"), \
             patch.object(pr_phase, "_post_pr_todo_list") as mock_post:
            result = pr_phase._create_or_update_pr(existing_pr, branch_name)
            return result, mock_post

    def test_pr_created_calls_post_pr_todo_list(self, temp_issue_dir):
        """When a PR is created, _post_pr_todo_list is called with the PR number."""
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        pr_url = "https://github.com/test/repo/pull/99"
        pr_phase.github_ops.create_pr.return_value = pr_url

        result, mock_post = self._run_create_or_update_pr(pr_phase, existing_pr=None)

        mock_post.assert_called_once_with("99")

    def test_pr_updated_calls_post_pr_todo_list(self, temp_issue_dir):
        """When a PR is updated, _post_pr_todo_list is called with the PR number."""
        pr_phase = _make_pr_phase(temp_issue_dir, post_todo_list=True)
        pr_phase.issue_dir = temp_issue_dir

        existing_pr = {"number": 55, "url": "https://github.com/test/repo/pull/55"}

        result, mock_post = self._run_create_or_update_pr(pr_phase, existing_pr=existing_pr)

        mock_post.assert_called_once_with("55")
