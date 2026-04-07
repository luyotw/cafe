"""Test PR phase GitHub mode execution flow."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseStatus, PhaseResult
from cafe.core.status_codes import PhaseStatusCode


pytestmark = pytest.mark.slow


class TestPRPhaseGitHubMode:
    """Test _execute_github_mode flow scenarios."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("# Test Plan")

        return issue_dir, spec_file

    # =========================================================================
    # Scenario A: Has new commits, no PR exists -> Create PR
    # =========================================================================
    def test_scenario_a_create_pr_with_new_commits(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario A: Has new commits, no PR -> Push + Create PR + Call agent -> READY_FOR_REVIEW."""
        issue_dir, spec_file = setup_issue_dir

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "feat: add feature"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = None  # No PR exists
        mock_dependencies["github_ops"].create_pr.return_value = "https://github.com/test/repo/pull/1"

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Test PR", "Test body"))):
                phase = PRPhase(
                    spec_file=str(spec_file),
                    issue_name="test-issue",
                    **mock_dependencies
                )

                result = phase._execute_github_mode()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert "created" in result.message.lower()
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

        # Verify iteration context.json was created with correct status_code
        iteration_dir = issue_dir / "pr" / "iteration_001"
        context_file = iteration_dir / "context.json"
        assert context_file.exists()
        with open(context_file) as f:
            context = json.load(f)
        assert context.get("status_code") == "CAFE_READY_FOR_REVIEW"

    # =========================================================================
    # Scenario B: Has new commits, PR exists -> Update PR
    # =========================================================================
    def test_scenario_b_update_pr_with_new_commits(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario B: Has new commits, PR exists -> Push + Update PR + Call agent -> READY_FOR_REVIEW."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001 with READY_FOR_REVIEW
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "def5678", "message": "fix: bug fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Updated PR", "Updated body"))):
                phase = PRPhase(
                    spec_file=str(spec_file),
                    issue_name="test-issue",
                    **mock_dependencies
                )

                result = phase._execute_github_mode()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert "updated" in result.message.lower()
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

        # Verify new iteration_002 was created
        iteration_002 = issue_dir / "pr" / "iteration_002"
        context_file = iteration_002 / "context.json"
        assert context_file.exists()
        with open(context_file) as f:
            context = json.load(f)
        assert context.get("status_code") == "CAFE_READY_FOR_REVIEW"

    # =========================================================================
    # Scenario C: No new commits, no PR -> Return "nothing to do"
    # =========================================================================
    def test_scenario_c_no_commits_no_pr(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario C: No new commits, no PR -> Return 'nothing to do'."""
        issue_dir, spec_file = setup_issue_dir

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = None

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._execute_github_mode()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert "no new commits" in result.message.lower() or "no commits" in result.message.lower()

    # =========================================================================
    # Scenario D: No new commits, PR exists, last iteration READY_FOR_REVIEW
    #             -> Fetch comments, call agent -> CONFIRMED or NEEDS_CHANGES
    # =========================================================================
    def test_scenario_d_fetch_comments_after_ready_for_review(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario D: No commits, PR exists, READY_FOR_REVIEW -> Fetch comments -> NEEDS_CHANGES."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001 with READY_FOR_REVIEW
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        # Mock fetching comments
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_save_pr_comments_to_user_input") as mock_save_comments:
                mock_save_comments.return_value = iteration_001 / "iteration_002" / "user_input.md"
                with patch.object(PRPhase, "_organize_comments_to_todo_list") as mock_organize:
                    mock_organize.return_value = PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message="Organized comments",
                        data={"status_code": "CAFE_NEEDS_CHANGES"}
                    )

                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        # Assert
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") in ["CAFE_NEEDS_CHANGES", "CAFE_CONFIRMED"]

    # =========================================================================
    # Scenario E: No new commits, PR exists, last iteration CONFIRMED
    #             -> Return "already completed"
    # =========================================================================
    def test_scenario_e_already_confirmed(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario E: No commits, PR exists, CONFIRMED -> Return 'already completed'."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001 with CONFIRMED
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))
        (iteration_001 / "user_input.md").write_text("Some feedback")

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._execute_github_mode()

        # Assert - should return completed without doing anything
        assert result.status == PhaseStatus.COMPLETED
        # Should not create new iteration
        assert not (issue_dir / "pr" / "iteration_002").exists()

    # =========================================================================
    # Scenario F: No new commits, PR exists, last iteration NEEDS_CHANGES
    #             -> Should be blocked by Step 1 (_check_waiting_for_develop)
    # =========================================================================
    def test_scenario_f_needs_changes_waiting_for_develop(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Scenario F: NEEDS_CHANGES -> Blocked by _check_waiting_for_develop."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration with NEEDS_CHANGES
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))
        (iteration_001 / "user_input.md").write_text("Please fix this")

        # No develop phase has run (so develop hasn't processed feedback)

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._execute_github_mode()

        # Assert - should be waiting for develop
        assert result.status == PhaseStatus.COMPLETED
        assert "waiting" in result.message.lower() or "develop" in result.message.lower()


