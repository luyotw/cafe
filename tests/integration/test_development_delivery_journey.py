"""I1–I9: public hook/task/capability journeys with isolated Git and fake GitHub."""

import json
import shutil
from collections import UserDict
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.capability_approvals import CapabilityApprovalService
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_operations as local_operations
from tests.integration.test_development_delivery_operations import git


@pytest.fixture
def local_action(tmp_path):
    return local_operations.local_action.__wrapped__(tmp_path)


def graph(tmp_path, *, renamed=False, playbook="direct"):
    data = PlaybookLoader(project_root=tmp_path, global_root=tmp_path).load(playbook)
    if renamed:
        data = PlaybookLoader(project_root=tmp_path, global_root=tmp_path).load_model(
            playbook
        ).model.model_dump(mode="json", exclude_unset=True)
        names = {"pr": "package", "deliver": "ship", "develop": "build"}
        # JSON substitution is limited to declared machine identities in this fixture.
        raw = json.dumps(data)
        for old, new in names.items():
            raw = raw.replace('"' + old + '"', '"' + new + '"')
        raw = raw.replace('"delivery_actions"', '"chosen_bundle"').replace(
            '"delivery_result"', '"outcome_document"'
        )
        data = json.loads(raw)
        data["playbook"]["id"] = "renamed-delivery"
        for field in data["commands"]["prepare"].get("fields", []):
            if isinstance(field.get("write"), str):
                section, dot, key = field["write"].partition(".")
                field["write"] = names.get(section, section) + dot + key
        # An equivalent catalog must also declare the renamed correction artifact in its target skill.
        from cafe.skills.loader import SkillLoader

        target_skill = tmp_path / ".cafe" / "skills" / "cafe-develop"
        shutil.copytree(
            SkillLoader(project_root=tmp_path).get_skill_dir("cafe-develop"), target_skill
        )
        skill_file = target_skill / "SKILL.md"
        skill_file.write_text(skill_file.read_text().replace("delivery_result", "outcome_document"))
        catalog = tmp_path / ".cafe" / "playbooks"
        catalog.mkdir(parents=True, exist_ok=True)
        (catalog / "renamed-delivery.yaml").write_text(json.dumps(data))
        data = PlaybookLoader(project_root=tmp_path, global_root=tmp_path / "global").load(
            "renamed-delivery", strict=True
        )
    return data


def hook(engine, stage, name, kwargs):
    if name == "DevelopmentDeliveryOutcome":
        (kwargs["phase"].issue_dir / "next_step.txt").write_text(
            json.dumps(
                {"version": 1, "to_owner": "user", "to_step": "user", "intent": "confirm_output"}
            )
        )
    step = dict(kwargs["step_def"])
    step["hooks"] = {stage: [name]}
    return engine._run_hook_stage(stage, skill_name=step["skill"], **{**kwargs, "step_def": step})


def pause(issue, state, step, intent=HandoffIntent.CONFIRM_OUTPUT):
    store = BlackboardStore(issue)
    store.set_current_step(state, "user")
    store.update_handoff_contract(
        state,
        from_step=step,
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=intent,
        source="test",
    )


