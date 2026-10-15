"""Full cleanup preserves archives and unrelated edits while deleting exact resources."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.unit._kickoff_test_support import load_kickoff_module
from tests.unit.test_driver_contract_application import _proposal
from cafe.manager import ActivateConfirmedContract, activate_confirmed_contract


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def journey(tmp_path, monkeypatch, request):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root, worktree, remote = tmp_path / "repo", tmp_path / "feature", tmp_path / "remote.git"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "README").write_text("base\n")
    (root / ".gitignore").write_text(".cafe/\n")
    git(root, "add", "README", ".gitignore")
    git(root, "commit", "-m", "base")
    git(root, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "origin", "develop")
    git(root, "worktree", "add", "-b", "issue474", str(worktree))
    git(root, "push", "-u", "origin", "issue474")
    issue = worktree / ".cafe/issues/issue474"
    proposal = _proposal()
    proposal["manager"] = proposal.pop("driver")
    proposal["task_contract"] = {"user_required": [], "manager_confirmable": []}
    proposal["confirmation_contract"]["manager_confirmable"] = proposal["confirmation_contract"].pop("driver_confirmable")
    proposal["reactive_user_handoffs"]["alignment_checkpoint"] = "manager_resolvable_when_clear"
    proposal["checkout"] = {"kind": "worktree", "path": str(worktree)}
    activate_confirmed_contract(ActivateConfirmedContract(
        issue_dir=issue, issue_name="issue474", workflow_id="workflow-474", proposal=proposal,
        confirmed_by="user", confirmed_at=datetime.now(timezone.utc),
    ))
    (issue / "blackboard.json").write_text(json.dumps({"workflow_id": "workflow-474", "current_step": "done"}))
    (issue / "human_tasks.json").write_text(json.dumps({"tasks": []}))
    module = load_kickoff_module("cleanup_worktree")
    real_run = subprocess.run
    archive = module._archive(root, "issue474")

    def run(argv, **kwargs):
        if argv == ["cafe", "close", "--archive-only", "--issue", "issue474"]:
            assert Path(kwargs["cwd"]) == worktree
            if getattr(request, "param", None) == "real_archive":
                from typer.testing import CliRunner
                from cafe.ui.cli import app
                from cafe.core.git import GitOperations
                from unittest.mock import patch

                with monkeypatch.context() as context:
                    context.chdir(worktree)
                    with patch("cafe.ui.cli.GitOperations", GitOperations):
                        result = CliRunner().invoke(app, argv[1:])
                if result.exit_code:
                    raise subprocess.CalledProcessError(result.exit_code, argv, output=result.output)
                return subprocess.CompletedProcess(argv, 0, stdout=result.output)
            archive.parent.mkdir(parents=True)
            shutil.move(str(issue), str(archive))
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", run)
    return module, root, worktree, remote, issue, archive


def test_full_cleanup_archives_and_removes_local_and_remote_resources(journey):
    module, root, worktree, remote, issue, archive = journey
    (root / "README").write_text("unrelated user edit\n")
    result = module.cleanup(root, worktree, "issue474", "origin")
    assert result["status"] == "completed"
    assert archive.is_dir() and (archive / "manager/contract.json").is_file()
    assert not issue.exists() and not worktree.exists()
    assert git(root, "branch", "--list", "issue474") == ""
    assert git(root, "ls-remote", "--heads", "origin", "refs/heads/issue474") == ""
    assert (root / "README").read_text() == "unrelated user edit\n"


def test_dirty_feature_is_refused_before_archiving_or_deleting(journey):
    module, root, worktree, remote, issue, archive = journey
    (worktree / "unsaved").write_text("retain me")
    with pytest.raises(ValueError, match="uncommitted|untracked"):
        module.cleanup(root, worktree, "issue474", "origin")
    assert issue.is_dir() and not archive.exists()
    assert (worktree / "unsaved").read_text() == "retain me"
    assert git(root, "ls-remote", "--heads", "origin", "refs/heads/issue474")


def test_advanced_remote_is_refused_before_any_cleanup(journey):
    module, root, worktree, remote, issue, archive = journey
    (root / "new").write_text("another change")
    git(root, "add", "new")
    git(root, "commit", "-m", "advanced")
    git(root, "push", "origin", "HEAD:refs/heads/issue474")
    with pytest.raises(ValueError, match="remote feature branch"):
        module.cleanup(root, worktree, "issue474", "origin")
    assert issue.is_dir() and worktree.is_dir() and not archive.exists()
    assert git(root, "branch", "--list", "issue474")


@pytest.fixture
def correction(journey, monkeypatch, request):
    """Real Git resources with separately retained successful delivery receipts.

    The owning acceptance validator is tested separately below; this fixture
    supplies its accepted selection to exercise cleanup's resource checks.
    """
    from cafe.delivery.contracts import ActionProposal, ActionSnapshot
    from cafe.delivery.records import ActionStore
    import inspect_delivery_closeout

    module, root, worktree, remote, issue, archive = journey
    mode = getattr(request, "param", "local")
    github = {}

    def integrated_snapshot(branch):
        p = ActionProposal(workflow_id="workflow-474", approval_step="pr", approval_iteration=1,
            repository="example/repo" if mode == "github" else git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"),
            source_oid=git(worktree, "rev-parse", "HEAD"), source_branch=branch,
            target_branch="develop", target_oid=git(root, "rev-parse", "HEAD"),
            mode=mode, strategy="merge" if mode == "github" else "merge-commit",
            destination="" if mode == "github" else str(root),
            pr_number=(1 if branch == "issue474" else 2) if mode == "github" else None)
        snapshot = ActionSnapshot(proposal=p, proposal_digest=p.digest,
            task_id=f"approve-{branch}", result_id=f"result-{branch}", selected=())
        git(root, "merge", "--no-ff", "-m", "integrate", branch)
        store = ActionStore(issue, snapshot)
        with store.locked():
            store.finish("integration", {"state": "succeeded", "commit": git(root, "rev-parse", "HEAD")})
        github[str(p.pr_number)] = {"state": "MERGED", "headRefOid": p.source_oid,
            "headRefName": branch, "baseRefName": "develop",
            "mergeCommit": {"oid": git(root, "rev-parse", "HEAD")}}
        return snapshot, store.read("integration")

    old, old_receipt = integrated_snapshot("issue474")
    git(worktree, "checkout", "-b", "issue474-date-fix")
    (worktree / "correction").write_text("fixed\n")
    git(worktree, "add", "correction")
    git(worktree, "commit", "-m", "correct")
    git(worktree, "push", "-u", "origin", "issue474-date-fix")
    current, receipt = integrated_snapshot("issue474-date-fix")
    report = {"actions": {"integration": receipt},
              "previous_results": {f"{old.digest}:integration": old_receipt}}
    (issue / "delivery" / current.digest / "result.json").write_text(json.dumps(report))
    monkeypatch.setattr(inspect_delivery_closeout, "inspect", lambda *a, **k: {
        "status": "accepted", "selection": {"choice": "cleanup", "snapshot": current.digest}})
    if mode == "github":
        previous_run = module.subprocess.run

        def run(argv, **kwargs):
            if argv[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(github[argv[3]]))
            return previous_run(argv, **kwargs)

        monkeypatch.setattr(module.subprocess, "run", run)
    return journey, current, old


def test_accepted_correction_cleans_both_exact_branches_and_preserves_unrelated(correction):
    (module, root, worktree, remote, issue, archive), current, old = correction
    git(root, "branch", "issue474-unrelated")
    result = module.cleanup(root, worktree, "issue474", "origin")
    assert result["branches"] == ["issue474", "issue474-date-fix"]
    assert result["branch"] == "issue474-date-fix"
    assert archive.is_dir() and not worktree.exists()
    assert (archive / "delivery" / old.digest / "integration.json").is_file()
    for branch in result["branches"]:
        assert git(root, "branch", "--list", branch) == ""
        assert git(root, "ls-remote", "--heads", "origin", f"refs/heads/{branch}") == ""
    assert git(root, "branch", "--list", "issue474-unrelated")


@pytest.mark.parametrize("change", ["current_tip", "original_tip", "remote_tip", "receipt", "retained",
                                    "other_worktree", "target", "foreign_branch"])
def test_correction_rejects_changed_or_unproven_resources_before_archive(correction, change, tmp_path):
    (module, root, worktree, remote, issue, archive), current, old = correction
    if change == "current_tip":
        (worktree / "correction").write_text("new work\n")
        git(worktree, "commit", "-am", "advanced")
    elif change == "original_tip":
        git(root, "branch", "-f", "issue474", current.proposal.source_oid)
    elif change == "remote_tip":
        git(root, "push", "origin", f"{current.proposal.source_oid}:refs/heads/issue474")
    elif change == "receipt":
        path = issue / "delivery" / old.digest / "integration.json"
        value = json.loads(path.read_text()); value["state"] = "unknown"
        path.write_text(json.dumps(value))
    elif change == "retained":
        path = issue / "delivery" / current.digest / "result.json"
        value = json.loads(path.read_text()); value["previous_results"] = {}
        path.write_text(json.dumps(value))
    elif change == "other_worktree":
        git(root, "worktree", "add", str(tmp_path / "other"), "issue474")
    elif change == "target":
        git(root, "reset", "--hard", old.proposal.source_oid)
    else:
        git(worktree, "checkout", "-b", "unrelated")
    with pytest.raises(ValueError):
        module.cleanup(root, worktree, "issue474", "origin")
    assert worktree.is_dir() and issue.is_dir() and not archive.exists()
    for branch in ("issue474", "issue474-date-fix"):
        assert git(root, "branch", "--list", branch)
        assert git(root, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")


def test_legacy_different_branch_does_not_gain_cleanup_authority(journey):
    module, root, worktree, remote, issue, archive = journey
    git(worktree, "checkout", "-b", "issue474-date-fix")
    with pytest.raises(ValueError, match="another branch"):
        module.cleanup(root, worktree, "issue474", "origin")
    assert issue.exists() and not archive.exists()


@pytest.mark.parametrize("correction", ["github"], indirect=True)
def test_github_correction_checks_each_exact_merged_pr(correction, monkeypatch):
    (module, root, worktree, remote, issue, archive), current, old = correction
    result = module.cleanup(root, worktree, "issue474")
    assert result["branches"] == ["issue474", "issue474-date-fix"]
    assert archive.is_dir() and not worktree.exists()
    # No remote was authorized, so both remote refs remain.
    assert git(root, "ls-remote", "--heads", "origin", "refs/heads/issue474")


@pytest.mark.parametrize("correction", ["github"], indirect=True)
def test_github_changed_merge_is_refused_before_archive(correction, monkeypatch):
    (module, root, worktree, remote, issue, archive), current, old = correction
    previous_run = module.subprocess.run

    def run(argv, **kwargs):
        result = previous_run(argv, **kwargs)
        if argv[:3] == ["gh", "pr", "view"]:
            data = json.loads(result.stdout)
            data["mergeCommit"]["oid"] = "a" * 40
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(data))
        return result

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="GitHub delivery identity"):
        module.cleanup(root, worktree, "issue474")
    assert issue.exists() and worktree.exists() and not archive.exists()


def test_archival_tip_change_stops_removal_and_preserves_archive(correction, monkeypatch):
    (module, root, worktree, remote, issue, archive), current, old = correction
    previous_run = module.subprocess.run

    def run(argv, **kwargs):
        result = previous_run(argv, **kwargs)
        if argv == ["cafe", "close", "--archive-only", "--issue", "issue474"]:
            git(root, "branch", "-f", "issue474", current.proposal.source_oid)
        return result

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="changed during cleanup"):
        module.cleanup(root, worktree, "issue474", "origin")
    assert archive.exists() and worktree.exists()
    assert git(root, "branch", "--list", "issue474-date-fix")


@pytest.mark.parametrize("journey", ["real_archive"], indirect=True)
def test_correction_cleanup_uses_real_cli_archive_with_explicit_issue(correction):
    import yaml

    (module, root, worktree, remote, issue, archive), current, old = correction
    (issue / "issue.yaml").write_text(yaml.safe_dump({
        "issue_name": "issue474", "worktree_path": str(worktree),
        "base_branch": "develop", "pr": {"auto_create": True},
    }))
    (issue / "cost-proof.txt").write_text("retained accounting evidence")
    result = module.cleanup(root, worktree, "issue474", "origin")
    assert result["status"] == "completed" and archive.exists() and not worktree.exists()
    assert (archive / "cost-proof.txt").read_text() == "retained accounting evidence"
    for branch in ("issue474", "issue474-date-fix"):
        assert git(root, "branch", "--list", branch) == ""
        assert git(root, "ls-remote", "--heads", "origin", f"refs/heads/{branch}") == ""


def test_default_prefills_full_cleanup_but_explicit_archive_preference_wins(tmp_path):
    git(tmp_path, "init", "-b", "develop")
    git(tmp_path, "remote", "add", "origin", "https://github.com/example/repo.git")
    module = load_kickoff_module("kickoff_inputs")
    request = {"schema_version": 1, "project_root": str(tmp_path), "issue_name": "new",
               "playbook_id": "subagent-flow"}
    report = module.assemble_kickoff(request)
    command = report["formatter_draft"]["cleanup"][-1]
    assert Path(command[1]).name == "cleanup_worktree.py"
    assert command[command.index("--worktree") + 1] == str(tmp_path / ".cafe/worktrees/new")
    assert command[-2:] == ["--remote", "origin"]
    assert "origin branch" in report["formatter_draft"]["cleanup_description"][-1]
    request["current_explicit_inputs"] = {"cleanup": [["cafe", "close", "--archive-only"]],
                                         "cleanup_description": ["Keep feature resources."]}
    explicit = module.assemble_kickoff(request)
    assert explicit["formatter_draft"]["cleanup"] == [["cafe", "close", "--archive-only"]]
    assert not (tmp_path / ".cafe/issues").exists()


@pytest.mark.parametrize("change", [None, "receipt", "report"])
def test_archived_acceptance_uses_correlated_receipts_without_live_source_queries(tmp_path, monkeypatch, change):
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.delivery.closeout import plan_text
    from cafe.delivery.contracts import DeliveryVerification, approve_selection, digest
    from cafe.delivery.records import ActionStore
    from cafe.delivery.selection import save_shown_proposal
    from tests.unit.test_development_delivery import proposal, authority

    p = proposal(proposals=[], verification=DeliveryVerification(not_required_reason="Reviewed PR only."))
    records = HumanTaskRecordStore(tmp_path)
    def task(step, policy, prompt):
        return records.materialize(workflow_id=p.workflow_id, step=step, iteration=1,
            trigger="confirm_output", policy_id=policy, prompt=prompt,
            expected_result={"input_schema": "decision"}, continuations={"confirm_cleanup": "_done"},
            assignee_type="user")
    approval = task(p.approval_step, "delivery-review", f"Action proposal SHA256: {p.digest}")
    save_shown_proposal(tmp_path, approval, p)
    reply = records.complete(workflow_id=p.workflow_id, task_id=approval.id,
                             payload={"decision": "integrate_only"}, source="user")
    auth = authority(p, "integrate_only", "")
    auth.update(task_id=approval.id, result_id=reply.id)
    snapshot = approve_selection(p, auth)
    store = ActionStore(tmp_path, snapshot)
    with store.locked():
        store.finish("integration", {"state": "succeeded", "commit": "c" * 40})
    report = {"snapshot": snapshot.digest, "complete": True, "remaining": [],
              "actions": {"integration": store.read("integration")}, "previous_results": {},
              "verification": {"state": "not_required", "commit": "c" * 40,
                               "reason": "Reviewed PR only."}}
    report_path = store.directory / "result.json"
    report_path.write_text(json.dumps(report))
    cleanup = [["cafe", "close", "--archive-only"]]
    plan = {"version": 1, "workflow_id": p.workflow_id, "contract_sha256": "a" * 64, "cleanup": cleanup}
    (tmp_path / "delivery/closeout.json").write_text(json.dumps(plan))
    terminal = task("deliver", "delivery-outcome",
                    f"Action snapshot SHA256: {snapshot.digest}\nDelivery result SHA256: {digest(report)}\n" + plan_text(plan))
    records.complete(workflow_id=p.workflow_id, task_id=terminal.id,
                     payload={"decision": "confirm_cleanup"}, source="user")
    from cafe.manager.closeout import accepted_delivery_result
    monkeypatch.setattr("cafe.delivery.selection.validate_source_identity",
                        lambda *a: pytest.fail("An archived outcome cannot query a live source checkout."))
    monkeypatch.setattr("cafe.delivery.verification.observe_delivery",
                        lambda *a, **k: pytest.fail("Do not re-execute observers after archival."))
    if change == "receipt":
        store.finish("integration", {"state": "unknown"})
    if change == "report":
        report["actions"]["integration"]["commit"] = "d" * 40
        report_path.write_text(json.dumps(report))
    if change:
        with pytest.raises(ValueError):
            accepted_delivery_result(tmp_path, p.workflow_id, archived=True)
    else:
        assert accepted_delivery_result(tmp_path, p.workflow_id, archived=True)["decision"] == "confirm_cleanup"