class TestPRPhaseStep0ResumeIncomplete:
    """Test Step 0: Resume incomplete iteration."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        return issue_dir, spec_file

    def test_resume_incomplete_iteration_with_user_input(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Resume incomplete iteration that has user_input but no status_code."""
        issue_dir, spec_file = setup_issue_dir

        # Create incomplete iteration (no status_code)
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00"
            # No status_code - incomplete!
        }))
        (iteration_001 / "user_input.md").write_text("Please fix the bug")

        # Setup mocks
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_organize_comments_to_todo_list") as mock_organize:
                mock_organize.return_value = PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Organized",
                    data={"status_code": "CAFE_NEEDS_CHANGES"}
                )

                phase = PRPhase(
                    spec_file=str(spec_file),
                    issue_name="test-issue",
                    **mock_dependencies
                )

                result = phase._execute_github_mode()

        # Assert - should have resumed and completed
        mock_organize.assert_called_once()

        # Verify status_code was saved to context.json
        context_file = iteration_001 / "context.json"
        with open(context_file) as f:
            context = json.load(f)
        assert context.get("status_code") == "CAFE_NEEDS_CHANGES"

    def test_resume_incomplete_iteration_without_user_input(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Resume incomplete iteration that has no user_input (PR update iteration)."""
        issue_dir, spec_file = setup_issue_dir

        # Create incomplete iteration (no status_code, no user_input)
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00"
            # No status_code - incomplete!
        }))
        # No user_input.md - this was a PR create/update iteration
        # Create output.md with real content (not template) so it's considered complete
        (iteration_001 / "output.md").write_text("# Real PR Title\n\n## Summary\nThis is real content\n")

        # Setup mocks
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = False

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Title", "Body"))):
                phase = PRPhase(
                    spec_file=str(spec_file),
                    issue_name="test-issue",
                    **mock_dependencies
                )

                result = phase._execute_github_mode()

        # Assert - should have completed the iteration
        context_file = iteration_001 / "context.json"
        with open(context_file) as f:
            context = json.load(f)
        assert context.get("status_code") == "CAFE_READY_FOR_REVIEW"


class TestPRPhaseStep1WaitingForDevelop:
    """Test Step 1: Check waiting for develop."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        return issue_dir, spec_file

    def test_check_waiting_uses_status_code_not_user_input(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """_check_waiting_for_develop should use status_code == NEEDS_CHANGES, not has_user_input."""
        issue_dir, spec_file = setup_issue_dir

        # Create iteration with NEEDS_CHANGES status
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_NEEDS_CHANGES"
        }))
        # Note: No user_input.md needed - decision should be based on status_code

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._check_waiting_for_develop()

        # Assert - should be waiting because status_code is NEEDS_CHANGES
        assert result is not None

    def test_not_waiting_when_status_code_is_ready_for_review(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Should NOT wait when status_code is READY_FOR_REVIEW."""
        issue_dir, spec_file = setup_issue_dir

        # Create iteration with READY_FOR_REVIEW status
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._check_waiting_for_develop()

        # Assert - should NOT be waiting
        assert result is None

    def test_not_waiting_when_status_code_is_confirmed(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Should NOT wait when status_code is CONFIRMED."""
        issue_dir, spec_file = setup_issue_dir

        # Create iteration with CONFIRMED status
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )

            result = phase._check_waiting_for_develop()

        # Assert - should NOT be waiting
        assert result is None


