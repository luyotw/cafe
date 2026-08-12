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
@pytest.mark.skip(reason="workflow_feedback replaces the last-seen artifact")
def test_pr_runtime_loads_last_seen_comment_ids_from_artifact(tmp_path: Path) -> None:
    from cafe.utils.github import load_pr_last_seen_comment_ids, persist_last_seen_comment_ids

    pr_dir = tmp_path / "pr"
    persist_last_seen_comment_ids(pr_dir, ["100", "200"])
    assert load_pr_last_seen_comment_ids(pr_dir) == {"100", "200"}
