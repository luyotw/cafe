"""PR content approval and delivery action permission are separate authorities."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.human_tasks import resolve_step_human_task
from cafe.core.status_codes import PhaseStatusCode
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.integration.test_delivery_verification_gate import configure
from tests.unit.test_github_delivery_tool import verification_plan

local_action = journey.local_action
fake_github = journey.fake_github


@pytest.mark.parametrize("renamed", [False, True])
def test_content_review_then_delivery_permission_wait_restart_and_outcome(
    local_action, tmp_path, fake_github, renamed,
):
    configure(fake_github, "queued", None)
    ctx = journey.setup_action(local_action, tmp_path, github=True, renamed=renamed,
                               verification=verification_plan(), prepare_review=False)
    root, dest, issue, state, phase, data, engine, args, _, delivery = ctx
    publication = "package" if renamed else "pr"
    request = args["output_file"].parent / "delivery_request.json"
    draft = request.read_bytes()
    request.unlink()  # PR completion needs no integration strategy configuration.
    policy, binding = resolve_step_human_task(playbook_data=data, step_name=publication,
                                             trigger="confirm_output")
    records = HumanTaskRecordStore(issue)
    published = Path(state.artifacts["pr_result"].path)
    (published.parent / "iteration.json").write_text(json.dumps({
        "agent_invoked": True, "step_name": publication, "iteration": 1,
    }))
    (published.parent / "workflow_feedback_batch.json").write_text(json.dumps({"version": 1, "entries": []}))
    content_task = records.materialize(
        workflow_id=state.workflow_id, step=publication, iteration=1, trigger="confirm_output",
        policy_id=policy.id, prompt=policy.prompt, expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes, assignee_type="user",
    )
    journey.pause(issue, state, publication)
    invalid = apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=state, from_step=publication,
        trigger="confirm_output", source="test",
        raw_payload={"task": policy.id, "human_task_id": content_task.id, "decision": "integrate_only"},
    )
    assert invalid.rejection is not None
    accepted = apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=state, from_step=publication,
        trigger="confirm_output", source="test",
        raw_payload={"task": policy.id, "human_task_id": content_task.id, "decision": "confirm"},
    )
    assert accepted.target == delivery
    BlackboardStore(issue).set_current_step(state, delivery)
    prepared = journey.hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", args)
    assert prepared.continue_pipeline
    assert prepared.context_updates["delivery_stage"] == "prepare_action"
    assert json.loads(fake_github.read_text())["effects"] == []
    assert not any(t.policy_id == "delivery-review" for t in records.tasks())

    agent_calls = []
    def execute(name, declaration, blackboard, before_agent=None, **kwargs):
        args.update(step_name=name, step_def=declaration, blackboard_state=blackboard)
        prepared = journey.hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", args)
        if before_agent:
            before_agent()
        agent_calls.append(name)
        (args["output_file"].parent / "iteration.json").write_text(json.dumps({
            "agent_invoked": True, "step_name": name, "iteration": phase.iteration,
        }))
        if prepared.context_updates.get("delivery_stage") == "prepare_action":
            request.write_bytes(draft)
            result = journey.hook(engine, "publish_output", "DevelopmentActionContext", args)
            status = PhaseStatusCode.NEED_PERMISSION
        else:
            args["output_file"].write_text("# Verified delivery result\n")
            result = journey.hook(engine, "publish_output", "DevelopmentDeliveryOutcome", args)
            status = PhaseStatusCode.CONFIRM_OUTPUT
        assert result.continue_pipeline
        return StepExecutionResult(response="Prepared declared delivery handoff", artifacts={},
                                   status_code=status.value, events=result.events)

    callbacks = []
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=data, executor=execute,
                                         workflow_event_callback=callbacks.append)
    first = runtime.run(start_step=delivery, single_step=True)
    assert not first.completed
    assert any(t.policy_id == "delivery-review" for t in records.tasks()), (first, agent_calls)
    action_task = next(t for t in records.tasks() if t.policy_id == "delivery-review")
    assert action_task.step == delivery and action_task.trigger == "need_permission"
    assert sum(t.policy_id == "delivery-review" for t in records.tasks()) == 1
    assert json.loads(fake_github.read_text())["effects"] == []
    current = (*ctx[:3], runtime.blackboard, *ctx[4:8], action_task, delivery)
    journey.approve_action(current)
    # Replacing the delivery output cannot invalidate the original PR evidence.
    assert "Published PR" in Path(runtime.blackboard.artifacts["pr_result"].path).read_text()
    phase.iteration = 2
    args["output_file"] = issue / delivery / "iteration_002/output.md"
    args["output_file"].parent.mkdir()
    BlackboardStore(issue).set_current_step(runtime.blackboard, delivery)
    resumed = BlackboardWorkflowRuntime(issue_dir=issue, playbook=data, executor=execute)
    assert resumed.run(start_step=delivery).wait_seconds is not None
    assert agent_calls == [delivery]
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]
    assert not any(t.policy_id == "delivery-outcome" for t in records.tasks())
    configure(fake_github)
    fresh = BlackboardWorkflowRuntime(issue_dir=issue, playbook=data, executor=execute)
    result = fresh.run()
    assert not result.completed
    assert agent_calls == [delivery, delivery]
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]
    outcome = next(t for t in records.tasks() if t.policy_id == "delivery-outcome")
    journey.pause(issue, fresh.blackboard, delivery)
    accepted = apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=fresh.blackboard, from_step=delivery,
        trigger="confirm_output", source="test",
        raw_payload={"task": outcome.policy_id, "human_task_id": outcome.id, "decision": "confirm"},
    )
    assert accepted.target == "done", accepted.rejection


@pytest.mark.parametrize("decision,feedback", [("review_only", ""), ("fix_now", "Use repository policy")])
def test_non_action_decisions_return_to_delivery_plan_without_integration(
    local_action, tmp_path, fake_github, decision, feedback,
):
    ctx = journey.setup_action(local_action, tmp_path, github=True)
    root, dest, issue, state, phase, data, engine, args, task, delivery = ctx
    journey.pause(issue, state, delivery, HandoffIntent.NEED_PERMISSION)
    reply = apply_human_task_payload(
        issue_dir=issue, playbook_data=data, blackboard=state, from_step=delivery,
        trigger="need_permission", source="test",
        raw_payload={"task": task.policy_id, "human_task_id": task.id,
                     "decision": decision, "feedback": feedback},
    )
    assert reply.target == delivery, reply.rejection
    result = journey.hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", args)
    assert result.context_updates["delivery_stage"] == "prepare_action"
    assert json.loads(fake_github.read_text())["effects"] == []


@pytest.mark.parametrize("lookup", ["historical", "merged", "ambiguous", "truncated"])
def test_pr_lookup_uses_current_source_and_rejects_real_ambiguity(
    local_action, tmp_path, fake_github, lookup,
):
    external = json.loads(fake_github.read_text())
    old = deepcopy(external["pr"])
    old.update(number=7, state="closed", merged=True)
    old["head"]["sha"] = "e" * 40
    external["lookup_prs"] = [old, external["pr"]]
    if lookup == "ambiguous":
        extra = deepcopy(external["pr"])
        extra["number"] = 24
        external["lookup_prs"].append(extra)
    if lookup == "truncated":
        external["lookup_prs"] = [old] * 99 + [external["pr"]]
    if lookup == "merged":
        external["pr"].update(state="closed", merged=True)
        row = deepcopy(external["pr"])
        row.pop("merged")  # List responses expose merged_at, unlike PR detail.
        row["merged_at"] = "2026-10-10T00:00:00Z"
        external["lookup_prs"] = [row]
    fake_github.write_text(json.dumps(external))
    ctx = journey.setup_action(local_action, tmp_path, github=True, prepare_review=False)
    result = journey.hook(ctx[6], "publish_output", "DevelopmentActionContext", ctx[7])
    if lookup in {"historical", "merged"}:
        assert result.continue_pipeline
        task = HumanTaskRecordStore(ctx[2]).get_task(result.context_updates["delivery_action_review_task"])
        assert "https://github.com/owner/repo/pull/23" in task.prompt
    else:
        assert not result.continue_pipeline
        assert result.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
        assert not any(t.policy_id == "delivery-review" for t in HumanTaskRecordStore(ctx[2]).tasks())
    assert json.loads(fake_github.read_text())["effects"] == []