class TestPRPhasePriorityNewCommits:
    """Test that new commits take priority over other states."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        return issue_dir, spec_file

    def test_new_commits_override_ready_for_review(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """New commits should update PR even if last iteration was READY_FOR_REVIEW."""
        issue_dir, spec_file = setup_issue_dir

        # Create iteration with READY_FOR_REVIEW
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))

        # Setup mocks - has new commits
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "fix: new fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Title", "Body"))):
                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        # Assert - should update PR, not fetch comments
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        mock_dependencies["github_ops"].update_pr.assert_called_once()

    def test_new_commits_override_confirmed(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """New commits should update PR even if last iteration was CONFIRMED."""
        issue_dir, spec_file = setup_issue_dir

        # Create iteration with CONFIRMED
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_CONFIRMED"
        }))

        # Setup mocks - has new commits
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "fix: new fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Title", "Body"))):
                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        # Assert - should update PR
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        mock_dependencies["github_ops"].update_pr.assert_called_once()


class TestPRPhaseAgentCalled:
    """Test that _generate_pr_content (agent) is called during PR create/update."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("# Test Plan")

        return issue_dir, spec_file

    def test_create_pr_calls_generate_pr_content(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Creating a PR should call _generate_pr_content (agent)."""
        issue_dir, spec_file = setup_issue_dir

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "feat: add feature"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = None
        mock_dependencies["github_ops"].create_pr.return_value = "https://github.com/test/repo/pull/1"

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_generate_pr_content", return_value=None) as mock_generate:
                with patch.object(PRPhase, "_get_pr_title", return_value="Test PR Title"):
                    with patch.object(PRPhase, "_get_pr_body", return_value="Test PR Body"):
                        phase = PRPhase(
                            spec_file=str(spec_file),
                            issue_name="test-issue",
                            **mock_dependencies
                        )

                        result = phase._execute_github_mode()

        # Assert agent was called
        mock_generate.assert_called_once()
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"

    def test_update_pr_calls_generate_pr_content(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Updating a PR should call _generate_pr_content (agent) for new iteration."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001 with READY_FOR_REVIEW and output.md
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))
        (iteration_001 / "output.md").write_text("# Old PR Title\n\nOld body")

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "def5678", "message": "fix: bug fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_generate_pr_content", return_value=None) as mock_generate:
                with patch.object(PRPhase, "_get_pr_title", return_value="Updated PR Title"):
                    with patch.object(PRPhase, "_get_pr_body", return_value="Updated PR Body"):
                        phase = PRPhase(
                            spec_file=str(spec_file),
                            issue_name="test-issue",
                            **mock_dependencies
                        )

                        result = phase._execute_github_mode()

        # Assert agent was called for iteration 2
        mock_generate.assert_called_once()
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_READY_FOR_REVIEW"
        mock_dependencies["github_ops"].update_pr.assert_called_once()

        # Verify iteration_002 directory was created (agent writes to correct iteration)
        iteration_002 = pr_dir / "iteration_002"
        assert iteration_002.exists()

    def test_update_pr_uses_correct_iteration_number(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Updating PR should set self.iteration BEFORE calling _prepare_pr_content."""
        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001 with READY_FOR_REVIEW
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "end_time": "2026-01-27T10:05:00+08:00",
            "status_code": "CAFE_READY_FOR_REVIEW"
        }))
        (iteration_001 / "output.md").write_text("# Old PR Title\n\nOld body")

        # Setup mocks
        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "def5678", "message": "fix: bug fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 1,
            "url": "https://github.com/test/repo/pull/1"
        }

        captured_iteration = {}

        def mock_generate_side_effect():
            """Capture self.iteration at the time _generate_pr_content is called."""
            # Access phase.iteration via closure
            captured_iteration["value"] = phase.iteration
            return None

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_generate_pr_content", side_effect=mock_generate_side_effect) as mock_generate:
                with patch.object(PRPhase, "_get_pr_title", return_value="Updated PR"):
                    with patch.object(PRPhase, "_get_pr_body", return_value="Updated body"):
                        phase = PRPhase(
                            spec_file=str(spec_file),
                            issue_name="test-issue",
                            **mock_dependencies
                        )

                        result = phase._execute_github_mode()

        # Assert iteration was set to 2 BEFORE _generate_pr_content was called
        mock_generate.assert_called_once()
        assert captured_iteration["value"] == 2, (
            f"Expected iteration 2 when _generate_pr_content was called, got {captured_iteration['value']}"
        )


