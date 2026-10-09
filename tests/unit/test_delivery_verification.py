"""Approved observers are platform-neutral and fail closed on stale evidence."""
import hashlib
import json
import subprocess
import pytest

from cafe.delivery.contracts import DeliveryVerification, VerificationTool, approve_selection
from cafe.delivery.verification import run_tool, tool_bytes, validate_observation
from tests.unit.test_development_delivery import proposal, authority

COMMIT = "c" * 40


def tool_snapshot(root, code):
    path = root / "verify.py"
    path.write_text(code)
    tool = VerificationTool(owner="repository", path="verify.py",
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            options={"build_name": "custom CI"})
    p = proposal(proposals=[], verification=DeliveryVerification(scope="Production release", tool=tool))
    return approve_selection(p, authority(p, "integrate_only", ""))


def checker(state="succeeded", commit=None, evidence=True):
    return ("import json,sys\np=json.load(sys.stdin)\n"
            "print(json.dumps({'state':" + repr(state) + ", 'commit':"
            + (repr(commit) if commit else "p['commit']")
            + ", 'evidence':" + ("{'release':'custom-build-23'}" if evidence else "{}") + "}))\n")


def test_custom_provider_needs_no_core_adapter(tmp_path):
    snapshot = tool_snapshot(tmp_path, checker())
    result = run_tool(tmp_path, snapshot, COMMIT)
    assert result["state"] == "succeeded"
    assert result["evidence"] == {"release": "custom-build-23"}


@pytest.mark.parametrize("state", ["pending", "failed", "unknown"])
def test_zero_exit_reports_actual_check_state(tmp_path, state):
    assert run_tool(tmp_path, tool_snapshot(tmp_path, checker(state)), COMMIT)["state"] == state


@pytest.mark.parametrize("code", [
    "raise SystemExit(1)", "print('not-json')", checker(commit="d"*40),
    checker(evidence=False), "print('{}')", "print('x'*70000)",
    "import local_helper",
])
def test_invalid_exit_output_version_and_local_import_never_pass(tmp_path, code):
    (tmp_path / "local_helper.py").write_text(checker())
    result = run_tool(tmp_path, tool_snapshot(tmp_path, code), COMMIT)
    assert result["state"] == "unknown" and not result["retryable"]


def test_timeout_is_retryable_unknown(tmp_path, monkeypatch):
    snapshot = tool_snapshot(tmp_path, checker())
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("tool", 30)
    monkeypatch.setattr("cafe.delivery.verification.subprocess.run", timeout)
    assert run_tool(tmp_path, snapshot, COMMIT)["retryable"] is True


def test_replaced_script_is_not_executed(tmp_path):
    snapshot = tool_snapshot(tmp_path, checker())
    (tmp_path / "verify.py").write_text("raise SystemExit('replacement')")
    with pytest.raises(ValueError, match="changed"):
        run_tool(tmp_path, snapshot, COMMIT)


def test_child_executes_verified_bytes_even_if_path_changes(tmp_path, monkeypatch):
    snapshot = tool_snapshot(tmp_path, checker())
    real_run = subprocess.run
    def replace_then_run(*a, **kw):
        (tmp_path / "verify.py").write_text("raise SystemExit(99)")
        return real_run(*a, **kw)
    monkeypatch.setattr("cafe.delivery.verification.subprocess.run", replace_then_run)
    assert run_tool(tmp_path, snapshot, COMMIT)["state"] == "succeeded"


def test_symlink_and_traversal_are_rejected(tmp_path):
    snapshot = tool_snapshot(tmp_path, checker())
    original = tmp_path / "verify.py"
    external = tmp_path / "external.py"
    original.rename(external)
    original.symlink_to(external)
    with pytest.raises(ValueError, match="approved owner"):
        tool_bytes(tmp_path, snapshot.proposal.verification.tool)
    for path in ("../verify.py", "/tmp/verify.py", "verify.sh"):
        with pytest.raises(ValueError):
            VerificationTool(owner="repository", path=path, sha256="a"*64)


@pytest.mark.parametrize("raw", [
    {}, {"scope": "CI"}, {"not_required_reason": " "},
    {"scope": "CI", "tool": {"owner": "repository", "path": "x.py", "sha256": "a"*64},
     "not_required_reason": "skip"},
])
def test_verification_scope_is_explicit(raw):
    with pytest.raises(ValueError):
        DeliveryVerification.model_validate(raw)


def test_scope_and_options_are_not_a_ci_dsl():
    tool = VerificationTool(owner="repository", path="x.py", sha256="a"*64,
                            options={"any_platform": {"build": 23}})
    assert DeliveryVerification(scope="Agreed checks", tool=tool).tool.options["any_platform"]


def test_success_requires_exact_commit_and_evidence():
    with pytest.raises(ValueError):
        validate_observation({"state": "succeeded", "commit": "d"*40, "evidence": "build"}, COMMIT)


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
    with pytest.raises(ValueError, match="fresh delivery action"):
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
