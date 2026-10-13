"""Local artifact acceptance follows the normal delivery result task, without effects."""

import json
import subprocess
from pathlib import Path

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration.test_development_delivery_journey import (
    approve_action, git, hook, local_action, pause, run_and_approve_host, setup_action,
)


def prepare(local_action, tmp_path, *, renamed=False, prepare_review=False):
    context = setup_action(local_action, tmp_path, renamed=renamed, prepare_review=prepare_review)
    root, _, issue, state, _, data, _, kwargs, _, delivery = context
    name = data["steps"][delivery]["workspace_input_artifact"]
    workspace = build_workspace_artifact(
        repo=root, name=name, version=1,
        base_sha=local_action[3].proposal.target_oid, head_sha=git(root, "rev-parse", "HEAD"),
    )
    path = issue / "workspace.json"
    path.write_text(json.dumps(workspace.to_dict()))
    BlackboardStore(issue).put_artifact(state, ArtifactEntry(
        name=name, kind=ArtifactKind.WORKSPACE, version=1, updated_by="develop", path=str(path),
    ))
    (kwargs["output_file"].parent / "delivery_request.json").write_text('{"mode":"local-only"}')
    return context


def accept(context, task):
    _, _, issue, state, _, data, _, _, _, delivery = context
    pause(issue, state, delivery)
    return apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=state, from_step=delivery,
        trigger="confirm_output", source="test",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"},
    )


@pytest.mark.parametrize("renamed", [False, True])
def test_local_only_result_requires_acceptance_and_never_integrates(local_action, tmp_path, monkeypatch, renamed):
    context = prepare(local_action, tmp_path, renamed=renamed)
    root, dest, issue, state, _, _, engine, kwargs, _, _ = context
    target = git(dest, "rev-parse", "HEAD")
    native = subprocess.Popen

    def readonly(argv, *args, **kw):
        assert argv[0] != "gh"
        assert "merge" not in argv and "push" not in argv
        return native(argv, *args, **kw)

    monkeypatch.setattr(subprocess, "Popen", readonly)
    result = hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    assert result.continue_pipeline, result.context_updates
    task = HumanTaskRecordStore(issue).tasks()[-1]
    assert task.policy_id == "delivery-outcome"
    assert "local-only" in task.prompt
    assert not list((issue / "delivery").glob("*/actions.json"))
    again = hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    assert again.continue_pipeline and len(HumanTaskRecordStore(issue).tasks()) == 1
    assert state.current_step != "done"
    assert accept(context, task).target == "done"
    assert git(dest, "rev-parse", "HEAD") == target != git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("change", ["source", "dirty", "branch", "pr", "workspace", "output", "request", "result", "workflow", "version"])
def test_changed_local_delivery_evidence_cannot_be_accepted(local_action, tmp_path, change):
    context = prepare(local_action, tmp_path)
    root, _, issue, state, _, _, engine, kwargs, _, _ = context
    result = hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    assert result.continue_pipeline
    task = HumanTaskRecordStore(issue).tasks()[-1]
    if change in {"source", "dirty"}:
        (root / "later.txt").write_text("later")
        if change == "source":
            git(root, "add", ".")
            git(root, "commit", "-qm", "Later")
    elif change == "branch":
        git(root, "checkout", "-b", "different")
    elif change == "pr":
        Path(state.artifacts["pr_result"].path).write_text("different")
    elif change == "workspace":
        path = issue / "workspace.json"
        data = json.loads(path.read_text())
        data["head_sha"] = local_action[3].proposal.target_oid
        path.write_text(json.dumps(data))
    elif change == "output":
        kwargs["output_file"].write_text("different")
    elif change == "request":
        (kwargs["output_file"].parent / "delivery_request.json").write_text('{"mode":"local"}')
    elif change == "workflow":
        path = Path(result.context_updates["delivery_receipts_file"])
        report = json.loads(path.read_text())
        report["workflow_id"] = "foreign-workflow"
        path.write_text(json.dumps(report))
    elif change == "version":
        state.artifacts["pr_result"].version += 1
        BlackboardStore(issue).put_artifact(state, state.artifacts["pr_result"])
    else:
        Path(result.context_updates["delivery_receipts_file"]).write_text('{}')
    rejected = accept(context, task)
    assert rejected.target is None and rejected.rejection is not None


def test_pending_integration_review_cannot_be_replaced_by_local_acceptance(local_action, tmp_path):
    context = prepare(local_action, tmp_path, prepare_review=True)
    result = hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    assert result.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert not any(t.policy_id == "delivery-outcome" for t in HumanTaskRecordStore(context[2]).tasks())


def test_prior_effects_cannot_be_hidden_by_switching_to_local_only(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    run_and_approve_host(context)
    output = context[7]["output_file"]
    (output.parent / "delivery_request.json").write_text('{"mode":"local-only"}')
    result = hook(context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7])
    assert result.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert not any(t.policy_id == "delivery-outcome" for t in HumanTaskRecordStore(context[2]).tasks())


@pytest.mark.parametrize("change_batch", [False, True])
def test_local_delivery_revision_binds_immutable_feedback_batch(local_action, tmp_path, change_batch):
    context = prepare(local_action, tmp_path)
    _, _, issue, state, phase, data, engine, kwargs, _, delivery = context
    hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    task = HumanTaskRecordStore(issue).tasks()[-1]
    pause(issue, state, delivery)
    revised = apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=state, from_step=delivery,
        trigger="confirm_output", source="test",
        raw_payload={"task": task.policy_id, "human_task_id": task.id,
                     "decision": "revise", "feedback": "Clarify the local evidence."},
    )
    assert revised.target == delivery, revised.rejection
    output = issue / delivery / "iteration_002" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Clarified local evidence\n")
    (output.parent / "delivery_request.json").write_text('{"mode":"local-only"}')
    phase.iteration = 2
    kwargs["output_file"] = output
    ledger = WorkflowFeedbackLedger(issue)
    assert ledger.pending(target_step=delivery)
    batch = output.parent / "workflow_feedback_batch.json"
    identities = ledger.write_pending_snapshot(path=batch, target_step=delivery, limit=100)
    result = hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    assert result.continue_pipeline
    task = HumanTaskRecordStore(issue).tasks()[-1]
    ledger.settle_or_reconcile_reviewed(identities, ())
    if change_batch:
        batch.write_text('{"version":1,"entries":[]}')
    accepted = accept(context, task)
    assert accepted.target == (None if change_batch else "done"), accepted.rejection