class TestCreateOrUpdatePRRecordsLastSeenCommentIds:
    """Test that _create_or_update_pr() records last_seen_comment_ids in context.json."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Setup basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)

        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True)
        spec_file.write_text("# Test Spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("# Test Plan")

        return issue_dir, spec_file

    def test_create_pr_records_last_seen_comment_ids_when_comments_exist(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Test 3.1: create PR 後 context.json 中有 last_seen_comment_ids（有 comments 的情況）

        情境：PR 建立成功，GitHub 上有現有 comments
        預期：context.json 包含 last_seen_comment_ids，記錄當前所有 comment IDs
        """
        from cafe.utils.github import PRComment

        issue_dir, spec_file = setup_issue_dir

        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "feat: add feature"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = None
        mock_dependencies["github_ops"].create_pr.return_value = "https://github.com/test/repo/pull/42"

        existing_comments = [
            PRComment(id="R1", body="舊 review comment", author="reviewer1",
                      created_at="2025-01-01T10:00:00Z", comment_type="review"),
            PRComment(id="T1", body="舊 timeline comment", author="maintainer",
                      created_at="2025-01-02T09:00:00Z", comment_type="timeline"),
        ]

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Test PR", "Test body"))):
                with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=existing_comments):
                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        assert result.status == PhaseStatus.COMPLETED

        context_file = issue_dir / "pr" / "iteration_001" / "context.json"
        assert context_file.exists()
        with open(context_file) as f:
            context = json.load(f)

        assert "last_seen_comment_ids" in context
        assert set(context["last_seen_comment_ids"]) == {"R1", "T1"}

    def test_create_pr_records_empty_last_seen_comment_ids_when_no_comments(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Test 3.2: create PR 後 context.json 中有空的 last_seen_comment_ids（沒有 comments 的情況）

        情境：PR 建立成功，GitHub 上沒有 comments（首次建立 PR）
        預期：context.json 包含空的 last_seen_comment_ids
        """
        from cafe.utils.github import GitHubError

        issue_dir, spec_file = setup_issue_dir

        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "abc1234", "message": "feat: add feature"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = None
        mock_dependencies["github_ops"].create_pr.return_value = "https://github.com/test/repo/pull/42"

        # No comments found
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Test PR", "Test body"))):
                with patch("cafe.phases.pr_phase.get_all_pr_comments",
                           side_effect=GitHubError("No comments found")):
                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        assert result.status == PhaseStatus.COMPLETED

        context_file = issue_dir / "pr" / "iteration_001" / "context.json"
        assert context_file.exists()
        with open(context_file) as f:
            context = json.load(f)

        # Should still have the field, just empty
        assert "last_seen_comment_ids" in context
        assert context["last_seen_comment_ids"] == []

    def test_update_pr_records_last_seen_comment_ids(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Test 3.3: update PR 後 context.json 中有 last_seen_comment_ids

        情境：PR 更新成功，GitHub 上有現有 comments
        預期：新 iteration 的 context.json 包含 last_seen_comment_ids
        """
        from cafe.utils.github import PRComment

        issue_dir, spec_file = setup_issue_dir

        # Create existing PR iteration_001
        pr_dir = issue_dir / "pr"
        iteration_001 = pr_dir / "iteration_001"
        iteration_001.mkdir(parents=True)
        (iteration_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "CAFE_READY_FOR_REVIEW",
            "last_seen_comment_ids": ["OLD1"]
        }))

        mock_dependencies["git_ops"].has_unpushed_commits.return_value = True
        mock_dependencies["git_ops"].get_unpushed_commits.return_value = [
            {"hash": "def5678", "message": "fix: bug fix"}
        ]
        mock_dependencies["github_ops"].check_gh_auth.return_value = True
        mock_dependencies["github_ops"].get_pr_for_branch.return_value = {
            "number": 42,
            "url": "https://github.com/test/repo/pull/42"
        }

        current_comments = [
            PRComment(id="OLD1", body="舊 comment", author="reviewer1",
                      created_at="2025-01-01T10:00:00Z", comment_type="review"),
            PRComment(id="NEW1", body="新 comment", author="reviewer2",
                      created_at="2025-01-03T10:00:00Z", comment_type="timeline"),
        ]

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch.object(PRPhase, "_prepare_pr_content", return_value=(None, ("Updated PR", "Updated body"))):
                with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=current_comments):
                    phase = PRPhase(
                        spec_file=str(spec_file),
                        issue_name="test-issue",
                        **mock_dependencies
                    )

                    result = phase._execute_github_mode()

        assert result.status == PhaseStatus.COMPLETED

        context_file = issue_dir / "pr" / "iteration_002" / "context.json"
        assert context_file.exists()
        with open(context_file) as f:
            context = json.load(f)

        assert "last_seen_comment_ids" in context
        assert set(context["last_seen_comment_ids"]) == {"OLD1", "NEW1"}


