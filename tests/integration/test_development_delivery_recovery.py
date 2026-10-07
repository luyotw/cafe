"""I5/I7/I9: corrected local delivery and bounded large-repository reconciliation."""

import json
import subprocess
from pathlib import Path

import pytest

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.delivery.contracts import DeliveryBinding
from cafe.delivery.selection import approved_snapshot
from cafe.ui.human_tasks import apply_human_task_payload
from tests.integration import test_development_delivery_journey as journey
from tests.integration.test_development_delivery_journey import (
    approve_action,
    hook,
    pause,
    run_and_approve_host,
    setup_action,
)
from tests.integration.test_development_delivery_operations import git


@pytest.fixture
def local_action(tmp_path):
    return journey.local_action.__wrapped__(tmp_path)


@pytest.fixture
def fake_github(local_action, tmp_path, monkeypatch):
    return journey.fake_github.__wrapped__(local_action, tmp_path, monkeypatch)


def report(result):
    return json.loads(Path(result.context_updates["delivery_receipts_file"]).read_text())


def historical_issues(count, *, start=1):
    return [
        {
            "number": number,
            "title": "Historical",
            "body": "Unrelated",
            "html_url": f"https://github.com/owner/repo/issues/{number}",
            **({"pull_request": {}} if number % 2 else {}),
        }
        for number in range(start, start + count)
    ]


def select_one(local_action, tmp_path):
    context = setup_action(
        local_action,
        tmp_path,
        github=True,
        proposals=[
            {"id": "FUP-001", "title": "Selected", "body": "Original draft", "evidence": "path:1"}
        ],
    )
    approve_action(context, selected="FUP-001")
    return context


def finish_issue(context, *, cycles=5):
    for _ in range(cycles):
        result = run_and_approve_host(context)
        if report(result)["complete"]:
            return result
    return result


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_conflict_abort_develop_fix_fresh_review_and_authorization_deliver(
    local_action, tmp_path, legacy_receipt
):
    root, dest, _, _ = local_action
    (dest / "result.txt").write_text("Competing target")
    git(dest, "add", "result.txt")
    git(dest, "commit", "-m", "Competing target")
    original_target = git(dest, "rev-parse", "HEAD")
    context = setup_action(local_action, tmp_path, playbook="standard-qa", strategy="merge-commit")
    approve_action(context)
    first = report(run_and_approve_host(context))
    assert not first["complete"] and first["actions"]["integration"]["state"] == "unknown"
    assert git(dest, "rev-parse", "MERGE_HEAD") == git(root, "rev-parse", "HEAD")
    issue, state, phase, data, engine, kwargs = context[2:8]
    old_snapshot = approved_snapshot(
        issue,
        workflow_id=state.workflow_id,
        binding=DeliveryBinding.model_validate(data["steps"]["deliver"]["delivery"]),
    )
    assert (
        hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs).override_status_code
        == PhaseStatusCode.NEED_CLARIFICATION
    )
    if legacy_receipt:
        receipt_path = issue / "delivery" / old_snapshot.digest / "integration.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("local_before")
        receipt_path.write_text(json.dumps(receipt))
    assert "develop" in data["steps"]["deliver"]["allowed_goto"]
    assert data["steps"]["develop"]["on"]["await_agent"] == "review"
    assert data["steps"]["review"]["on"]["await_agent"] == "qa"
    assert data["steps"]["qa"]["on"]["await_agent"] == "pr"
    # Develop repairs the source only after the external destination is safely aborted.
    git(dest, "merge", "--abort")
    assert git(dest, "rev-parse", "HEAD") == original_target
    assert not git(dest, "status", "--porcelain")
    with pytest.raises(subprocess.CalledProcessError):
        git(root, "merge", "--no-ff", "--no-edit", original_target)
    (root / "result.txt").write_text("Reviewed resolution retaining both changes")
    git(root, "add", "result.txt")
    git(root, "commit", "-m", "Resolve source conflict")
    new_source = git(root, "rev-parse", "HEAD")
    assert new_source != old_snapshot.proposal.source_oid
    from cafe.core.capabilities import default_capability_definition_dirs, load_capability_registry
    from cafe.delivery.service import execute_snapshot

    with pytest.raises(ValueError):
        execute_snapshot(
            root=root,
            issue_dir=issue,
            snapshot=old_snapshot,
            registry=load_capability_registry(default_capability_definition_dirs(root)),
            step="deliver",
            iteration=2,
            output_file=kwargs["output_file"],
        )
    review = issue / "review.md"
    review.write_text(
        "Independent review of corrected source "
        + new_source
        + '\n\n## Follow-up Proposals\n\n```json\n{"proposals": []}\n```\n'
    )
    BlackboardStore(issue).put_artifact(
        state,
        ArtifactEntry(
            name="review_feedback",
            kind=ArtifactKind.DOCUMENT,
            version=2,
            updated_by="review",
            path=str(review),
        ),
    )
    qa = issue / "qa.md"
    qa.write_text("Acceptance passed for corrected source " + new_source)
    BlackboardStore(issue).put_artifact(
        state,
        ArtifactEntry(
            name="qa_feedback", kind=ArtifactKind.DOCUMENT, version=2, updated_by="qa", path=str(qa)
        ),
    )
    phase.iteration = 2
    phase.phase_name = "pr"
    output = issue / "pr" / "iteration_002" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("Reviewed corrected package " + new_source)
    (output.parent / "delivery_request.json").write_text(
        json.dumps(
            {
                "mode": "local",
                "strategy": "merge-commit",
                "target_branch": "develop",
                "destination": str(dest),
                "issue_repository": "",
            }
        )
    )
    kwargs.update(step_name="pr", step_def=data["steps"]["pr"], output_file=output, context={})
    assert hook(engine, "publish_output", "DevelopmentActionContext", kwargs).continue_pipeline
    task = HumanTaskRecordStore(issue).tasks()[-1]
    revised = (*context[:8], task, "deliver")
    approve_action(revised)
    current = approved_snapshot(
        issue,
        workflow_id=state.workflow_id,
        binding=DeliveryBinding.model_validate(data["steps"]["deliver"]["delivery"]),
    )
    assert current.task_id != old_snapshot.task_id and current.proposal.source_oid == new_source
    assert current.proposal.review_source.version == 2
    result = run_and_approve_host(revised)
    final = report(result)
    assert final["complete"]
    assert final["actions"]["integration"]["state"] == "succeeded"
    retained = final["previous_results"][old_snapshot.digest + ":integration"]
    assert retained["state"] == "settled" and retained["process"]["returncode"] != 0
    assert git(dest, "merge-base", "--is-ancestor", new_source, "HEAD") == ""
    assert git(dest, "rev-parse", "HEAD") == final["actions"]["integration"]["commit"]
    assert state.current_step != "done"
    assert hook(engine, "publish_output", "DevelopmentDeliveryOutcome", kwargs).continue_pipeline
    outcome = HumanTaskRecordStore(issue).tasks()[-1]
    pause(issue, state, "deliver")
    accepted = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=data,
        blackboard=state,
        from_step="deliver",
        trigger="confirm_output",
        source="test",
        raw_payload={"task": outcome.policy_id, "human_task_id": outcome.id, "decision": "confirm"},
    )
    assert accepted.target == "done", accepted.rejection