def setup_action(
    local_action, tmp_path, *, renamed=False, github=False, proposals=(), playbook="direct"
):
    root, dest, _, local = local_action
    data = graph(root, renamed=renamed, playbook=playbook)
    approval, delivery = ("package", "ship") if renamed else ("pr", "deliver")
    issue = root / ".cafe" / "issues" / "journey"
    issue.mkdir(parents=True)
    output = issue / approval / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Reviewed package\n")
    (issue / "next_step.txt").write_text(
        json.dumps(
            {"version": 1, "to_owner": "user", "to_step": "user", "intent": "confirm_output"}
        )
    )
    request = {
        "mode": "github" if github else "local",
        "strategy": "merge" if github else "ff-only",
        "target_branch": "develop",
        "destination": "" if github else str(dest),
        "issue_repository": "owner/repo" if proposals else "",
    }
    (output.parent / "delivery_request.json").write_text(json.dumps(request))
    store = BlackboardStore(issue)
    state = store.load_or_create(approval, playbook_id=data["playbook"]["id"])
    if data["steps"][approval]["delivery"].get("proposals_artifact") or proposals:
        from cafe.core.blackboard import ArtifactEntry, ArtifactKind

        review = issue / "review.md"
        review.write_text(
            "## Follow-up Proposals\n\n```json\n"
            + json.dumps({"proposals": list(proposals)})
            + "\n```\n"
        )
        store.put_artifact(
            state,
            ArtifactEntry(
                name="review_feedback",
                kind=ArtifactKind.DOCUMENT,
                version=1,
                updated_by="review",
                path=str(review),
            ),
        )
    phase = SimpleNamespace(
        issue_dir=issue,
        iteration=1,
        phase_name=approval,
        playbook=data,
        git_ops=SimpleNamespace(repo_path=root),
    )
    kwargs = {
        "phase": phase,
        "step_name": approval,
        "step_def": data["steps"][approval],
        "blackboard_state": state,
        "output_file": output,
        "status_code": PhaseStatusCode.CONFIRM_OUTPUT,
        "validated_pr_auto_create": github,
        "context": {"pr_number": "23"},
    }
    from cafe.skills.loader import SkillLoader

    engine = GenericPhase(SkillLoader(project_root=root, global_root=tmp_path))
    result = hook(engine, "publish_output", "DevelopmentActionContext", kwargs)
    assert result.continue_pipeline, result.context_updates
    task = HumanTaskRecordStore(issue).get_task(
        result.context_updates["delivery_action_review_task"]
    )
    return root, dest, issue, state, phase, data, engine, kwargs, task, delivery


def approve_action(context, *, selected=""):
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    pause(issue, state, kwargs["step_name"])
    result = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=kwargs["step_name"],
        trigger="confirm_output",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "integrate_selected" if selected else "integrate_only",
            "feedback": selected,
        },
        source="test",
    )
    assert result.target == delivery, result.rejection
    phase.phase_name = delivery
    output = issue / delivery / f"iteration_{phase.iteration:03d}" / "output.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# Delivery\n")
    kwargs.update(
        step_name=delivery, step_def=data["steps"][delivery], output_file=output, context={}
    )


def run_and_approve_host(context):
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    result = hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    if result.override_status_code == PhaseStatusCode.NEED_PERMISSION:
        pending = HumanTaskRecordStore(issue).get_task(result.events[0]["task_id"])
        service = CapabilityApprovalService(
            issue_dir=issue, workflow_id=state.workflow_id, step=delivery, iteration=phase.iteration
        )
        approval = service.inspect(pending.id)
        service.record_decision(
            pending.id,
            {
                "decision": "approve",
                "workflow_id": state.workflow_id,
                "task_id": pending.id,
                "request_fingerprint": approval["fingerprint"],
                "correlation_id": approval["correlation_id"],
            },
        )
        result = hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    return result


@pytest.mark.parametrize("renamed", [False, True])
def test_local_delivery_has_no_github_and_waits_for_exact_result_acceptance(
    local_action, tmp_path, renamed
):
    context = setup_action(local_action, tmp_path, renamed=renamed)
    approve_action(context)
    result = run_and_approve_host(context)
    assert result.context_updates["delivery_complete"] == "true"
    root, dest, issue, state, phase, data, engine, kwargs, _, delivery = context
    assert git(dest, "rev-parse", "HEAD") == git(root, "rev-parse", "HEAD")
    assert state.current_step != "done"
    hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    task = next(t for t in HumanTaskRecordStore(issue).tasks() if t.policy_id == "delivery-outcome")
    pause(issue, state, delivery)
    accepted = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=delivery,
        trigger="confirm_output",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"},
        source="test",
    )
    assert accepted.target == "done", accepted.rejection


def test_invalid_proposal_selection_is_rejected_before_dispatch(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    _, dest, issue, state, _, data, _, kwargs, task, _ = context
    target = git(dest, "rev-parse", "HEAD")
    pause(issue, state, kwargs["step_name"])
    rejected = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=kwargs["step_name"],
        trigger="confirm_output",
        raw_payload={
            "task": task.policy_id,
            "human_task_id": task.id,
            "decision": "integrate_selected",
            "feedback": "FUP-999",
        },
        source="test",
    )
    assert rejected.target is None and rejected.rejection is not None
    assert git(dest, "rev-parse", "HEAD") == target


