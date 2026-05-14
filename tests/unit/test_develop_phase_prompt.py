"""Test develop phase prompt generation with review feedback and PR comments."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import PhaseStatus
from cafe.phases.develop_phase import DevelopPhase


def _write_review_iteration(
    issue_dir: Path,
    *,
    iteration: int = 1,
    status_code: str = "needs_changes",
    timestamp: str = "2026-01-07T11:39:41+08:00",
    end_time: str | None = None,
    output: str = "## Todo List\n\n- [ ] Review issue",
) -> None:
    review_dir = issue_dir / "review" / f"iteration_{iteration:03d}"
    review_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "iteration": iteration,
        "timestamp": timestamp,
        "status_code": status_code,
    }
    if end_time is not None:
        payload["end_time"] = end_time
    (review_dir / "context.json").write_text(json.dumps(payload))
    (review_dir / "output.md").write_text(output)


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
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T11:39:41+08:00",
            output="Please fix commit message format",
        )

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

    def test_prompt_uses_review_iteration_context_without_status_file(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            end_time="2026-01-07T11:40:00+08:00",
            output="Please fix review issue from iteration context",
        )

        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )
            phase._check_if_already_completed_with_review()
            phase.iteration = 1
            prompt = phase._generate_prompt(user_input="")

        assert "checklist.md" in prompt.lower()

    def test_pr_feedback_handled_via_checklist_when_no_review_feedback(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR feedback is handled via checklist (not in prompt) when no review feedback exists."""
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

        # Create PR iteration with output.md containing todo list
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create output.md with todo list
        output_file = pr_iteration_dir / "output.md"
        output_file.write_text("## Todo List\n\n- [ ] Fix issue 1\n- [ ] Fix issue 2")

        # Create context.json for PR iteration with status_code
        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": "needs_changes"
        }))

        # Mock get_agent_file_path
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Need to set iteration before calling _generate_prompt
            phase.iteration = 1

            # Act: Generate prompt
            prompt = phase._generate_prompt(user_input="")

            # Assert: PR feedback should NOT be directly in prompt (it's in checklist)
            # The prompt should reference the checklist for feedback
            assert "checklist.md" in prompt.lower()
            assert "Fix issue 1" not in prompt
            assert "Fix issue 2" not in prompt

    def test_pr_feedback_handled_via_checklist_when_review_approved(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR feedback is handled via checklist even when review is APPROVED."""
        # Arrange: Create directory structure
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback with APPROVED status
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="confirmed",
            timestamp="2026-01-07T11:39:41+08:00",
            output="Looks good!",
        )

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Create PR iteration with output.md containing todo list
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        # Create output.md with todo list
        output_file = pr_iteration_dir / "output.md"
        output_file.write_text("## Todo List\n\n- [ ] Address PR comment 1")

        # Create context.json for PR iteration with status_code
        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": "needs_changes"
        }))

        # Mock get_agent_file_path
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Need to set iteration before calling _generate_prompt
            phase.iteration = 1

            # Act: Generate prompt
            prompt = phase._generate_prompt(user_input="")

            # Assert: PR feedback should be handled via checklist, not in prompt
            assert "checklist.md" in prompt.lower()
            assert "Address PR comment 1" not in prompt

    def test_execute_passes_correct_feedback_file_path_to_checklist_generator(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that DevelopPhase.execute passes the correct feedback_file_path to generate_develop_checklist."""
        from unittest.mock import patch, call

        # Arrange: Create directory structure with review feedback
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T11:39:41+08:00",
            output="## Todo List\n\n- [ ] Fix issue",
        )

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Mock get_agent_file_path and generate_develop_checklist
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)), \
             patch('cafe.utils.checklist_generator.generate_develop_checklist') as mock_gen_checklist, \
             patch.object(DevelopPhase, '_execute_and_handle_agent_response', return_value=(None, "confirmed")):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Override issue_dir to use tmp_path (since git operations use CWD)
            phase.issue_dir = issue_dir
            phase.phase_dir = issue_dir / "develop"
            phase.history_dir = phase.phase_dir / "history"

            # Act: Execute (will call generate_develop_checklist)
            try:
                phase.execute()
            except:
                pass  # Ignore execution errors, we just want to verify the call

            # Assert: generate_develop_checklist was called with feedback_file_path
            assert mock_gen_checklist.called
            call_kwargs = mock_gen_checklist.call_args.kwargs
            assert 'feedback_file_path' in call_kwargs
            assert call_kwargs['feedback_file_path'] is not None
            assert 'review' in call_kwargs['feedback_file_path']

    def test_pr_feedback_used_when_pr_end_time_is_newer(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that PR feedback is used when PR end_time is newer than review end_time."""
        from unittest.mock import patch

        # Arrange: Create directory structure with both review and PR feedback
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback (older end_time)
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T11:39:41+08:00",
            end_time="2026-01-07T11:40:00+08:00",
            output="## Todo List\n\n- [ ] Review issue",
        )

        # Create PR feedback (newer end_time — should take priority)
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        pr_output = pr_iteration_dir / "output.md"
        pr_output.write_text("## Todo List\n\n- [ ] PR issue")

        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": "needs_changes",
            "end_time": "2026-01-07T11:50:00+08:00",
        }))

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Mock get_agent_file_path and generate_develop_checklist
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)), \
             patch('cafe.utils.checklist_generator.generate_develop_checklist') as mock_gen_checklist, \
             patch.object(DevelopPhase, '_execute_and_handle_agent_response', return_value=(None, "confirmed")):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Override issue_dir to use tmp_path (since git operations use CWD)
            phase.issue_dir = issue_dir
            phase.phase_dir = issue_dir / "develop"
            phase.history_dir = phase.phase_dir / "history"

            # Act: Execute
            try:
                phase.execute()
            except:
                pass

            # Assert: PR feedback path should be used (PR end_time is newer)
            assert mock_gen_checklist.called
            call_kwargs = mock_gen_checklist.call_args.kwargs
            assert 'feedback_file_path' in call_kwargs
            assert call_kwargs['feedback_file_path'] is not None
            assert 'pr' in call_kwargs['feedback_file_path']
            assert 'review' not in call_kwargs['feedback_file_path']

    def test_review_feedback_takes_priority_when_review_end_time_is_newer(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that review feedback takes priority when review end_time is newer than PR end_time."""
        from unittest.mock import patch

        # Arrange: Create directory structure with both review and PR feedback
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback (newer end_time — should take priority)
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T11:58:24+08:00",
            end_time="2026-01-07T11:58:24+08:00",
            output="## Todo List\n\n- [ ] Review issue",
        )

        # Create PR feedback (older end_time)
        pr_dir = issue_dir / "pr"
        pr_iteration_dir = pr_dir / "iteration_001"
        pr_iteration_dir.mkdir(parents=True, exist_ok=True)

        pr_output = pr_iteration_dir / "output.md"
        pr_output.write_text("## Todo List\n\n- [ ] PR issue")

        pr_context_file = pr_iteration_dir / "context.json"
        pr_context_file.write_text(json.dumps({
            "iteration": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": "needs_changes"
        }))

        # Create PR status.json with older end_time
        pr_status_file = pr_dir / "status.json"
        pr_status_file.write_text(json.dumps({
            "status_code": "needs_changes",
            "end_time": "2026-01-07T11:42:12+08:00"
        }))

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Mock get_agent_file_path and generate_develop_checklist
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)), \
             patch('cafe.utils.checklist_generator.generate_develop_checklist') as mock_gen_checklist, \
             patch.object(DevelopPhase, '_execute_and_handle_agent_response', return_value=(None, "confirmed")):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Override issue_dir to use tmp_path (since git operations use CWD)
            phase.issue_dir = issue_dir
            phase.phase_dir = issue_dir / "develop"
            phase.history_dir = phase.phase_dir / "history"

            # Act: Execute
            try:
                phase.execute()
            except:
                pass

            # Assert: Review feedback path should be used (review end_time is newer)
            assert mock_gen_checklist.called
            call_kwargs = mock_gen_checklist.call_args.kwargs
            assert 'feedback_file_path' in call_kwargs
            assert call_kwargs['feedback_file_path'] is not None
            assert 'review' in call_kwargs['feedback_file_path']

    def test_review_feedback_used_when_no_pr_feedback(
        self, tmp_path, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """Test that review feedback is used when no PR feedback exists."""
        from unittest.mock import patch

        # Arrange: Create directory structure with only review feedback (no PR)
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        # Create review feedback
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T11:39:41+08:00",
            output="## Todo List\n\n- [ ] Review issue",
        )

        # NO PR feedback

        # Create agent file
        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        # Mock get_agent_file_path and generate_develop_checklist
        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(agent_file)), \
             patch('cafe.utils.checklist_generator.generate_develop_checklist') as mock_gen_checklist, \
             patch.object(DevelopPhase, '_execute_and_handle_agent_response', return_value=(None, "confirmed")):

            # Create phase
            phase = DevelopPhase(
                agent_manager=mock_agent_manager,
                permission_handler=mock_permission_handler,
                git_ops=mock_git_ops,
                spec_file=str(spec_file),
                plan_file=str(plan_file),
                issue_name="test-issue",
            )

            # Override issue_dir to use tmp_path (since git operations use CWD)
            phase.issue_dir = issue_dir
            phase.phase_dir = issue_dir / "develop"
            phase.history_dir = phase.phase_dir / "history"

            # Act: Execute
            try:
                phase.execute()
            except:
                pass

            # Assert: Review feedback path should be used
            assert mock_gen_checklist.called
            call_kwargs = mock_gen_checklist.call_args.kwargs
            assert 'feedback_file_path' in call_kwargs
            assert call_kwargs['feedback_file_path'] is not None
            assert 'review' in call_kwargs['feedback_file_path']