def test_known_created_issue_is_verified_directly_in_large_repository(
    local_action, tmp_path, fake_github
):
    context = select_one(local_action, tmp_path)
    initial = report(finish_issue(context))
    assert initial["complete"]
    external = json.loads(fake_github.read_text())
    original = external["issues"][0]
    external["issues"] += historical_issues(1200, start=2)
    external["observations"] = []
    fake_github.write_text(json.dumps(external))
    final = report(run_and_approve_host(context))
    assert final["complete"]
    assert final["actions"]["FUP-001"]["url"] == original["html_url"]
    external = json.loads(fake_github.read_text())
    assert "repos/owner/repo/issues/1" in external["observations"]
    assert external["effects"] == ["merge", "issue"]
    assert hook(
        context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
    ).continue_pipeline


@pytest.mark.parametrize("duplicate", [False, True])
def test_lost_response_large_observation_resumes_and_never_recreates(
    local_action, tmp_path, fake_github, duplicate
):
    context = select_one(local_action, tmp_path)
    external = json.loads(fake_github.read_text())
    external["lose_issue_response"] = True
    fake_github.write_text(json.dumps(external))
    run_and_approve_host(context)
    lost = report(run_and_approve_host(context))
    assert not lost["complete"] and lost["actions"]["FUP-001"]["state"] == "unknown"
    external = json.loads(fake_github.read_text())
    external["issues"] += historical_issues(1200, start=2)
    if duplicate:
        external["issues"].append(
            {
                **external["issues"][0],
                "number": 1202,
                "html_url": "https://github.com/owner/repo/issues/1202",
            }
        )
    external["observations"] = []
    fake_github.write_text(json.dumps(external))
    partial = report(run_and_approve_host(context))
    assert not partial["complete"]
    scanned = json.loads(fake_github.read_text())["observations"]
    assert len([url for url in scanned if "?state=all" in url]) <= 10
    final = report(run_and_approve_host(context))
    external = json.loads(fake_github.read_text())
    assert any("page=11" in url for url in external["observations"])
    assert final["complete"] is (not duplicate)
    if duplicate:
        assert final["actions"]["FUP-001"]["state"] == "unknown"
        assert final["actions"]["FUP-001"]["error"] == "ambiguous_issue_marker"
    else:
        assert final["actions"]["FUP-001"]["url"] == external["issues"][0]["html_url"]
        assert hook(
            context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
        ).continue_pipeline
    assert external["effects"] == ["merge", "issue"]


def test_new_issue_creation_has_no_repository_history_size_limit(
    local_action, tmp_path, fake_github
):
    external = json.loads(fake_github.read_text())
    external["issues"] = historical_issues(1200)
    fake_github.write_text(json.dumps(external))
    context = select_one(local_action, tmp_path)
    final = report(finish_issue(context))
    assert final["complete"]
    external = json.loads(fake_github.read_text())
    assert external["effects"] == ["merge", "issue"]
    assert len(external["issues"]) == 1201
    assert final["actions"]["FUP-001"]["url"].endswith("/1201")