class TestSavePRCommentsFiltersLastSeen:
    """Test that _save_pr_comments_to_user_input() filters out previously seen comments."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        agent_manager = MagicMock()
        permission_handler = MagicMock()
        git_ops = MagicMock()
        github_ops = MagicMock()

        git_ops.get_current_branch.return_value = "test-issue"
        git_ops.get_repo_root.return_value = Path("/tmp")

        return {
            "agent_manager": agent_manager,
            "permission_handler": permission_handler,
            "git_ops": git_ops,
            "github_ops": github_ops,
        }

    def _make_phase(self, issue_dir, mock_dependencies, iteration=2):
        """Helper to create PRPhase instance with given iteration."""
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Test Spec")
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies
            )
            phase.iteration = iteration
            return phase

    def test_filters_out_previously_seen_comments(self, tmp_path, mock_dependencies):
        """Test 4.1: _save_pr_comments_to_user_input() 只儲存新 comments

        情境：上一輪 push 後記錄了 last_seen_comment_ids，現在有舊 comments 和新 comments
        預期：get_all_pr_comments 以 exclude_ids 呼叫，只有新 comment 被儲存到 user_input.md
        """
        from cafe.utils.github import PRComment
        from unittest.mock import call

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"

        # iteration_001: push iteration with last_seen_comment_ids
        pr_dir = issue_dir / "pr"
        iter_001 = pr_dir / "iteration_001"
        iter_001.mkdir(parents=True)
        (iter_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "CAFE_READY_FOR_REVIEW",
            "last_seen_comment_ids": ["OLD1", "OLD2"]
        }))

        # Mock returns only the new comment (simulating what get_all_pr_comments
        # actually returns after applying exclude_ids filtering)
        new_only = [
            PRComment(id="NEW1", body="新 comment 1", author="r3",
                      created_at="2025-01-02T10:00:00Z", comment_type="timeline"),
        ]

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=new_only) as mock_fetch:
                phase = self._make_phase(issue_dir, mock_dependencies, iteration=2)
                result = phase._save_pr_comments_to_user_input(42)

        assert result is not None
        user_input_file = pr_dir / "iteration_002" / "user_input.md"
        assert user_input_file.exists()
        content = user_input_file.read_text()
        assert "新 comment 1" in content

        # Verify get_all_pr_comments was called with the correct exclude_ids
        mock_fetch.assert_called_once_with(42, exclude_ids={"OLD1", "OLD2"})

    def test_returns_none_when_all_comments_previously_seen(self, tmp_path, mock_dependencies):
        """Test 4.2: 當所有 comments 都已看過時返回 None（沒有新 comments）

        情境：所有 GitHub comments 都在 last_seen_comment_ids 中，過濾後結果為空
        預期：返回 None，不建立 user_input.md
        """
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"

        pr_dir = issue_dir / "pr"
        iter_001 = pr_dir / "iteration_001"
        iter_001.mkdir(parents=True)
        (iter_001 / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "CAFE_READY_FOR_REVIEW",
            "last_seen_comment_ids": ["R1", "T1"]
        }))

        # Mock returns empty list (simulating all comments filtered out by exclude_ids)
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=[]) as mock_fetch:
                phase = self._make_phase(issue_dir, mock_dependencies, iteration=2)
                result = phase._save_pr_comments_to_user_input(42)

        assert result is None
        # user_input.md should NOT be created
        user_input_file = pr_dir / "iteration_002" / "user_input.md"
        assert not user_input_file.exists()

        # Verify get_all_pr_comments was called with the correct exclude_ids
        mock_fetch.assert_called_once_with(42, exclude_ids={"R1", "T1"})

    def test_first_iteration_no_filter_all_comments_included(self, tmp_path, mock_dependencies):
        """Test 4.3: 第一輪 iteration（沒有 last_seen_comment_ids）時包含所有 comments

        情境：沒有前一輪記錄，first iteration
        預期：所有 comments 都被儲存，無過濾
        """
        from cafe.utils.github import PRComment

        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        # No pr/ directory exists yet (first iteration)

        all_comments = [
            PRComment(id="R1", body="review comment", author="reviewer",
                      created_at="2025-01-01T10:00:00Z", comment_type="review"),
            PRComment(id="T1", body="timeline comment", author="maintainer",
                      created_at="2025-01-01T11:00:00Z", comment_type="timeline"),
        ]

        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=all_comments):
                phase = self._make_phase(issue_dir, mock_dependencies, iteration=1)
                result = phase._save_pr_comments_to_user_input(42)

        assert result is not None
        pr_dir = issue_dir / "pr"
        user_input_file = pr_dir / "iteration_001" / "user_input.md"
        assert user_input_file.exists()
        content = user_input_file.read_text()
        assert "review comment" in content
        assert "timeline comment" in content