class TestReviewFeedbackDetectionByEndTime:
    """Test that review feedback handling uses end_time comparison instead of handled_review_timestamp."""

    @pytest.fixture
    def mock_git_ops(self):
        git_ops = MagicMock()
        git_ops.has_unpushed_commits.return_value = False
        git_ops.get_latest_unpushed_commit_timestamp.return_value = None
        git_ops.branch_exists.return_value = True
        git_ops.get_current_branch.return_value = "test-issue"
        return git_ops

    @pytest.fixture
    def mock_agent_manager(self):
        return MagicMock()

    @pytest.fixture
    def mock_permission_handler(self):
        return MagicMock()

    @pytest.fixture
    def setup_issue_dir(self, tmp_path):
        """Create basic issue directory structure."""
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("Test spec")

        plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("Test plan")

        agent_dir = tmp_path / ".cafe" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "test-dev.md"
        agent_file.write_text("Test developer agent")

        return {
            "issue_dir": issue_dir,
            "spec_file": spec_file,
            "plan_file": plan_file,
            "agent_file": agent_file,
        }

    def _create_phase(self, setup, mock_git_ops, mock_agent_manager, mock_permission_handler):
        """Helper to create a DevelopPhase instance."""
        phase = DevelopPhase(
            agent_manager=mock_agent_manager,
            permission_handler=mock_permission_handler,
            git_ops=mock_git_ops,
            spec_file=str(setup["spec_file"]),
            plan_file=str(setup["plan_file"]),
            issue_name="test-issue",
        )
        phase.issue_dir = setup["issue_dir"]
        phase.phase_dir = setup["issue_dir"] / "develop"
        phase.history_dir = phase.phase_dir / "history"
        return phase

    def test_review_already_handled_when_develop_end_time_newer(
        self, tmp_path, setup_issue_dir, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """When develop end_time > review end_time, review feedback is already handled."""
        setup = setup_issue_dir
        issue_dir = setup["issue_dir"]

        # Create review feedback with NEEDS_CHANGES
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T10:00:00+08:00",
            end_time="2026-01-07T10:05:00+08:00",
        )

        # Create develop status with end_time AFTER review
        develop_dir = issue_dir / "develop"
        develop_status_file = develop_dir / "status.json"
        develop_status_file.parent.mkdir(parents=True, exist_ok=True)
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "status_code": "confirmed",
            "timestamp": "2026-01-07T10:10:00+08:00",
            "iteration": 3,
            "message": "Phase completed with confirmed",
            "end_time": "2026-01-07T10:15:00+08:00",
        }))

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(setup["agent_file"])):
            phase = self._create_phase(setup, mock_git_ops, mock_agent_manager, mock_permission_handler)
            result = phase._check_if_already_completed_with_review()

        # Should return completed (review feedback already handled)
        assert result is not None
        assert result.status == PhaseStatus.COMPLETED

    def test_review_needs_handling_when_review_end_time_newer(
        self, tmp_path, setup_issue_dir, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """When review end_time > develop end_time, review feedback needs handling."""
        setup = setup_issue_dir
        issue_dir = setup["issue_dir"]

        # Create review feedback with NEEDS_CHANGES, end_time AFTER develop
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T10:20:00+08:00",
            end_time="2026-01-07T10:25:00+08:00",
        )

        # Create develop status with end_time BEFORE review
        develop_dir = issue_dir / "develop"
        develop_status_file = develop_dir / "status.json"
        develop_status_file.parent.mkdir(parents=True, exist_ok=True)
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "status_code": "confirmed",
            "timestamp": "2026-01-07T10:10:00+08:00",
            "iteration": 3,
            "message": "Phase completed with confirmed",
            "end_time": "2026-01-07T10:15:00+08:00",
        }))

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(setup["agent_file"])):
            phase = self._create_phase(setup, mock_git_ops, mock_agent_manager, mock_permission_handler)
            result = phase._check_if_already_completed_with_review()

        # Should return None (need to handle review feedback)
        assert result is None
        assert phase._has_review_feedback is True

    def test_review_needs_handling_from_iteration_context_without_status_file(
        self, tmp_path, setup_issue_dir, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        setup = setup_issue_dir
        issue_dir = setup["issue_dir"]

        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T10:20:00+08:00",
            end_time="2026-01-07T10:25:00+08:00",
        )

        develop_dir = issue_dir / "develop"
        develop_status_file = develop_dir / "status.json"
        develop_status_file.parent.mkdir(parents=True, exist_ok=True)
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "status_code": "confirmed",
            "timestamp": "2026-01-07T10:10:00+08:00",
            "iteration": 3,
            "message": "Phase completed with confirmed",
            "end_time": "2026-01-07T10:15:00+08:00",
        }))

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(setup["agent_file"])):
            phase = self._create_phase(setup, mock_git_ops, mock_agent_manager, mock_permission_handler)
            result = phase._check_if_already_completed_with_review()

        assert result is None
        assert phase._has_review_feedback is True

    def test_review_needs_handling_when_develop_has_no_end_time(
        self, tmp_path, setup_issue_dir, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """When develop has no end_time, assume review feedback needs handling."""
        setup = setup_issue_dir
        issue_dir = setup["issue_dir"]

        # Create review feedback with NEEDS_CHANGES
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T10:20:00+08:00",
            end_time="2026-01-07T10:25:00+08:00",
        )

        # Create develop status WITHOUT end_time
        develop_dir = issue_dir / "develop"
        develop_status_file = develop_dir / "status.json"
        develop_status_file.parent.mkdir(parents=True, exist_ok=True)
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "status_code": "confirmed",
            "timestamp": "2026-01-07T10:10:00+08:00",
            "iteration": 3,
            "message": "Phase completed with confirmed",
        }))

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(setup["agent_file"])):
            phase = self._create_phase(setup, mock_git_ops, mock_agent_manager, mock_permission_handler)
            result = phase._check_if_already_completed_with_review()

        # Should return None (can't determine, assume needs handling)
        assert result is None

    def test_review_needs_handling_when_review_has_no_end_time(
        self, tmp_path, setup_issue_dir, mock_git_ops, mock_agent_manager, mock_permission_handler
    ):
        """When review has no end_time, assume review feedback needs handling."""
        setup = setup_issue_dir
        issue_dir = setup["issue_dir"]

        # Create review feedback with NEEDS_CHANGES but NO end_time
        _write_review_iteration(
            issue_dir,
            iteration=1,
            status_code="needs_changes",
            timestamp="2026-01-07T10:20:00+08:00",
        )

        # Create develop status with end_time
        develop_dir = issue_dir / "develop"
        develop_status_file = develop_dir / "status.json"
        develop_status_file.parent.mkdir(parents=True, exist_ok=True)
        develop_status_file.write_text(json.dumps({
            "phase": "develop",
            "status": "completed",
            "status_code": "confirmed",
            "timestamp": "2026-01-07T10:10:00+08:00",
            "iteration": 3,
            "message": "Phase completed with confirmed",
            "end_time": "2026-01-07T10:15:00+08:00",
        }))

        with patch('cafe.agents.manager.AgentManager.get_agent_file_path', return_value=str(setup["agent_file"])):
            phase = self._create_phase(setup, mock_git_ops, mock_agent_manager, mock_permission_handler)
            result = phase._check_if_already_completed_with_review()

        # Should return None (can't determine, assume needs handling)
        assert result is None
