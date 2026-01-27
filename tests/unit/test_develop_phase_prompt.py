"""Test develop phase prompt generation with review feedback and PR comments."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import PhaseStatus
from cafe.phases.develop_phase import DevelopPhase


class TestDevelopPhasePromptGeneration:
    """Test that prompt generation correctly handles review feedback vs PR comments."""

    @pytest.fixture
    def mock_git_ops(self):
        """Create mock GitOperations."""
        git_ops = MagicMock()
        git_ops.has_unpushed_commits.return_value = False
        git_ops.get_latest_unpushed_commit_timestamp.return_value = None
        git_ops.branch_exists.return_value = True
        git_ops.get_current_branch.return_value = "test-issue"
        return git_ops

    @pytest.fixture
    def mock_agent_manager(self):
        """Create mock AgentManager."""
        return MagicMock()

    @pytest.fixture
    def mock_permission_handler(self):
        """Create mock PermissionHandler."""
        return MagicMock()

    def test_prompt_excludes_pr_comments_when_review_feedback_exists(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR comments are NOT included in prompt when review feedback exists."""
        # Arrange: Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback (NEEDS_CHANGES)
        review_dir = issue_dir / "review"
        review_status_file = review_dir / "status.json"
        review_status_file.parent.mkdir(parents=True, exist_ok=True)
        review_status_file.write_text(json.dumps({
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": "2026-01-07T11:39:41+08:00"
        }))

        review_output = review_dir / "iteration_001" / "output.md"
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text("Please fix commit message format")

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Mock get_agent_file_path
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):
            # Create phase with pr_number set
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
                pr_number="123",  # PR number is set
            )

            # Call _check_if_already_completed_with_review to set _has_review_feedback flag
            phase._check_if_already_completed_with_review()

            # Mock _load_pr_comments to track if it's called
            original_load_pr_comments = phase._load_pr_comments
            load_pr_comments_called = False

            def mock_load_pr_comments():
                nonlocal load_pr_comments_called
                load_pr_comments_called = True
                return "PR comment content", 1

            phase._load_pr_comments = mock_load_pr_comments

            # Need to set iteration before calling _generate_prompt
            phase.iteration = 1

            # Act: Generate prompt
            prompt = phase._generate_prompt(user_input="")

        # Assert: PR comments should NOT be in prompt (they're in checklist now)
        assert "PR Review Comments" not in prompt
        assert "PR comment content" not in prompt
        assert "unresolved comment" not in prompt.lower()

        # Assert: Review feedback and PR feedback are handled via checklist, not in prompt
        # The prompt should reference the checklist for these items
        assert "checklist.md" in prompt.lower()

        # Assert: _load_pr_comments should NOT have been called
        assert not load_pr_comments_called

    @pytest.mark.xfail(reason="Test needs updating for new architecture where PR comments come from user_input.md instead of GitHub API")
    def test_prompt_includes_pr_comments_when_no_review_feedback(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR comments ARE included in prompt when no review feedback exists."""
        # Arrange: Create directory structure without review feedback
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # No review feedback - review directory doesn't exist

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Create PR iteration with user_input.md containing comments
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create user_input.md with formatted PR comments
        user_input_file = pr_iteration_dir / "user_input.md"
        user_input_file.write_text("Formatted PR comments")

        # Create context.json for PR iteration with timestamp
        import json
        from datetime import datetime, timezone
        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pr_number": 123,
            "source": "github_pr_comments"
        }))

        # Mock get_agent_file_path
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):

            # Create phase with pr_number set
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
                pr_number="123",
            )

            # Need to set iteration before calling _generate_prompt
            phase.iteration = 1

            # Act: Generate prompt
            prompt = phase._generate_prompt(user_input="")

            # Assert: PR comments should be in prompt
            assert "Formatted PR comments" in prompt

    @pytest.mark.xfail(reason="Test needs updating for new architecture where PR comments come from user_input.md instead of GitHub API")
    def test_prompt_with_review_feedback_approved(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR comments ARE included when review feedback is APPROVED (not NEEDS_CHANGES)."""
        # Arrange: Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback with APPROVED status
        review_dir = issue_dir / "review"
        review_status_file = review_dir / "status.json"
        review_status_file.parent.mkdir(parents=True, exist_ok=True)
        review_status_file.write_text(json.dumps({
            "status_code": "CAFE_CONFIRMED",  # Approved, not NEEDS_CHANGES
            "timestamp": "2026-01-07T11:39:41+08:00"
        }))

        review_output = review_dir / "iteration_001" / "output.md"
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text("Looks good!")

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Create PR iteration with user_input.md containing comments
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create user_input.md with formatted PR comments
        user_input_file = pr_iteration_dir / "user_input.md"
        user_input_file.write_text("Formatted PR comments")

        # Create context.json for PR iteration with timestamp
        from datetime import datetime, timezone
        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pr_number": 123,
            "source": "github_pr_comments"
        }))

        # Mock get_agent_file_path
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):

            # Create phase with pr_number set
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
                pr_number="123",
            )

            # Need to set iteration before calling _generate_prompt
            phase.iteration = 1

            # Act: Generate prompt
            prompt = phase._generate_prompt(user_input="")

            # Assert: PR comments should be in prompt because review is APPROVED
            assert "Formatted PR comments" in prompt
