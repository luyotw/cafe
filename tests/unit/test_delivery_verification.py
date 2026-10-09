"""Post-merge checks cannot be replaced by a merge receipt or stale Actions run."""

from copy import deepcopy

import pytest

from cafe.delivery.contracts import DeliveryVerification
from cafe.delivery.operations import OperationError
from cafe.delivery.verification import observe_delivery, validate_verification, wait_for_delivery
from tests.unit.test_development_delivery import authority, proposal
from cafe.delivery.contracts import approve_selection

COMMIT = "c" * 40
PATH = ".github/workflows/deploy.yml"


def snapshot():
    p = proposal(
        proposals=[],
        verification=DeliveryVerification.model_validate(
            {
                "workflows": [{"path": PATH, "jobs": {"deploy": ["Deploy", "Public checks"]}}],
            }
        ),
    )
    return approve_selection(p, authority(p, "integrate_only", ""))


def responses():
    return {
        "workflow_runs": [
            {
                "id": 123,
                "run_attempt": 1,
                "path": PATH,
                "head_sha": COMMIT,
                "head_branch": "develop",
                "event": "push",
                "repository": {"full_name": "owner/repo"},
                "status": "completed",
                "conclusion": "success",
            }
        ],
    }, {
        "total_count": 1,
        "jobs": [
            {
                "name": "deploy",
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {"name": "Deploy", "status": "completed", "conclusion": "success"},
                    {"name": "Public checks", "status": "completed", "conclusion": "success"},
                ],
            }
        ],
    }


def install(monkeypatch, runs=None, jobs=None):
    defaults = responses()
    runs = defaults[0] if runs is None else runs
    jobs = defaults[1] if jobs is None else jobs
    calls = []

    def api(self, endpoint):
        assert endpoint.startswith("repos/owner/repo/actions/")
        calls.append(endpoint)
        return deepcopy(jobs if "/jobs?" in endpoint else runs)

    monkeypatch.setattr("cafe.delivery.verification.Commands.api", api)
    return calls


def test_exact_commit_success_and_required_deploy_public_steps(monkeypatch):
    calls = install(monkeypatch)
    current = observe_delivery(snapshot(), COMMIT)
    assert current["state"] == "succeeded"
    assert current["commit"] == COMMIT and current["workflows"][0]["attempt"] == 1
    assert "head_sha=" + COMMIT in calls[0] and "branch=develop" in calls[0]
    validate_verification(
        snapshot(), {"actions": {"integration": {"commit": COMMIT}}, "verification": current}
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "d" * 40),
        ("event", "pull_request"),
        ("head_branch", "main"),
        ("path", ".github/workflows/other.yml"),
        ("repository", {"full_name": "other/repo"}),
    ],
)
def test_unrelated_workflow_is_unknown(monkeypatch, field, value):
    runs, jobs = responses()
    runs["workflow_runs"][0][field] = value
    install(monkeypatch, runs, jobs)
    assert observe_delivery(snapshot(), COMMIT)["state"] == "unknown"


@pytest.mark.parametrize(
    "state,conclusion,expected",
    [
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "failed"),
        ("completed", "skipped", "failed"),
        ("completed", "timed_out", "failed"),
    ],
)
def test_non_success_never_completes(monkeypatch, state, conclusion, expected):
    runs, jobs = responses()
    runs["workflow_runs"][0].update(status=state, conclusion=conclusion)
    install(monkeypatch, runs, jobs)
    assert wait_for_delivery(snapshot(), COMMIT, timeout=0)["state"] == expected


@pytest.mark.parametrize(
    "change",
    [
        "missing_job",
        "duplicate_job",
        "skipped_job",
        "missing_public",
        "skipped_deploy",
        "failed_public",
        "truncated_jobs",
    ],
)
def test_successful_workflow_does_not_hide_unverified_obligations(monkeypatch, change):
    runs, jobs = responses()
    job = jobs["jobs"][0]
    if change == "missing_job":
        job["name"] = "other"
    if change == "duplicate_job":
        jobs["jobs"].append(deepcopy(job))
        jobs["total_count"] = 2
    if change == "skipped_job":
        job["conclusion"] = "skipped"
    if change == "missing_public":
        job["steps"].pop()
    if change == "skipped_deploy":
        job["steps"][0]["conclusion"] = "skipped"
    if change == "failed_public":
        job["steps"][1]["conclusion"] = "failure"
    if change == "truncated_jobs":
        jobs["total_count"] = 2
    install(monkeypatch, runs, jobs)
    assert observe_delivery(snapshot(), COMMIT)["state"] in {"failed", "unknown"}


def test_missing_run_waits_then_succeeds_without_mutation(monkeypatch):
    runs, jobs = responses()
    count = 0

    def api(self, endpoint):
        nonlocal count
        if "/jobs?" in endpoint:
            return jobs
        count += 1
        return {"workflow_runs": []} if count == 1 else runs

    monkeypatch.setattr("cafe.delivery.verification.Commands.api", api)
    sleeps = []
    monkeypatch.setattr("cafe.delivery.verification.time.sleep", sleeps.append)
    assert wait_for_delivery(snapshot(), COMMIT)["state"] == "succeeded"
    assert count == 2 and sleeps == [10]


def test_unknown_api_failure_does_not_loop_or_pass(monkeypatch):
    def unavailable(*args):
        raise OperationError("command_failed", state="unknown")

    monkeypatch.setattr("cafe.delivery.verification.Commands.api", unavailable)
    assert wait_for_delivery(snapshot(), COMMIT)["state"] == "unknown"


