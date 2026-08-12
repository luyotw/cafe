"""Declarable, read-only feedback intake and local review context hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.hooks import HookResult, NoOpHook
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workflow_feedback import WorkflowFeedbackError, WorkflowFeedbackLedger
from cafe.utils.github import GitHubOps, get_all_pr_comments


def _comment_value(comment: Any, name: str, default: Any = "") -> Any:
    if isinstance(comment, dict):
        return comment.get(name, default)
    return getattr(comment, name, default)


def _register_artifact(
    *,
    phase: Any,
    blackboard_state: Any,
    name: str,
    path: Path,
    updated_by: str,
) -> None:
    """Register a hook-produced document using the existing blackboard boundary."""
    if blackboard_state is None:
        return
    previous = getattr(blackboard_state, "artifacts", {}).get(name)
    entry = ArtifactEntry(
        name=name,
        kind=ArtifactKind.DOCUMENT,
        version=(previous.version + 1) if previous else 1,
        updated_by=updated_by,
        path=str(path),
    )
    issue_dir = getattr(phase, "issue_dir", None)
    if not isinstance(issue_dir, Path):
        return
    BlackboardStore(issue_dir).put_artifact(blackboard_state, entry)


class GitHubPRFeedbackSource(NoOpHook):
    """Read and persist GitHub feedback without owning any GitHub mutation."""

    name = "GitHubPRFeedbackSource"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "prepare_input":
            return HookResult()
        phase = kwargs.get("phase")
        if phase is None:
            return HookResult()
        try:
            branch = phase.git_ops.get_current_branch()
            existing_pr = GitHubOps().get_pr_for_branch(branch) if branch else None
        except Exception:
            return HookResult()
        if not existing_pr:
            return HookResult()

        pr_number = str(existing_pr.get("number") or "").strip()
        context_updates = {
            "pr_number": pr_number,
            "pr_url": str(existing_pr.get("url") or ""),
        }
        if not pr_number:
            return HookResult(context_updates=context_updates)
        behavior = kwargs.get("step_def") or {}
        if isinstance(behavior, dict):
            behavior = behavior.get("behavior") or {}
        target_step = (
            str(behavior.get("feedback_target") or "develop")
            if isinstance(behavior, dict)
            else "develop"
        )

        try:
            comments = get_all_pr_comments(int(pr_number))
            ledger = WorkflowFeedbackLedger(phase.issue_dir)
            resolved = {
                f"github-pr:{pr_number}:{_comment_value(comment, 'id')}"
                for comment in comments
                if bool(_comment_value(comment, "is_resolved", False))
                and str(_comment_value(comment, "id")).strip()
            }
            ledger.reconcile_resolved(resolved)
            new_comments: list[Any] = []
            for comment in comments:
                comment_id = str(_comment_value(comment, "id")).strip()
                body = str(_comment_value(comment, "body")).strip()
                if not comment_id or not body or bool(_comment_value(comment, "is_resolved", False)):
                    continue
                created, _entry = ledger.record(
                    source_identity=f"github-pr:{pr_number}:{comment_id}",
                    source_kind=str(_comment_value(comment, "comment_type", "github_pr")),
                    target_step=target_step,
                    content=body,
                )
                if created:
                    new_comments.append(comment)
        except (WorkflowFeedbackError, OSError, ValueError):
            return HookResult(
                context_updates=context_updates,
                events=[{"type": "workflow_feedback_unavailable", "pr_number": pr_number}],
            )
        except Exception:
            return HookResult(
                context_updates=context_updates,
                events=[{"type": "workflow_feedback_read_failed", "pr_number": pr_number}],
            )

        _register_artifact(
            phase=phase,
            blackboard_state=kwargs.get("blackboard_state"),
            name=WorkflowFeedbackLedger.artifact_name,
            path=ledger.path,
            updated_by=self.name,
        )
        if not new_comments:
            return HookResult(context_updates=context_updates)
        feedback_text = "\n\n".join(str(_comment_value(comment, "body")).strip() for comment in new_comments)
        step_name = kwargs.get("step_name")
        if isinstance(step_name, str) and step_name:
            phase.step_user_inputs[step_name] = feedback_text
        return HookResult(
            context_updates={
                **context_updates,
                "user_input": feedback_text,
                "pr_comment_count": str(len(new_comments)),
                "pr_mode": "comments",
            },
            events=[
                {
                    "type": "workflow_feedback_recorded",
                    "count": len(new_comments),
                    "pr_number": pr_number,
                }
            ],
        )


class LocalReviewContextProvider(NoOpHook):
    """Prepare generic review context and request the declared HumanTask pause."""

    name = "LocalReviewContextProvider"

    def run(self, **kwargs: Any) -> HookResult:
        if kwargs.get("stage") != "publish_output":
            return HookResult()
        context = kwargs.get("context") or {}
        required = str(context.get("review_required") or "").strip().lower()
        if required not in {"1", "true", "yes"}:
            return HookResult()
        phase = kwargs.get("phase")
        iteration_dir = kwargs.get("iteration_dir")
        if phase is None or not isinstance(iteration_dir, Path):
            return HookResult()
        base = str(context.get("review_base") or "").strip()
        head = str(context.get("review_head") or "").strip()
        if not base or not head:
            return HookResult(
                events=[{"type": "local_review_context_unavailable"}],
                artifact_ready=False,
            )
        try:
            diff = phase.git_ops.get_diff(base, head)
        except Exception:
            return HookResult(
                events=[{"type": "local_review_context_unavailable"}],
                artifact_ready=False,
            )
        path = iteration_dir / "local_review_context.md"
        path.write_text(str(diff), encoding="utf-8")
        _register_artifact(
            phase=phase,
            blackboard_state=kwargs.get("blackboard_state"),
            name="local_review_context",
            path=path,
            updated_by=self.name,
        )
        return HookResult(
            override_status_code=PhaseStatusCode.CONFIRM_OUTPUT,
            events=[{"type": "local_review_context_ready"}],
        )
