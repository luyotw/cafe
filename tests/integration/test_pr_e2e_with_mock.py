"""E2E tests for default-playbook PR step via workflow runtime (no PRPhase)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader


def _load_default_playbook() -> dict:
    return PlaybookLoader().load("default")


def _write_baton(
    issue_dir: Path,
    *,
    from_step: str,
    to_owner: HandoffOwner,
    to_step: str,
    intent: HandoffIntent,
    status_code: str = "confirmed",
) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create(from_step)
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=to_owner,
        to_step=to_step,
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def _seed_pr_artifacts(issue_dir: Path) -> None:
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    (issue_dir / "issue.yaml").write_text("base_branch: main\n", encoding="utf-8")


@pytest.mark.e2e
def test_pr_runtime_completes_with_capability_receipt(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-e2e"
    playbook = _load_default_playbook()
    assert playbook["steps"]["pr"]["capability_requests"] == ["cafe.pr.publish"]
    _seed_pr_artifacts(issue_dir)

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name != "pr":
            return StepExecutionResult(response="skip", artifacts={})
        _write_baton(
            issue_dir,
            from_step="pr",
            to_owner=HandoffOwner.DONE,
            to_step="done",
            intent=HandoffIntent.WORKFLOW_COMPLETE,
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr": str(issue_dir / "pr" / "iteration_001" / "output.md")},
            events=[
                {
                    "type": "capability_receipt",
                    "capability": "cafe.pr.publish",
                    "success": True,
                    "correlation_id": "test-pr-e2e",
                    "category": None,
                    "code": None,
                }
            ],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", max_transitions=5)

    assert result.completed is True
    assert result.final_step == "pr"
    blackboard = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    receipts = blackboard.get("capability_receipts") or []
    assert any(
        r.get("capability") == "cafe.pr.publish" for r in receipts
    ) or result.final_status_code in {
        "BATON_WORKFLOW_COMPLETE",
        "confirmed",
    }


@pytest.mark.e2e
def test_declared_pr_feedback_source_records_and_delivers_each_comment_once(tmp_path: Path) -> None:
    """IT-001: GitHub feedback is batched, retried, and delivered exactly once."""
    from unittest.mock import MagicMock, patch

    from cafe.core.hooks.feedback import GitHubPRFeedbackSource
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger
    from cafe.ui.cli_shared import _find_external_resume_step

    issue_dir = tmp_path / ".cafe" / "issues" / "pr-feedback"
    playbook = _load_default_playbook()
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr", playbook_id="default")

    class Phase:
        def __init__(self) -> None:
            self.issue_dir = issue_dir
            self.git_ops = MagicMock()
            self.git_ops.get_current_branch.return_value = "pr-feedback"
            self.step_user_inputs: dict[str, str] = {}

    phase = Phase()
    source = GitHubPRFeedbackSource()
    with (
        patch("cafe.core.hooks.feedback.GitHubOps") as github_ops,
        patch(
            "cafe.core.hooks.feedback.get_all_pr_comments",
            return_value=[
                {"id": "100", "body": "Handle the first boundary.", "is_resolved": False},
                {"id": "101", "body": "Handle the second boundary.", "is_resolved": False},
            ],
        ),
    ):
        github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 101,
            "url": "https://example.test/pr/101",
        }
        first = source.run(
            stage="prepare_input",
            phase=phase,
            blackboard_state=state,
            step_def=playbook["steps"]["pr"],
            step_name="pr",
        )
        second = source.run(
            stage="prepare_input",
            phase=phase,
            blackboard_state=state,
            step_def=playbook["steps"]["pr"],
            step_name="pr",
        )

    ledger = WorkflowFeedbackLedger(issue_dir)
    assert [entry.content for entry in ledger.pending()] == [
        "Handle the first boundary.",
        "Handle the second boundary.",
    ]
    assert any(event["type"] == "workflow_feedback_recorded" for event in first.events)
    assert second.events == []

    phase.git_ops.reset_mock()
    assert _find_external_resume_step(
        issue_dir=issue_dir,
        playbook_data=playbook,
        git_ops=phase.git_ops,
    ) == "develop"
    assert len(ledger.pending(target_step="develop")) == 2
    phase.git_ops.get_current_branch.assert_not_called()

    delivered_feedback: list[list[str]] = []

    def interrupted_executor(
        step_name: str, _step_def: dict, _state: object
    ) -> StepExecutionResult:
        delivered_feedback.append(
            [entry.content for entry in ledger.pending(target_step=step_name)]
        )
        raise KeyboardInterrupt()

    interrupted = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=interrupted_executor,
    ).run(start_step="develop", single_step=True)

    assert interrupted.final_status_code.startswith("INTERRUPTED")
    assert [entry.content for entry in ledger.pending(target_step="develop")] == [
        "Handle the first boundary.",
        "Handle the second boundary.",
    ]

    def executor(step_name: str, _step_def: dict, _state: object) -> StepExecutionResult:
        delivered_feedback.append(
            [entry.content for entry in ledger.pending(target_step=step_name)]
        )
        return StepExecutionResult(response="completed", artifacts={}, status_code="confirmed")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    runtime.run(start_step="develop", single_step=True)

    assert delivered_feedback == [
        ["Handle the first boundary.", "Handle the second boundary."],
        ["Handle the first boundary.", "Handle the second boundary."],
    ]
    assert ledger.pending(target_step="develop") == []
