"""Integration tests for alignment checkpoint runtime handoff semantics."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime


def test_required_alignment_checkpoint_pauses_at_user(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-runtime"
    playbook = {
        "playbook": {"id": "tiny"},
        "steps": {
            "develop": {
                "skill": "develop",
                "role": "developer",
                "alignment": {},
                "on": {"await_agent": "_done"},
            }
        },
        "entry_point": "develop",
    }

    def executor(step_name: str, step_def: dict, state) -> StepExecutionResult:
        store = BlackboardStore(issue_dir)
        store.update_handoff_contract(
            state,
            from_step=step_name,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.ALIGNMENT_CHECKPOINT,
            status_code="alignment_checkpoint",
            source="test.alignment",
        )
        return StepExecutionResult(
            response="",
            artifacts={},
            status_code="alignment_checkpoint",
            auto_continue=False,
            events=[{"type": "handoff_intent", "intent": "alignment_checkpoint"}],
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    ).run(start_step="develop", max_transitions=3)

    assert result.completed is False
    blackboard = BlackboardStore(issue_dir).load_or_create("develop", playbook_id="tiny")
    assert blackboard.current_step == "user"
    assert blackboard.handoff_contract is not None
    assert blackboard.handoff_contract.intent == HandoffIntent.ALIGNMENT_CHECKPOINT
