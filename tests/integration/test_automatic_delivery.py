"""Real automatic owner, task decisions and local/GitHub receipts without an agent."""

import json
from pathlib import Path

import pytest
import yaml

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.ui.human_tasks import apply_human_task_payload
from cafe.workflow_execution.automatic import automatic_executor_registry
from tests.integration import test_development_delivery_journey as journey
from tests.integration.test_development_delivery_operations import git

local_action = journey.local_action
fake_github = journey.fake_github


def setup(local_action, tmp_path, *, github=False, renamed=False):
    ctx = journey.setup_action(
        local_action, tmp_path, github=github, renamed=renamed, prepare_review=False
    )
    root, dest, issue, state, phase, data, engine, kwargs, _, step = ctx
    request = json.loads((kwargs["output_file"].parent / "delivery_request.json").read_text())
    (issue / "issue.yaml").write_text(
        yaml.safe_dump({"playbook_id": data["playbook"]["id"], step: request})
    )
    workspace = build_workspace_artifact(
        repo=root,
        name="workspace",
        version=1,
        base_sha=git(dest, "rev-parse", "HEAD"),
        head_sha=git(root, "rev-parse", "HEAD"),
    )
    path = issue / "workspace.json"
    path.write_text(json.dumps(workspace.to_dict()))
    store = BlackboardStore(issue)
    store.put_artifact(
        state,
        ArtifactEntry(
            name="workspace",
            kind=ArtifactKind.WORKSPACE,
            version=1,
            updated_by="build" if renamed else "develop",
            path=str(path),
            base_sha=workspace.base_sha,
            head_sha=workspace.head_sha,
        ),
    )
    # Discard the fixture's legacy agent baton; runtime constructs the real automatic handoff.
    (issue / "next_step.txt").unlink()
    return ctx


def run(ctx, **kw):
    root, dest, issue, state, phase, data, engine, kwargs, _, step = ctx

    def forbidden(*args, **kwargs):
        pytest.fail("automatic delivery invoked an agent")

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=data,
        executor=forbidden,
        automatic_registry=automatic_executor_registry(),
        **kw,
    )
    result = runtime.run(start_step=step)
    return runtime, result


def answer(ctx, runtime, decision, feedback=""):
    issue, data, step = ctx[2], ctx[5], ctx[-1]
    state = runtime.blackboard
    pending = [t for t in HumanTaskRecordStore(issue).tasks() if t.status.value == "pending"]
    task = pending[-1]
    applied = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=step,
        trigger=task.trigger,
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": decision,
            "feedback": feedback,
        },
        source="test",
    )
    assert not applied.rejection, applied.rejection
    return task, applied


@pytest.mark.parametrize("github,renamed", [(False, False), (True, False), (False, True)])
def test_review_execute_outcome_and_restart_are_automatic(
    local_action, fake_github, tmp_path, monkeypatch, github, renamed
):
    ctx = setup(local_action, tmp_path, github=github, renamed=renamed)
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_PERMISSION", result
    review, _ = answer(ctx, runtime, "integrate_only")
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    result_name = ctx[5]["steps"][ctx[-1]]["delivery"]["result_artifact"]
    artifact = runtime.blackboard.artifacts[result_name]
    text = Path(artifact.path).read_text()
    assert "Action snapshot SHA256:" in text and "Delivery result SHA256:" in text
    assert '"complete": true' in text and "Verified integration commit:" in text
    assert (
        len([t for t in HumanTaskRecordStore(ctx[2]).tasks() if t.policy_id == "delivery-outcome"])
        == 1
    )
    from cafe.delivery.operations import Commands

    original = Commands.run

    def no_merge(self, argv, **kw):
        assert "merge" not in argv
        return original(self, argv, **kw)

    monkeypatch.setattr(Commands, "run", no_merge)
    # Crash just after task creation: output and blackboard publication are reconstructed.
    Path(artifact.path).unlink()
    runtime.blackboard.artifacts.pop(result_name)
    BlackboardStore(ctx[2]).save(runtime.blackboard)
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    assert Path(runtime.blackboard.artifacts[result_name].path).read_text() == text
    task, applied = answer(ctx, runtime, "confirm")
    assert applied.target == "done"
    assert runtime.blackboard.ownership_cursor is None
    assert ctx[1].exists()  # Cleanup remains an explicit separate command.