@pytest.fixture
def fake_github(local_action, tmp_path, monkeypatch):
    """Only the external executable is replaced; registry/approval/children stay real."""
    root, _, _, snapshot = local_action
    git(root, "remote", "add", "origin", "https://github.com/owner/repo.git")
    state_path = tmp_path / "github.json"
    state_path.write_text(
        json.dumps(
            {
                "pr": {
                    "number": 23,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": snapshot.proposal.source_oid, "ref": "feature"},
                    "base": {
                        "sha": snapshot.proposal.target_oid,
                        "ref": "develop",
                        "repo": {"full_name": "owner/repo"},
                    },
                },
                "issues": [],
                "effects": [],
                "lose_issue_response": False,
            }
        )
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ['CAFE_TEST_GITHUB'])
state = json.loads(path.read_text())
argv = sys.argv[1:]
if argv[:2] == ['pr', 'merge']:
    state['pr'].update(merged=True, merge_commit_sha='c'*40)
    state['effects'].append('merge')
    path.write_text(json.dumps(state))
    sys.exit(0)
endpoint = argv[1]
if '/pulls/' in endpoint:
    print(json.dumps(state['pr']))
elif '--method' in argv:
    draft = json.load(sys.stdin)
    draft['html_url'] = 'https://github.com/owner/repo/issues/' + str(len(state['issues'])+1)
    state['issues'].append(draft)
    state['effects'].append('issue')
    path.write_text(json.dumps(state))
    if state['lose_issue_response']:
        sys.exit(1)
    print(json.dumps(draft))
else:
    print(json.dumps(state['issues']))
