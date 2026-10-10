"""Actual action hooks wait for checks and reject premature/stale terminal approval."""

import json

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workflow_models import StepWaiting
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.unit.test_github_delivery_tool import responses, verification_plan

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
    ctx = journey.setup_action(
        local_action,
        tmp_path,
        github=True,
        verification=verification_plan(),
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
    with pytest.raises(StepWaiting):
        prepare(ctx)
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

def test_changed_tool_cannot_run_and_delivery_can_help_draft(
    local_action, tmp_path, fake_github, monkeypatch,
):
    import shutil
    from cafe.skills.loader import SkillLoader
    root = local_action[0]
    owner = root / ".cafe/skills/cafe-deliver_development"
    shutil.copytree(SkillLoader(project_root=root).get_skill_dir("cafe-deliver_development"), owner)
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    script = owner / "scripts/verify_github_actions.py"
    script.write_text("raise SystemExit('unapproved replacement')")
    result = prepare(ctx)
    assert result.continue_pipeline
    assert result.context_updates["delivery_complete"] == "false"
    assert "Help draft" in result.context_updates["continuation_prompt"]
    assert not [t for t in HumanTaskRecordStore(ctx[2]).tasks()
                if t.policy_id == "delivery-outcome"]
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


def test_changed_capability_boundary_is_not_repeatedly_executed(
    local_action, tmp_path, fake_github, monkeypatch,
):
    import yaml
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    with pytest.raises(StepWaiting):
        prepare(ctx)
    from cafe.core.capabilities import default_capability_definition_dirs
    builtin = next(path / "cafe.delivery.verify.yaml"
                   for path in default_capability_definition_dirs(ctx[0])
                   if (path / "cafe.delivery.verify.yaml").is_file())
    manifest = yaml.safe_load(builtin.read_text())
    manifest["credentials"] = ["replacement-credential"]
    custom = tmp_path / "updated-package-capabilities"
    custom.mkdir(exist_ok=True)
    (custom / "cafe.delivery.verify.yaml").write_text(yaml.safe_dump(manifest))
    import shutil
    for path in builtin.parent.glob("*.yaml"):
        if path.name != builtin.name:
            shutil.copy(path, custom / path.name)
    monkeypatch.setattr("cafe.core.capabilities._package_capabilities_dir", lambda: custom)
    before = json.loads(fake_github.read_text())["observations"]
    result = prepare(ctx)
    assert not result.continue_pipeline
    assert not [t for t in HumanTaskRecordStore(ctx[2]).tasks()
                if t.policy_id == "delivery-outcome"]
    after = json.loads(fake_github.read_text())["observations"]
    assert not any("/actions/" in endpoint for endpoint in after[len(before):])
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


def test_missing_skill_owner_delivery_can_help_draft(
    local_action, tmp_path, fake_github, monkeypatch,
):
    from cafe.skills.loader import SkillLoader
    from cafe.skills.exceptions import SkillDiscoveryError
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    with pytest.raises(StepWaiting):
        prepare(ctx)
    original = SkillLoader.get_skill_dir
    def missing(self, name):
        if name == "cafe-deliver_development":
            raise SkillDiscoveryError("approved tool owner disappeared")
        return original(self, name)
    monkeypatch.setattr(SkillLoader, "get_skill_dir", missing)
    result = prepare(ctx)
    assert result.continue_pipeline
    assert result.context_updates["delivery_complete"] == "false"
    assert "Help draft" in result.context_updates["continuation_prompt"]
    assert not [t for t in HumanTaskRecordStore(ctx[2]).tasks()
                if t.policy_id == "delivery-outcome"]
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


def test_pending_unwinds_real_phase_lease_before_any_agent_call(
    local_action, tmp_path, fake_github, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from cafe.core.workspace_lock import workspace_execution_lock
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    args = ctx[7]
    extra = {key: value for key, value in args.items()
             if key not in {"step_def", "status_code", "context"}}
    with pytest.raises(StepWaiting):
        ctx[6].execute(
            skill_name=args["step_def"]["skill"], step_def=args["step_def"],
            skill_invocation="/cafe-deliver_development",
            agent_executor=lambda *a: pytest.fail("AI must not run while pending"),
            output_file=args["output_file"], hook_context=extra,
            execution_lease=lambda: workspace_execution_lock(ctx[0]),
        )
    def acquire():
        with workspace_execution_lock(ctx[0]):
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(acquire).result(timeout=2)
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]


def test_restart_only_rechecks_and_retains_single_merged_effect(
    local_action, tmp_path, fake_github, monkeypatch,
):
    from cafe.delivery.contracts import DeliveryBinding
    from cafe.delivery.selection import approved_snapshot
    from cafe.delivery.service import execute_snapshot
    from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
    ctx = context(local_action, tmp_path, fake_github, monkeypatch)
    with pytest.raises(StepWaiting):
        prepare(ctx)
    snapshot = approved_snapshot(
        ctx[2], workflow_id=ctx[3].workflow_id,
        binding=DeliveryBinding.model_validate(ctx[7]["step_def"]["delivery"]),
    )
    configure(fake_github)
    report, path = execute_snapshot(
        root=ctx[0], issue_dir=ctx[2], snapshot=snapshot,
        registry=load_capability_registry(default_capability_definition_dirs(ctx[0])),
        step=ctx[9], iteration=1, output_file=ctx[7]["output_file"],
    )
    assert report["complete"]
    assert "observation" not in report
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