def test_review_only_represents_once_and_correction_routes_feedback(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    runtime, _ = run(ctx)
    first, _ = answer(ctx, runtime, "review_only")
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_PERMISSION"
    pending = [t for t in HumanTaskRecordStore(ctx[2]).tasks() if t.status.value == "pending"]
    assert len(pending) == 1 and pending[0].iteration == first.iteration + 1
    task, applied = answer(ctx, runtime, "fix_now", "Correct result.txt before integration")
    assert applied.target == "develop"
    assert runtime.blackboard.ownership_cursor is None
    from cafe.core.workflow_feedback import WorkflowFeedbackLedger

    assert WorkflowFeedbackLedger(ctx[2]).pending(target_step="develop")


def test_dirty_workspace_needs_correction_and_never_merges(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    original = git(ctx[1], "rev-parse", "HEAD")
    (ctx[0] / "result.txt").write_text("Unreviewed dirty change")
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_CLARIFICATION", result
    assert git(ctx[1], "rev-parse", "HEAD") == original
    task, applied = answer(ctx, runtime, None, "Restore reviewed content")
    assert applied.target == "develop"


def test_pending_verification_waits_without_merge_replay(local_action, tmp_path, monkeypatch):
    ctx = setup(local_action, tmp_path)
    runtime, _ = run(ctx)
    answer(ctx, runtime, "integrate_only")
    from cafe.delivery import verification

    original = verification.observe_delivery
    monkeypatch.setattr(verification, "observe_delivery", lambda *a, **kw: {"state": "pending"})
    runtime, result = run(ctx)
    assert result.final_status_code == "HOST_WAITING", result
    assert runtime.blackboard.host_wait
    attempt = runtime.blackboard.step_attempt_counts[ctx[-1]]
    from cafe.delivery.operations import Commands

    command_run = Commands.run

    def no_merge(self, argv, **kw):
        assert "merge" not in argv
        return command_run(self, argv, **kw)

    monkeypatch.setattr(Commands, "run", no_merge)
    monkeypatch.setattr(verification, "observe_delivery", original)
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    assert runtime.blackboard.host_wait is None
    assert runtime.blackboard.step_attempt_counts[ctx[-1]] == attempt


def test_action_task_crash_repairs_missing_proposal_without_new_task(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    runtime, _ = run(ctx)
    records = HumanTaskRecordStore(ctx[2])
    task = [t for t in records.tasks() if t.status.value == "pending"][-1]
    from cafe.delivery.selection import proposal_path

    path = proposal_path(ctx[2], task.id)
    original = path.read_bytes()
    path.unlink()
    output = ctx[2] / ctx[-1] / f"iteration_{task.iteration:03d}" / "output.md"
    output.unlink()
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_PERMISSION", result
    assert path.read_bytes() == original
    assert output.read_text() == task.prompt
    assert len(records.tasks()) == 1


def test_public_workflow_delivery_without_model_configuration(local_action, tmp_path, monkeypatch):
    ctx = setup(local_action, tmp_path)
    from tests.conftest import create_minimal_config
    from typer.testing import CliRunner
    from cafe.ui.cli import app

    create_minimal_config(ctx[0])
    monkeypatch.chdir(ctx[0])
    result = CliRunner().invoke(app, ["workflow", "--issue", ctx[2].name, "--execute"])
    assert result.exit_code == 0, result.output + repr(result.exception)
    tasks = HumanTaskRecordStore(ctx[2]).tasks()
    assert tasks[-1].policy_id == "delivery-review"


def test_scope_checkpoint_blocks_committed_out_of_scope_delivery(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    root = ctx[0]
    context = {
        "version": 1,
        "revision": 1,
        "authority_digest": "a" * 64,
        "root": str(root),
        "baseline_commit": git(ctx[1], "rev-parse", "HEAD"),
        "paths": ["other.txt"],
        "identity": {"issue": ctx[2].name},
        "preexisting": [],
    }
    runtime, result = run(ctx, execution_context=context)
    assert result.final_status_code == "EXECUTION_CHECKPOINT_BLOCKED", result
    assert not list((ctx[2] / "delivery").glob("*/integration.json"))


@pytest.mark.parametrize("state", ["failed", "unknown"])
def test_failed_or_unknown_verification_never_publishes_acceptance(
    local_action, tmp_path, monkeypatch, state
):
    ctx = setup(local_action, tmp_path)
    runtime, _ = run(ctx)
    answer(ctx, runtime, "integrate_only")
    monkeypatch.setattr(
        "cafe.delivery.verification.observe_delivery", lambda *a, **kw: {"state": state}
    )
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_CLARIFICATION", result
    assert not any(t.policy_id == "delivery-outcome" for t in HumanTaskRecordStore(ctx[2]).tasks())
    assert git(ctx[1], "rev-parse", "HEAD") == git(ctx[0], "rev-parse", "HEAD")


def test_formatted_result_uses_workflow_locale(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    ctx[3].conversation_locale = "zh-TW"
    BlackboardStore(ctx[2]).save(ctx[3])
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_NEED_PERMISSION", result
    answer(ctx, runtime, "integrate_only")
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    text = Path(runtime.blackboard.artifacts["delivery_result"].path).read_text()
    assert "已驗證整合 commit：" in text and "儲存庫：" in text


def test_existing_split_acceptance_keeps_its_prompt(local_action, tmp_path):
    ctx = setup(local_action, tmp_path)
    runtime, _ = run(ctx)
    answer(ctx, runtime, "integrate_only")
    # The previous formatter's plain JSON task is immutable across an upgrade.
    from cafe.delivery.runner import execute_delivery
    from cafe.delivery.presentation import DeliveryPresentation, materialize_task
    from cafe.delivery.contracts import digest

    step = ctx[-1]
    visit = json.loads(next((ctx[2] / step / "systematic").glob("*.json")).read_text())
    iteration = visit["iteration"]
    snapshot, report, path = execute_delivery(
        root=ctx[0],
        issue_dir=ctx[2],
        step=step,
        iteration=iteration,
        playbook=ctx[5],
        state=runtime.blackboard,
    )
    output = ctx[2] / step / f"iteration_{iteration:03d}" / "output.md"
    presentation = DeliveryPresentation(
        ctx[2], ctx[0], ctx[5], runtime.blackboard, step, iteration, output
    )
    task = materialize_task(
        presentation,
        f"Action snapshot SHA256: {snapshot.digest}\n"
        f"Delivery result SHA256: {digest(report)}\n\n"
        + json.dumps(report, ensure_ascii=False, indent=2),
    )
    runtime, result = run(ctx)
    assert result.final_status_code == "BATON_CONFIRM_OUTPUT", result
    assert HumanTaskRecordStore(ctx[2]).get_task(task.id).prompt == task.prompt
    assert output.read_text() == task.prompt
    assert (
        len([t for t in HumanTaskRecordStore(ctx[2]).tasks() if t.policy_id == "delivery-outcome"])
        == 1
    )
