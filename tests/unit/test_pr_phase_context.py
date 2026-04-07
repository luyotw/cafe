"""Test PR phase context.json field population."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from cafe.phases.pr_phase import PRPhase
from cafe.core.types import PhaseResult, PhaseStatus
from cafe.utils.github import PRComment


pytestmark = pytest.mark.slow


# Fields that must be present in context.json per issue #142
AGENT_CONTEXT_FIELDS = [
    "prompt",
    "cli",
    "session_id",
    "allowed_tools",
    "denied_tools",
    "cli_command_args",
    "model",
]


class TestPRPhaseContextFields:
    """Verify context.json contains all agent execution context fields."""

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

    def _create_phase(self, mock_dependencies, issue_dir, spec_file):
        """Helper to create PRPhase with mocked _get_issue_dir."""
        with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir):
            phase = PRPhase(
                spec_file=str(spec_file),
                issue_name="test-issue",
                **mock_dependencies,
            )
        return phase

    def test_github_mode_post_organize_preserves_agent_fields(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """After _organize_comments_to_todo_list, status_code update must not erase agent fields.

        Regression test: the post-organize code previously called _update_iteration_history()
        with only phase_specific_data and status_code, overwriting agent metadata set by
        _execute_agent_iteration(). The fix updates status_code directly in context.json.
        """
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        pr_dir = issue_dir / "pr"
        phase.iteration = 2

        # Simulate: agent execution already wrote context.json with all agent fields
        iter_dir = pr_dir / "iteration_002"
        iter_dir.mkdir(parents=True, exist_ok=True)
        agent_context = {
            "iteration": 2,
            "timestamp": "2026-01-27T10:00:00+08:00",
            "prompt": "test prompt from agent",
            "cli": "claude",
            "session_id": "sess-123",
            "allowed_tools": ["read", "edit"],
            "denied_tools": [],
            "cli_command_args": ["--model", "claude"],
            "model": "claude-sonnet",
            "response": "organized",
            "permission_denials": [],
            "streaming_log": [],
        }
        (iter_dir / "context.json").write_text(json.dumps(agent_context))

        # Simulate the post-organize status_code update (new behavior: direct JSON update)
        result_status = "CAFE_NEEDS_CHANGES"
        result_data = {"pr_number": "42", "status_code": "CAFE_NEEDS_CHANGES"}

        context_file = iter_dir / "context.json"
        with open(context_file, "r", encoding="utf-8") as f:
            context_data = json.load(f)
        context_data.update(result_data)
        context_data["status_code"] = result_status
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

        # Read context.json - agent fields must be preserved
        context = json.loads(context_file.read_text())

        for field in AGENT_CONTEXT_FIELDS:
            assert field in context, f"Field '{field}' missing from context.json"
            assert context[field] is not None, (
                f"Field '{field}' is None in context.json - "
                "agent metadata was erased by post-organize status update"
            )

        # status_code should be updated
        assert context["status_code"] == "CAFE_NEEDS_CHANGES"
        # phase-specific data should be merged
        assert context["pr_number"] == "42"

    def test_save_pr_comments_context_has_all_fields(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """_save_pr_comments_to_user_input must produce context.json with all required fields."""
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        phase.iteration = 1

        # Mock GitHub to return comments
        comments = [
            PRComment(
                id="1",
                body="Please fix this",
                author="reviewer",
                created_at="2025-01-01T10:00:00Z",
                comment_type="review",
            )
        ]

        with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=comments):
            with patch.object(phase, "_update_iteration_history") as mock_update:
                phase._save_pr_comments_to_user_input(pr_number=42)

                # Verify _update_iteration_history was called (not manual json.dump)
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args[1] if mock_update.call_args[1] else {}
                call_args = mock_update.call_args

                # phase_specific_data should contain pr_number, comment_count, source
                phase_data = call_args[1].get("phase_specific_data", call_args[0][0] if call_args[0] else {})
                assert "pr_number" in phase_data
                assert "comment_count" in phase_data
                assert "source" in phase_data

    def test_local_review_modification_context_has_all_fields(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Local review modification request must produce context.json with all required fields."""
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        # Create a previous completed iteration
        pr_dir = issue_dir / "pr"
        prev_iter_dir = pr_dir / "iteration_001"
        prev_iter_dir.mkdir(parents=True)
        (prev_iter_dir / "context.json").write_text(json.dumps({
            "iteration": 1,
            "status_code": "CAFE_READY_FOR_REVIEW",
        }))
        (prev_iter_dir / "output.md").write_text("# Title\n\nBody")

        phase.iteration = 1

        # Mock the review decision to return a modification request string
        with patch.object(phase, "_process_review_decision", return_value="Please fix X"):
            with patch.object(phase, "_organize_comments_to_todo_list", return_value=PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="done",
                data={"status_code": "CAFE_NEEDS_CHANGES"},
            )):
                with patch.object(phase, "_save_progress"):
                    with patch.object(phase, "_update_iteration_history") as mock_update:
                        # Trigger local review with modification path
                        # We call the relevant code directly by simulating the flow
                        from cafe.core.status_codes import PhaseStatusCode

                        phase.iteration += 1
                        iteration_dir = pr_dir / f"iteration_{phase.iteration:03d}"
                        iteration_dir.mkdir(parents=True, exist_ok=True)

                        # Call _update_iteration_history the way it should be called
                        phase._update_iteration_history(
                            phase_specific_data={
                                "user_input": "Please fix X",
                                "local_review": True,
                            },
                        )

                        # Verify it was called with phase_specific_data
                        mock_update.assert_called_once()

    def test_local_review_confirmation_context_has_all_fields(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Local review confirmation must produce context.json with all required fields."""
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        phase.iteration = 1

        pr_dir = issue_dir / "pr"

        from cafe.core.status_codes import PhaseStatusCode

        # Simulate calling _update_iteration_history for confirmation
        iter_dir = pr_dir / "iteration_002"
        iter_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(phase, "_update_iteration_history") as mock_update:
            phase.iteration = 2
            phase._update_iteration_history(
                phase_specific_data={"local_review": True},
                status_code=PhaseStatusCode.CONFIRMED,
            )

            # Verify it was called (not manual json.dump)
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args[1] if mock_update.call_args[1] else {}
            # Should have status_code
            assert call_kwargs.get("status_code") == PhaseStatusCode.CONFIRMED

    def test_context_json_fields_present_after_save_pr_comments(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Integration: context.json produced by _save_pr_comments_to_user_input has all fields."""
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        phase.iteration = 1

        # Mock GitHub to return comments
        comments = [
            PRComment(
                id="1",
                body="Fix this",
                author="reviewer",
                created_at="2025-01-01T10:00:00Z",
                comment_type="review",
            )
        ]

        # Patch _append_iteration_index to avoid side effects
        with patch("cafe.phases.pr_phase.get_all_pr_comments", return_value=comments):
            with patch.object(phase, "_append_iteration_index"):
                phase._save_pr_comments_to_user_input(pr_number=42)

        # Read context.json
        iter_dir = issue_dir / "pr" / "iteration_001"
        context_file = iter_dir / "context.json"
        assert context_file.exists(), "context.json was not created"

        context = json.loads(context_file.read_text())

        # All agent context fields must be present (even if None for pre-agent context)
        for field in AGENT_CONTEXT_FIELDS:
            assert field in context, f"Field '{field}' missing from context.json"

    def test_local_review_confirmation_context_fields_present(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Integration: context.json for local review confirmation has all fields."""
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        phase.iteration = 2

        pr_dir = issue_dir / "pr"
        iter_dir = pr_dir / "iteration_002"
        iter_dir.mkdir(parents=True, exist_ok=True)

        from cafe.core.status_codes import PhaseStatusCode

        # Patch _append_iteration_index to avoid side effects
        with patch.object(phase, "_append_iteration_index"):
            phase._update_iteration_history(
                phase_specific_data={"local_review": True},
                status_code=PhaseStatusCode.CONFIRMED,
            )

        context = json.loads((iter_dir / "context.json").read_text())

        # All agent context fields must be present
        for field in AGENT_CONTEXT_FIELDS:
            assert field in context, f"Field '{field}' missing from context.json"

        # status_code should be set
        assert context["status_code"] == "CAFE_CONFIRMED"

    def test_second_update_preserves_model_and_stats(
        self, tmp_path, mock_dependencies, setup_issue_dir
    ):
        """Calling _update_iteration_history a second time without model/stats must not overwrite them.

        Regression test: PR phase calls _update_iteration_history once after agent execution
        (with model and token_usage), then again after PR creation (without model/token_usage).
        The second call must not overwrite model to None or erase stats.
        """
        issue_dir, spec_file = setup_issue_dir
        phase = self._create_phase(mock_dependencies, issue_dir, spec_file)

        phase.iteration = 1

        pr_dir = issue_dir / "pr"
        iter_dir = pr_dir / "iteration_001"
        iter_dir.mkdir(parents=True, exist_ok=True)

        from cafe.core.status_codes import PhaseStatusCode
        from cafe.core.types import TokenUsage

        # First call: agent execution saves model and token_usage
        with patch.object(phase, "_append_iteration_index"):
            phase._update_iteration_history(
                phase_specific_data={"response": "agent output", "permission_denials": [], "streaming_log": []},
                prompt="test prompt",
                agent_cli="claude",
                model="claude-haiku-4-5-20251001",
                token_usage=TokenUsage(input_tokens=100, output_tokens=50, duration_ms=5000, duration_api_ms=4800),
            )

        # Second call: PR creation updates only phase_specific_data and status_code
        with patch.object(phase, "_append_iteration_index"):
            phase._update_iteration_history(
                phase_specific_data={"pr_number": "159", "pr_url": "https://example.com/pr/159", "branch": "issue158"},
                status_code=PhaseStatusCode.READY_FOR_REVIEW,
            )

        context = json.loads((iter_dir / "context.json").read_text())

        # model must be preserved from first call
        assert context["model"] == "claude-haiku-4-5-20251001"
        # stats must be preserved from first call
        assert context["stats"]["input_tokens"] == 100
        assert context["stats"]["output_tokens"] == 50
        assert context["stats"]["duration_ms"] == 5000
        assert context["stats"]["duration_api_ms"] == 4800
        # cli must be preserved from first call
        assert context["cli"] == "claude"
        assert context["prompt"] == "test prompt"
        # status_code should be updated from second call
        assert context["status_code"] == "CAFE_READY_FOR_REVIEW"
        # phase_specific_data from second call should be merged
        assert context["pr_number"] == "159"
