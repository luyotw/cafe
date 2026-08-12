"""Contract tests for declared feedback and local-review context hooks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.core.blackboard import BlackboardStore
from cafe.core.hooks.feedback import GitHubPRFeedbackSource, LocalReviewContextProvider
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.utils.github import PRComment


class _Phase:
    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.phase_dir = issue_dir / "pr"
        self.phase_name = "pr"
        self.git_ops = MagicMock()
        self.step_user_inputs: dict[str, str] = {}


def test_github_feedback_source_records_new_unresolved_comments_before_resume(tmp_path: Path) -> None:
    """UT-003/IT-001 — declared intake records durable feedback before exposing it."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-348"
    phase = _Phase(issue_dir)
    phase.git_ops.get_current_branch.return_value = "issue-348"
    state = BlackboardStore(issue_dir).load_or_create("pr")
    comment = PRComment(
        id="comment-1",
        body="Add the missing validation.",
        author="reviewer",
        created_at="2026-08-12T00:00:00Z",
    )

    with (
        patch("cafe.core.hooks.feedback.GitHubOps") as github_ops,
        patch("cafe.core.hooks.feedback.get_all_pr_comments", return_value=[comment]),
    ):
        github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 348,
            "url": "https://example.test/pr/348",
        }
        result = GitHubPRFeedbackSource().run(
            stage="prepare_input",
            phase=phase,
            step_name="pr",
            blackboard_state=state,
        )

    assert result.context_updates["pr_number"] == "348"
    assert result.context_updates["user_input"]
    assert phase.step_user_inputs["pr"] == result.context_updates["user_input"]
    assert result.events == [{"type": "workflow_feedback_recorded", "count": 1, "pr_number": "348"}]
    assert len(WorkflowFeedbackLedger(issue_dir).pending(target_step="develop")) == 1
    assert state.artifacts["workflow_feedback"].path.endswith("workflow_feedback.json")


def test_local_review_context_provider_is_generic_and_context_only(tmp_path: Path) -> None:
    """UT-005 — the provider snapshots generic context and only requests confirmation."""
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-348"
    phase = _Phase(issue_dir)
    phase.git_ops.get_diff.return_value = "diff --git a/app.py b/app.py\n+change\n"
    state = BlackboardStore(issue_dir).load_or_create("publish")
    iteration_dir = issue_dir / "publish" / "iteration_001"
    iteration_dir.mkdir(parents=True)

    result = LocalReviewContextProvider().run(
        stage="publish_output",
        phase=phase,
        step_name="publish",
        iteration_dir=iteration_dir,
        blackboard_state=state,
        context={"review_required": "true", "review_base": "main", "review_head": "HEAD"},
    )

    phase.git_ops.get_diff.assert_called_once_with("main", "HEAD")
    assert result.override_status_code == PhaseStatusCode.CONFIRM_OUTPUT
    assert result.events == [{"type": "local_review_context_ready"}]
    assert (iteration_dir / "local_review_context.md").exists()
    assert "local_review_context" in state.artifacts
    assert not (iteration_dir / "user_input.md").exists()