def test_rerun_invalidates_shown_success(monkeypatch):
    install(monkeypatch)
    shown = observe_delivery(snapshot(), COMMIT)
    runs, jobs = responses()
    runs["workflow_runs"][0].update(run_attempt=2, status="in_progress", conclusion=None)
    install(monkeypatch, runs, jobs)
    with pytest.raises(ValueError, match="changed"):
        validate_verification(
            snapshot(), {"actions": {"integration": {"commit": COMMIT}}, "verification": shown}
        )


def test_legacy_missing_scope_and_explicit_offline_scope(monkeypatch):
    monkeypatch.setattr("cafe.delivery.verification.Commands.api", lambda *a: pytest.fail("API"))
    legacy = proposal(proposals=[])
    old = approve_selection(legacy, authority(legacy, "integrate_only", ""))
    assert observe_delivery(old, COMMIT)["state"] == "missing"
    new = legacy.model_copy(
        update={
            "verification": DeliveryVerification(
                not_required_reason="Confirmed local integration only; no publication."
            )
        }
    )
    offline = approve_selection(new, authority(new, "integrate_only", ""))
    assert observe_delivery(offline, COMMIT)["state"] == "not_required"
    with pytest.raises(ValueError):
        validate_verification(
            old,
            {
                "verification": {"state": "succeeded"},
                "actions": {"integration": {"commit": COMMIT}},
            },
        )


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"workflows": [], "not_required_reason": " "},
        {"workflows": [{"path": PATH, "jobs": {"deploy": []}}], "not_required_reason": "skip"},
        {"workflows": [{"path": "../other.yml", "jobs": {"deploy": []}}]},
    ],
)
def test_scope_must_be_explicit_and_bounded(raw):
    with pytest.raises(ValueError):
        DeliveryVerification.model_validate(raw)


@pytest.mark.parametrize("suffix", ["", "@develop", "@refs/heads/develop"])
def test_documented_workflow_path_ref_representation(monkeypatch, suffix):
    runs, jobs = responses()
    runs["workflow_runs"][0]["path"] += suffix
    install(monkeypatch, runs, jobs)
    assert observe_delivery(snapshot(), COMMIT)["state"] == "succeeded"


def test_different_workflow_path_ref_is_rejected(monkeypatch):
    runs, jobs = responses()
    runs["workflow_runs"][0]["path"] += "@main"
    install(monkeypatch, runs, jobs)
    assert observe_delivery(snapshot(), COMMIT)["state"] == "unknown"


def test_completed_legacy_acceptance_survives_upgrade_but_pending_does_not(tmp_path, monkeypatch):
    import json
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.delivery.closeout import accepted_choice, plan_text
    from cafe.delivery.contracts import digest
    from cafe.delivery.records import ActionStore
    from cafe.delivery.selection import save_shown_proposal, validate_complete_report
    from cafe.delivery.service import execute_snapshot

    # A pre-upgrade proposal and two genuine durable replies, with original legacy bytes.
    p = proposal(proposals=[])
    records = HumanTaskRecordStore(tmp_path)

    def task(step, policy, prompt):
        return records.materialize(
            workflow_id=p.workflow_id,
            step=step,
            iteration=1,
            trigger="confirm_output",
            policy_id=policy,
            prompt=prompt,
            expected_result={"input_schema": "decision"},
            continuations={"confirm": "_done"},
            assignee_type="user",
        )

    approval = task(p.approval_step, "delivery-review", f"Action proposal SHA256: {p.digest}")
    save_shown_proposal(tmp_path, approval, p)
    reply = records.complete(
        workflow_id=p.workflow_id,
        task_id=approval.id,
        payload={"decision": "integrate_only"},
        source="user",
    )
    auth = authority(p, "integrate_only", "")
    auth.update(task_id=approval.id, result_id=reply.id)
    old = approve_selection(p, auth)
    store = ActionStore(tmp_path, old)
    with store.locked():
        store.finish("integration", {"state": "succeeded", "commit": COMMIT})
    report = {
        "snapshot": old.digest,
        "complete": True,
        "remaining": [],
        "actions": {"integration": store.read("integration")},
        "previous_results": {},
    }
    (store.directory / "result.json").write_text(json.dumps(report))
    plan = {"version": 1, "workflow_id": p.workflow_id, "contract_sha256": "a" * 64, "cleanup": []}
    (tmp_path / "delivery/closeout.json").write_text(json.dumps(plan))
    terminal = task(
        "deliver",
        "delivery-outcome",
        f"Action snapshot SHA256: {old.digest}\n"
        f"Delivery result SHA256: {digest(report)}\n" + plan_text(plan),
    )
    monkeypatch.setattr("cafe.delivery.selection.validate_source_identity", lambda *a: None)
    kwargs = {"workflow_id": p.workflow_id, "contract_sha256": "a" * 64, "cleanup": []}
    assert accepted_choice(tmp_path, **kwargs) is None
    with pytest.raises(ValueError, match="verification"):
        validate_complete_report(tmp_path, old, report)
    with pytest.raises(ValueError, match="fresh PR"):
        execute_snapshot(
            root=tmp_path,
            issue_dir=tmp_path,
            snapshot=old,
            registry={},
            step="deliver",
            iteration=1,
            output_file=tmp_path / "output.md",
        )
    records.complete(
        workflow_id=p.workflow_id,
        task_id=terminal.id,
        payload={"decision": "confirm"},
        source="user",
    )
    assert accepted_choice(tmp_path, **kwargs)["choice"] == "leave"
    store.finish("integration", {"state": "unknown"})
    with pytest.raises(ValueError, match="receipts"):
        accepted_choice(tmp_path, **kwargs)
