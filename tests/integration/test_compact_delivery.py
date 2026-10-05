"""I6/I7: approved delivery uses real Git and retains exact external evidence."""

from pathlib import Path
import json
import subprocess
import sys

import pytest

from tests.unit.test_compact_delivery import compact_request, compact_proposal, activate, git, make_ready
from tests.unit._kickoff_test_support import SCRIPT_ROOT


def direct_ready(compact_request, compact_proposal):
    from cafe.manager.delivery import prepare_compact_delivery
    root = Path(compact_request["project_root"])
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    # Preserve an unrelated user-owned staged edit as pre-existing evidence.
    (root / "user.txt").write_text("user work")
    git(root, "add", "user.txt")
    from cafe.manager.file_scope import prepare_file_scope
    compact_proposal["file_scope"] = prepare_file_scope(root, compact_proposal["file_scope"]["paths"])
    compact_proposal["delivery_contract"] = prepare_compact_delivery(root, {
        "schema_version": 4, "route": "direct", "remote": "origin",
        "branch": "feature", "effects": ["commit", "push"]}, issue_name="sample")
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    (root / "app.py").write_text("approved work")
    make_ready(root, issue)
    return root, issue


def closeout(root, issue, *args):
    return subprocess.run([sys.executable, str(SCRIPT_ROOT / "execute_closeout.py"),
        "--project-root", str(root), "--issue-dir", str(issue), "--issue-name", "sample",
        "--workflow-id", "workflow", *args], text=True, capture_output=True, timeout=30)


def test_direct_delivery_pushes_only_approved_changes_and_preserves_user_staging(compact_request, compact_proposal):
    root, issue = direct_ready(compact_request, compact_proposal)
    assert closeout(root, issue, "--initialize").returncode == 0
    commit = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "0")
    assert commit.returncode == 0, commit.stderr
    assert git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "app.py"
    assert git(root, "diff", "--cached", "--name-only") == "user.txt"
    push = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    assert push.returncode == 0, push.stderr
    evidence = json.loads((issue / "delivery_result.json").read_text())
    assert evidence["delivered"] and evidence["branch"] == "feature"
    assert evidence["commit"] == git(root, "rev-parse", "HEAD")
    assert git(root, "ls-remote", "origin", "refs/heads/feature").split()[0] == evidence["commit"]
    replay = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    assert replay.returncode != 0


def test_successful_commit_failed_push_retains_partial_nonreplayable_evidence(compact_request, compact_proposal):
    root, issue = direct_ready(compact_request, compact_proposal)
    assert closeout(root, issue, "--initialize").returncode == 0
    assert closeout(root, issue, "--execute", "--stage", "deliver", "--index", "0").returncode == 0
    head = git(root, "rev-parse", "HEAD")
    remote = Path(git(root, "remote", "get-url", "origin"))
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    failed = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    assert failed.returncode != 0
    evidence = json.loads((root / ".git/cafe/closeout/sample/workflow.json").read_text())
    assert [c["status"] for c in evidence["commands"]["deliver"]] == ["succeeded", "failed"]
    assert git(root, "rev-parse", "HEAD") == head
    assert not (issue / "delivery_result.json").exists()
    hook.unlink()
    assert closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1").returncode != 0


@pytest.mark.parametrize("policy", ["deny", "require_approval"])
def test_pr_policy_retains_human_gate_without_dispatch(compact_request, compact_proposal, policy):
    from cafe.manager.delivery import publish_compact_pr
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs, PolicyDecision
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    registry = load_capability_registry(default_capability_definition_dirs(root))
    manifest = registry["cafe.pr.publish"]
    if policy == "deny":
        manifest = manifest.model_copy(update={"policy": PolicyDecision.DENY})
    else:
        manifest = manifest.model_copy(update={"approval": "required"})
    registry = {**registry, "cafe.pr.publish": manifest}
    result = publish_compact_pr(issue, root, output, registry=registry)
    assert result["delivered"] is False
    assert not (issue / "delivery_result.json").exists()


@pytest.mark.parametrize("approval_required", [False, True])
def test_pr_delivery_verifies_actual_branches_and_sha_without_duplicate_confirmation(
    compact_request, compact_proposal, monkeypatch, approval_required
):
    import cafe.core.capabilities as capabilities
    from cafe.manager.delivery import publish_compact_pr
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    url = "https://github.com/example/project/pull/1"
    invocations = []
    def github_publish(**kwargs):
        invocations.append(kwargs["request"])
        assert kwargs["request"].args["base"] == "main"
        return {"pr_url": url, "pr_number": "1", "action": "created"}, None
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", github_publish)
    original = subprocess.run
    def transport(argv, **kwargs):
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"url": url,
                "headRefName": "feature", "baseRefName": "main",
                "headRefOid": git(root, "rev-parse", "HEAD"), "state": "OPEN"}), "")
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", transport)
    registry = dict(capabilities.load_capability_registry(capabilities.default_capability_definition_dirs(root)))
    approval = {}
    if approval_required:
        from cafe.core.capability_approvals import CapabilityApprovalService
        registry["cafe.pr.publish"] = registry["cafe.pr.publish"].model_copy(update={"approval": "required"})
        pending = publish_compact_pr(issue, root, output, registry=registry)
        assert pending["needs_human_task"] and not invocations
        service = CapabilityApprovalService(issue_dir=issue, workflow_id="workflow", step="delivery", iteration=1)
        task = service.inspect(pending["task_id"])
        service.record_decision(pending["task_id"], {"decision": "approve",
            "workflow_id": "workflow", "task_id": pending["task_id"],
            "request_fingerprint": task["fingerprint"], "correlation_id": task["correlation_id"]})
        approval = {"approval_task_id": pending["task_id"], "correlation_id": task["correlation_id"]}
    result = publish_compact_pr(issue, root, output, registry=registry, **approval)
    assert result["delivered"] and result["pr"]["url"] == url
    assert len(invocations) == 1
    with pytest.raises(ValueError):
        publish_compact_pr(issue, root, output)
    assert len(invocations) == 1


def test_uncertain_pr_publication_is_retained_and_never_replayed(compact_request, compact_proposal, monkeypatch):
    import cafe.core.capabilities as capabilities
    from cafe.manager.delivery import publish_compact_pr
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    def disconnected(**kwargs):
        raise TimeoutError("connection lost after external dispatch")
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", disconnected)
    with pytest.raises(TimeoutError):
        publish_compact_pr(issue, root, output)
    assert json.loads((issue / "delivery_result.json").read_text())["status"] == "unknown"
    with pytest.raises(ValueError):
        publish_compact_pr(issue, root, output)
