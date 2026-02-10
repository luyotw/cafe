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


class TestPostTodoListConfigOption:
    """Test post_todo_list configuration option controls PR comment posting."""

    def _create_pr_phase(self, temp_issue_dir, post_todo_list=None):
        """Helper to create PRPhase with optional post_todo_list parameter."""
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

        with patch.object(PRPhase, '_get_issue_dir', return_value=temp_issue_dir):
            pr_phase = PRPhase(**kwargs)

        return pr_phase

    def _setup_iteration_dir(self, temp_issue_dir):
        """Helper to set up PR iteration directory with required files."""
        pr_dir = temp_issue_dir / "pr"
        iteration_dir = pr_dir / "iteration_001"
        iteration_dir.mkdir(parents=True)

        user_input_file = iteration_dir / "user_input.md"
        user_input_file.write_text("PR Comment 1", encoding="utf-8")

        return iteration_dir

    def _run_organize(self, pr_phase, temp_issue_dir, iteration_dir, pr_number):
        """Helper to run _organize_comments_to_todo_list with mocked agent."""
        from cafe.core.status_codes import PhaseStatusCode

        def mock_execute_agent(*args, **kwargs):
            output_file = iteration_dir / "output.md"
            output_file.write_text("## Todo List\n- [ ] Fix issue", encoding="utf-8")
            return ("Done", PhaseStatusCode.NEEDS_CHANGES)

        pr_phase._execute_agent_iteration = mock_execute_agent
        pr_phase.iteration = 1
        pr_phase.issue_dir = temp_issue_dir

        with patch('cafe.utils.checklist_validator.validate_checklist') as mock_validate, \
             patch.object(pr_phase, '_print_token_usage_summary'):
            mock_validate.return_value = MagicMock(is_complete=True)
            return pr_phase._organize_comments_to_todo_list(
                pr_number=pr_number,
                pr_url="https://github.com/test/repo/pull/123",
                branch_name="test-branch"
            )

    def test_post_todo_list_true_github_mode_calls_add_pr_comment(self, temp_issue_dir):
        """Test 1.1: post_todo_list=True かつ pr_number > 0 の場合、add_pr_comment が呼ばれる。"""
        pr_phase = self._create_pr_phase(temp_issue_dir, post_todo_list=True)
        iteration_dir = self._setup_iteration_dir(temp_issue_dir)

        self._run_organize(pr_phase, temp_issue_dir, iteration_dir, pr_number=123)

        pr_phase.github_ops.add_pr_comment.assert_called_once()

    def test_post_todo_list_false_github_mode_does_not_call_add_pr_comment(self, temp_issue_dir):
        """Test 1.2: post_todo_list=False かつ pr_number > 0 の場合、add_pr_comment が呼ばれない。"""
        pr_phase = self._create_pr_phase(temp_issue_dir, post_todo_list=False)
        iteration_dir = self._setup_iteration_dir(temp_issue_dir)

        self._run_organize(pr_phase, temp_issue_dir, iteration_dir, pr_number=123)

        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_post_todo_list_true_local_mode_does_not_call_add_pr_comment(self, temp_issue_dir):
        """Test 1.3: post_todo_list=True かつ pr_number=0（ローカルモード）の場合、add_pr_comment が呼ばれない。"""
        pr_phase = self._create_pr_phase(temp_issue_dir, post_todo_list=True)
        iteration_dir = self._setup_iteration_dir(temp_issue_dir)

        self._run_organize(pr_phase, temp_issue_dir, iteration_dir, pr_number=0)

        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_post_todo_list_reads_from_issue_yaml_when_not_provided(self, temp_issue_dir):
        """Test 1.4: post_todo_list が指定されない場合、issue.yaml の pr.post_todo_list から読み込む。"""
        # issue.yaml に post_todo_list: false を設定
        issue_yaml = temp_issue_dir / "issue.yaml"
        issue_yaml.write_text("issue_id: '123'\nbase_branch: main\npr:\n  post_todo_list: false\n")

        pr_phase = self._create_pr_phase(temp_issue_dir, post_todo_list=None)
        iteration_dir = self._setup_iteration_dir(temp_issue_dir)

        self._run_organize(pr_phase, temp_issue_dir, iteration_dir, pr_number=123)

        # post_todo_list=false なので add_pr_comment が呼ばれないはず
        pr_phase.github_ops.add_pr_comment.assert_not_called()

    def test_post_todo_list_defaults_to_true_when_not_in_config(self, temp_issue_dir):
        """Test 1.4b: issue.yaml に pr.post_todo_list がない場合、デフォルトで True（後方互換性維持）。"""
        # issue.yaml に post_todo_list が存在しない
        issue_yaml = temp_issue_dir / "issue.yaml"
        issue_yaml.write_text("issue_id: '123'\nbase_branch: main\n")

        pr_phase = self._create_pr_phase(temp_issue_dir, post_todo_list=None)
        iteration_dir = self._setup_iteration_dir(temp_issue_dir)

        self._run_organize(pr_phase, temp_issue_dir, iteration_dir, pr_number=123)

        # デフォルトは True なので add_pr_comment が呼ばれるはず
        pr_phase.github_ops.add_pr_comment.assert_called_once()