""")
    gh.chmod(0o755)
    import os

    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CAFE_TEST_GITHUB", str(state_path))
    return state_path


@pytest.mark.parametrize("github", [False, True])
def test_selected_subset_creates_only_frozen_drafts_and_restart_does_not_replay(
    local_action, tmp_path, fake_github, github
):
    proposals = [
        {"id": "FUP-001", "title": "Unselected", "body": "Unselected body", "evidence": "path:1"},
        {
            "id": "FUP-002",
            "title": "Selected",
            "body": "Original selected body",
            "evidence": "path:2",
        },
    ]
    context = setup_action(local_action, tmp_path, github=github, proposals=proposals)
    approve_action(context, selected="FUP-002")
    result = run_and_approve_host(context)
    if result.override_status_code == PhaseStatusCode.NEED_PERMISSION:
        result = run_and_approve_host(context)
    assert result.context_updates["delivery_complete"] == "true"
    external = json.loads(fake_github.read_text())
    assert [issue["title"] for issue in external["issues"]] == ["Selected"]
    assert external["issues"][0]["body"].startswith(
        "Original selected body\n\n<!-- cafe-follow-up:"
    )
    assert external["effects"] == (["merge", "issue"] if github else ["issue"])
    rerun = run_and_approve_host(context)
    assert rerun.context_updates["delivery_complete"] == "true"
    assert json.loads(fake_github.read_text())["effects"] == external["effects"]


def test_lost_issue_response_preserves_partial_result_and_reconciles_without_duplicate(
    local_action, tmp_path, fake_github
):
    external = json.loads(fake_github.read_text())
    external["lose_issue_response"] = True
    fake_github.write_text(json.dumps(external))
    context = setup_action(
        local_action,
        tmp_path,
        github=True,
        proposals=[
            {"id": "FUP-001", "title": "Selected", "body": "Draft", "evidence": "path:1"},
        ],
    )
    approve_action(context, selected="FUP-001")
    run_and_approve_host(context)
    result = run_and_approve_host(context)
    report = json.loads(Path(result.context_updates["delivery_receipts_file"]).read_text())
    assert not report["complete"]
    assert report["actions"]["integration"]["state"] == "succeeded"
    assert report["actions"]["FUP-001"]["state"] == "unknown"
    recovered = run_and_approve_host(context)
    assert recovered.context_updates["delivery_complete"] == "true"
    assert json.loads(fake_github.read_text())["effects"] == ["merge", "issue"]


def test_stale_result_bytes_cannot_complete_workflow(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    result = run_and_approve_host(context)
    _, _, issue, state, _, data, engine, kwargs, _, delivery = context
    hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    task = next(t for t in HumanTaskRecordStore(issue).tasks() if t.policy_id == "delivery-outcome")
    path = Path(result.context_updates["delivery_receipts_file"])
    report = json.loads(path.read_text())
    report["complete"] = False
    path.write_text(json.dumps(report))
    pause(issue, state, delivery)
    rejected = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=delivery,
        trigger="confirm_output",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"},
        source="test",
    )
    assert rejected.target is None and rejected.rejection is not None


def test_changed_reviewed_artifact_requires_fresh_action_authority(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    kwargs["output_file"].write_text("Changed package\n")
    pause(issue, state, kwargs["step_name"])
    result = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=kwargs["step_name"],
        trigger="confirm_output",
        raw_payload={
            "task": "delivery-review",
            "human_task_id": task.id,
            "decision": "integrate_only",
        },
        source="test",
    )
    assert result.rejection is not None
    assert git(local_action[1], "rev-parse", "HEAD") != local_action[3].proposal.source_oid


def test_changed_source_blocks_dispatch_after_host_approval(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    root = local_action[0]
    (root / "new.txt").write_text("Changed source\n")
    git(root, "add", "new.txt")
    git(root, "commit", "-qm", "Changed source")
    result = run_and_approve_host(context)
    assert result.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert git(local_action[1], "rev-parse", "HEAD") == local_action[3].proposal.target_oid


def test_denied_host_approval_remains_nonterminal_without_dispatch(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    pending = hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    assert pending.override_status_code == PhaseStatusCode.NEED_PERMISSION
    service = CapabilityApprovalService(
        issue_dir=issue, workflow_id=state.workflow_id, step=delivery, iteration=1
    )
    task_id = pending.events[0]["task_id"]
    approval = service.inspect(task_id)
    service.record_decision(
        task_id,
        {
            "decision": "deny",
            "workflow_id": state.workflow_id,
            "task_id": task_id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
        },
    )
    hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    report = json.loads(next((issue / "delivery").glob("*/result.json")).read_text())
    assert not report["complete"] and report["remaining"] == ["integration"]
    assert git(dest, "rev-parse", "HEAD") == local_action[3].proposal.target_oid
    assert state.current_step != "done"


def test_revised_empty_selection_cannot_hide_an_earlier_unknown_issue(
    local_action, tmp_path, fake_github
):
    external = json.loads(fake_github.read_text())
    external["lose_issue_response"] = True
    fake_github.write_text(json.dumps(external))
    proposals = [{"id": "FUP-001", "title": "Selected", "body": "Original", "evidence": "path:1"}]
    context = setup_action(local_action, tmp_path, proposals=proposals)
    approve_action(context, selected="FUP-001")
    run_and_approve_host(context)
    run_and_approve_host(context)
    root, dest, issue, state, phase, data, engine, kwargs, old_task, delivery = context
    old = json.loads(next((issue / "delivery").glob("*/result.json")).read_text())
    assert not old["complete"]
    external = json.loads(fake_github.read_text())
    external["issues"] = []
    fake_github.write_text(json.dumps(external))
    phase.iteration = 2
    phase.phase_name = "pr"
    kwargs.update(
        step_name="pr",
        step_def=data["steps"]["pr"],
        output_file=issue / "pr" / "iteration_001" / "output.md",
        context={},
    )
    (issue / "next_step.txt").write_text(
        json.dumps(
            {"version": 1, "to_owner": "user", "to_step": "user", "intent": "confirm_output"}
        )
    )
    assert hook(engine, "publish_output", "DevelopmentActionContext", kwargs).continue_pipeline
    task = HumanTaskRecordStore(issue).tasks()[-1]
    revised = (*context[:8], task, delivery)
    approve_action(revised)
    hook(engine, "prepare_input", "DevelopmentDeliveryExecutor", kwargs)
    from cafe.delivery.contracts import DeliveryBinding
    from cafe.delivery.selection import approved_snapshot

    snapshot = approved_snapshot(
        issue,
        workflow_id=state.workflow_id,
        binding=DeliveryBinding.model_validate(data["steps"][delivery]["delivery"]),
    )
    report = json.loads((issue / "delivery" / snapshot.digest / "result.json").read_text())
    assert not report["complete"]
    assert any(
        item.startswith("previous:") and item.endswith(":FUP-001") for item in report["remaining"]
    )
    assert len(json.loads(fake_github.read_text())["effects"]) == 1

    path = issue / "delivery" / snapshot.digest / "result.json"
    report.update(complete=True, remaining=[], previous_results={})
    path.write_text(json.dumps(report))
    outcome = hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs)
    assert outcome.override_status_code == PhaseStatusCode.NEED_CLARIFICATION
    assert not any(
        task.policy_id == "delivery-outcome" for task in HumanTaskRecordStore(issue).tasks()
    )


def test_delivery_recovery_accepts_declared_mapping_feedback_route(local_action, tmp_path):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    phase.iteration = 1
    from cafe.core.hooks.delivery import _task

    recovery = _task(kwargs, "Recover the pending integration", trigger="need_clarification")
    pause(issue, state, delivery, HandoffIntent.NEED_CLARIFICATION)
    result = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step=delivery,
        trigger="need_clarification",
        raw_payload=UserDict(
            {
                "task": "delivery-recovery",
                "human_task_id": recovery.id,
                "feedback": "Retry observation without changing approved actions",
            }
        ),
        source="test",
    )
    assert result.target == delivery, result.rejection
    assert HumanTaskRecordStore(issue).get_result(recovery.id) is not None
    assert git(dest, "rev-parse", "HEAD") == local_action[3].proposal.target_oid


@pytest.mark.parametrize("renamed", [False, True])
def test_public_phase_execute_forwards_declared_delivery_and_current_receipts(
    local_action, tmp_path, renamed
):
    context = setup_action(local_action, tmp_path, renamed=renamed)
    approve_action(context)
    root, dest, issue, state, phase, data, engine, kwargs, task, delivery = context
    calls = []
    hook_context = {
        key: value
        for key, value in kwargs.items()
        if key not in {"step_def", "context", "status_code"}
    }

    def agent(prompt):
        calls.append(prompt)
        assert "delivery_receipts_file" in prompt and "delivery_complete: true" in prompt
        reports = list((issue / "delivery").glob("*/result.json"))
        report = json.loads(reports[-1].read_text())
        assert report["complete"] and report["actions"]["integration"]["commit"] == git(
            dest, "rev-parse", "HEAD"
        )
        (issue / "next_step.txt").write_text(
            json.dumps(
                {"version": 1, "to_owner": "user", "to_step": "user", "intent": "confirm_output"}
            )
        )
        return "Current delivery results presented"

    def execute():
        return engine.execute(
            skill_name=kwargs["step_def"]["skill"],
            step_def=kwargs["step_def"],
            agent_executor=agent,
            skill_invocation="/cafe-deliver_development",
            context={"next_step_path": str(issue / "next_step.txt")},
            output_file=kwargs["output_file"],
            hook_context=hook_context,
        )

    pending = execute()
    assert pending.status_code == PhaseStatusCode.NEED_PERMISSION
    assert not calls and not pending.published
    approval_task = next(
        task
        for task in HumanTaskRecordStore(issue).tasks()
        if task.policy_id == "capability-approval"
    )
    service = CapabilityApprovalService(
        issue_dir=issue, workflow_id=state.workflow_id, step=delivery, iteration=phase.iteration
    )
    approval = service.inspect(approval_task.id)
    service.record_decision(
        approval_task.id,
        {
            "decision": "approve",
            "workflow_id": state.workflow_id,
            "task_id": approval_task.id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
        },
    )
    completed = execute()
    assert completed.published and len(calls) == 1
    assert any(task.policy_id == "delivery-outcome" for task in HumanTaskRecordStore(issue).tasks())
    assert state.current_step != "done"


def test_qa_only_graph_empty_selection_needs_no_review_or_github(
    local_action, tmp_path, monkeypatch
):
    import subprocess

    native = subprocess.Popen

    def no_github(argv, *args, **kwargs):
        assert argv[0] != "gh"
        return native(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", no_github)
    context = setup_action(local_action, tmp_path, playbook="simple")
    assert "review_feedback" not in context[3].artifacts
    approve_action(context)
    result = run_and_approve_host(context)
    assert result.context_updates["delivery_complete"] == "true"
    assert git(local_action[1], "rev-parse", "HEAD") == local_action[3].proposal.source_oid
