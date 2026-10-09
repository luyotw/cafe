"""Actual action hooks wait for checks and reject premature/stale terminal approval."""

import json
from functools import partial

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.delivery.verification import wait_for_delivery
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.unit.test_delivery_verification import PATH, responses

local_action = journey.local_action
fake_github = journey.fake_github


def configure(path, state="completed", conclusion="success", attempt=1):
    data = json.loads(path.read_text())
    runs, jobs = responses()
    runs["workflow_runs"][0].update(status=state, conclusion=conclusion, run_attempt=attempt)
    data.update(workflow_runs=runs, jobs=jobs)
    path.write_text(json.dumps(data))


def context(local_action, tmp_path, fake_github, monkeypatch):
    configure(fake_github, "queued", None)
    monkeypatch.setattr(
        "cafe.delivery.verification.wait_for_delivery", partial(wait_for_delivery, timeout=0)
    )
    ctx = journey.setup_action(
        local_action,
        tmp_path,
        github=True,
        verification={
            "workflows": [{"path": PATH, "jobs": {"deploy": ["Deploy", "Public checks"]}}],
        },
    )
    assert "Post-integration verification:" in ctx[8].prompt
    journey.approve_action(ctx)
    return ctx


def prepare(ctx):
    return journey.hook(ctx[6], "prepare_input", "DevelopmentDeliveryExecutor", ctx[7])


def test_pending_checks_wait_then_complete_without_remerging(
    local_action,
    tmp_path,
    fake_github,
    monkeypatch,
):
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    first = prepare(ctx)
    assert first.context_updates["delivery_complete"] == "false"
    report = json.loads(next((ctx[2] / "delivery").glob("*/result.json")).read_text())
    assert report["actions"]["integration"]["state"] == "succeeded"
    assert report["verification"]["state"] == "pending" and not report["complete"]
    assert not [
        t for t in HumanTaskRecordStore(ctx[2]).tasks() if t.policy_id == "delivery-outcome"
    ]
    configure(fake_github)
    assert prepare(ctx).context_updates["delivery_complete"] == "true"
    assert journey.hook(
        ctx[6], "publish_output", "DevelopmentDeliveryOutcome", ctx[7]
    ).continue_pipeline
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_failed_checks_cannot_materialize_acceptance(
    local_action,
    tmp_path,
    fake_github,
    monkeypatch,
    conclusion,
):
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    configure(fake_github, conclusion=conclusion)
    assert prepare(ctx).context_updates["delivery_complete"] == "false"
    # Even a phase attempting a premature confirm_output cannot create acceptance.
    result = journey.hook(ctx[6], "publish_output", "DevelopmentDeliveryOutcome", ctx[7])
    assert not result.continue_pipeline
    assert not [
        t for t in HumanTaskRecordStore(ctx[2]).tasks() if t.policy_id == "delivery-outcome"
    ]
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


def test_new_rerun_invalidates_pending_user_acceptance(
    local_action,
    tmp_path,
    fake_github,
    monkeypatch,
):
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    configure(fake_github)
    assert prepare(ctx).context_updates["delivery_complete"] == "true"
    assert journey.hook(
        ctx[6], "publish_output", "DevelopmentDeliveryOutcome", ctx[7]
    ).continue_pipeline
    records = HumanTaskRecordStore(ctx[2])
    task = next(t for t in records.tasks() if t.policy_id == "delivery-outcome")
    journey.pause(ctx[2], ctx[3], ctx[9])
    configure(fake_github, "in_progress", None, attempt=2)
    reply = apply_human_task_payload(
        issue_dir=ctx[2],
        playbook_data=ctx[5],
        blackboard=ctx[3],
        from_step=ctx[9],
        trigger="confirm_output",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "confirm",
        },
        source="test",
    )
    assert reply.target is None and reply.rejection is not None
    assert records.get_task(task.id).status == HumanTaskStatus.PENDING
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]