@pytest.mark.parametrize("fault", ["absent", "deleted_prefix"])
def test_unknown_observation_never_authorizes_recreation_and_resumes_conservatively(
    local_action, tmp_path, fake_github, fault
):
    context = select_one(local_action, tmp_path)
    external = json.loads(fake_github.read_text())
    external["lose_issue_response"] = True
    fake_github.write_text(json.dumps(external))
    run_and_approve_host(context)
    lost = report(run_and_approve_host(context))
    assert lost["actions"]["FUP-001"]["state"] == "unknown"
    external = json.loads(fake_github.read_text())
    created = external["issues"][0]
    external["issues"] = historical_issues(1200, start=2)
    if fault == "deleted_prefix":
        external["issues"].append(
            {**created, "number": 1202, "html_url": "https://github.com/owner/repo/issues/1202"}
        )
    external["observations"] = []
    fake_github.write_text(json.dumps(external))
    assert not report(run_and_approve_host(context))["complete"]
    if fault == "deleted_prefix":
        external = json.loads(fake_github.read_text())
        external["issues"] = external["issues"][100:]
        fake_github.write_text(json.dumps(external))
    final = report(finish_issue(context, cycles=3))
    assert final["complete"] is (fault == "deleted_prefix")
    if fault == "absent":
        assert final["actions"]["FUP-001"]["state"] == "unknown"
        assert (
            hook(
                context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
            ).override_status_code
            == PhaseStatusCode.NEED_CLARIFICATION
        )
    assert json.loads(fake_github.read_text())["effects"] == ["merge", "issue"]


@pytest.mark.parametrize("corruption", ["receipt", "report"])
def test_domain_checks_reject_stale_outcome_through_public_response(
    local_action, tmp_path, corruption
):
    context = setup_action(local_action, tmp_path)
    approve_action(context)
    final = run_and_approve_host(context)
    assert report(final)["complete"]
    assert hook(
        context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
    ).continue_pipeline
    issue, state = context[2:4]
    task = HumanTaskRecordStore(issue).tasks()[-1]
    path = Path(final.context_updates["delivery_receipts_file"])
    if corruption == "receipt":
        path = path.parent / "integration.json"
    contents = json.loads(path.read_text())
    if corruption == "receipt":
        contents["commit"] = "f" * 40
    else:
        contents["actions"]["integration"]["commit"] = "f" * 40
    path.write_text(json.dumps(contents))
    pause(issue, state, "deliver")
    response = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=context[5],
        blackboard=state,
        from_step="deliver",
        trigger="confirm_output",
        source="test",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "confirm"},
    )
    assert response.rejection is not None and response.target is None
    assert HumanTaskRecordStore(issue).get_result(task.id) is None


def test_creation_response_identity_survives_failed_verification_and_large_history(
    local_action, tmp_path, fake_github
):
    external = json.loads(fake_github.read_text())
    external["fail_issue_observation"] = True
    fake_github.write_text(json.dumps(external))
    context = select_one(local_action, tmp_path)
    run_and_approve_host(context)
    partial = report(run_and_approve_host(context))
    assert not partial["complete"]
    action = partial["actions"]["FUP-001"]
    assert action["state"] == "unknown"
    assert action["issue_identity"] == {
        "number": 1,
        "url": "https://github.com/owner/repo/issues/1",
    }
    assert action["process"]["returncode"] == 0
    external = json.loads(fake_github.read_text())
    external["fail_issue_observation"] = False
    external["issues"] += historical_issues(1200, start=2)
    external["observations"] = []
    fake_github.write_text(json.dumps(external))
    final = report(run_and_approve_host(context))
    assert final["complete"]
    external = json.loads(fake_github.read_text())
    assert "repos/owner/repo/issues/1" in external["observations"]
    assert not any("?state=all" in url for url in external["observations"])
    assert external["effects"] == ["merge", "issue"]
    assert hook(
        context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
    ).continue_pipeline


@pytest.mark.parametrize("field", ["body", "html_url", "number"])
def test_known_issue_changed_identity_cannot_complete_or_recreate(
    local_action, tmp_path, fake_github, field
):
    context = select_one(local_action, tmp_path)
    assert report(finish_issue(context))["complete"]
    external = json.loads(fake_github.read_text())
    external["issues"][0][field] = {
        "body": "Changed draft",
        "html_url": "https://github.com/other/repo/issues/1",
        "number": True,
    }[field]
    fake_github.write_text(json.dumps(external))
    current = report(run_and_approve_host(context))
    assert not current["complete"]
    assert current["actions"]["FUP-001"]["state"] == "unknown"
    assert json.loads(fake_github.read_text())["effects"] == ["merge", "issue"]
    assert (
        hook(
            context[6], "publish_output", "DevelopmentDeliveryOutcome", context[7]
        ).override_status_code
        == PhaseStatusCode.NEED_CLARIFICATION
    )
