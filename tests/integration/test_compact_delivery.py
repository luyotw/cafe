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
@pytest.mark.parametrize("wrong_repository", [False, True])
def test_pr_delivery_verifies_actual_branches_and_sha_without_duplicate_confirmation(
    compact_request, compact_proposal, monkeypatch, approval_required, wrong_repository
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
    url = "https://github.com/" + ("other/unapproved" if wrong_repository else "example/project") + "/pull/1"
    invocations = []
    def github_publish(**kwargs):
        invocations.append(kwargs["request"])
        assert kwargs["request"].args["base"] == "main"
        return {"pr_url": url, "pr_number": "1", "action": "created"}, None
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", github_publish)
    original = subprocess.run
    def transport(argv, **kwargs):
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, "example/project\n", "")
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"url": url,
                "headRefName": "feature", "baseRefName": "main",
                "headRefOid": git(root, "rev-parse", "HEAD"), "state": "OPEN",
                "headRepository": {"nameWithOwner": "example/project"}}), "")
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
    if wrong_repository:
        with pytest.raises(ValueError):
            publish_compact_pr(issue, root, output, registry=registry, **approval)
        assert json.loads((issue / "delivery_result.json").read_text())["status"] == "unknown"
        assert len(invocations) == 1
        return
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
    original = subprocess.run
    def repository_transport(argv, **kwargs):
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, "example/project\n", "")
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", repository_transport)
    def disconnected(**kwargs):
        raise TimeoutError("connection lost after external dispatch")
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", disconnected)
    with pytest.raises(TimeoutError):
        publish_compact_pr(issue, root, output)
    assert json.loads((issue / "delivery_result.json").read_text())["status"] == "unknown"
    with pytest.raises(ValueError):
        publish_compact_pr(issue, root, output)


@pytest.mark.parametrize("drift", ["scope", "fetch_url", "omitted_remote"])
def test_existing_custom_publication_hook_checks_scope_before_dispatch(compact_request, compact_proposal, monkeypatch, drift):
    from types import SimpleNamespace
    import cafe.core.capabilities as capabilities
    from cafe.core.hooks.native import GitHubPRCreator
    from cafe.core.status_codes import PhaseStatusCode
    from cafe.manager.file_scope import execution_scope_projection
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    git(root, "remote", "set-url", "--push", "origin", git(root, "remote", "get-url", "origin"))
    activate(issue, compact_proposal)
    context = execution_scope_projection(issue, root)
    output = issue / "custom-publish/iteration_001/output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    request_file = output.parent / "request.json"
    request_file.write_text(json.dumps({"capability": "cafe.pr.publish", "args": {
        "output": output.relative_to(root).as_posix(), "base": "main", "remote": "origin"}}))
    phase = SimpleNamespace(issue_dir=issue, phase_dir=output.parent.parent, iteration=1,
        git_ops=SimpleNamespace(get_repo_root=lambda: root))
    invocations = []
    def transport(**kwargs):
        invocations.append(kwargs["request"])
        return {"pr_url": "https://github.com/example/project/pull/1", "pr_number": "1"}, None
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", transport)
    hook = GitHubPRCreator()
    arguments = {"stage": "publish_output", "phase": phase, "step_name": "custom-publish",
        "step_def": {"capability_requests": ["cafe.pr.publish"], "behavior": {"publish_confirmation": True}},
        "output_file": output, "capability_request_file": request_file,
        "status_code": PhaseStatusCode.CONFIRMED, "validated_pr_auto_create": True,
        "execution_context": context}
    hook.run(**arguments)
    assert len(invocations) == 1
    if drift == "scope":
        (root / "outside.py").write_text("unapproved change after implementation")
    elif drift == "fetch_url":
        git(root, "remote", "set-url", "origin", "https://github.com/other/unapproved.git")
    else:
        changed_request = json.loads(request_file.read_text())
        changed_request["args"].pop("remote")
        request_file.write_text(json.dumps(changed_request))
    with pytest.raises(ValueError):
        hook.run(**arguments)
    assert len(invocations) == 1


def test_normal_commit_hook_cannot_deliver_unreviewed_index_blob(compact_request, compact_proposal):
    root, issue = direct_ready(compact_request, compact_proposal)
    hook = root / '.git/hooks/pre-commit'
    hook.write_text("#!/bin/sh\nblob=$(printf 'unreviewed hook contents' | git hash-object -w --stdin)\ngit update-index --cacheinfo 100644,$blob,app.py\n")
    hook.chmod(0o755)
    assert closeout(root, issue, '--initialize').returncode == 0
    commit = closeout(root, issue, '--execute', '--stage', 'deliver', '--index', '0')
    assert git(root, 'show', 'HEAD:app.py') == 'unreviewed hook contents'
    assert (root / 'app.py').read_text() == 'approved work'
    assert commit.returncode != 0
    assert closeout(root, issue, '--execute', '--stage', 'deliver', '--index', '1').returncode != 0
    assert not git(root, 'ls-remote', 'origin', 'refs/heads/feature')
    assert git(root, 'show', ':user.txt') == 'user work'
    assert not (issue / 'delivery_result.json').exists()


def test_fetch_endpoint_change_blocks_owned_publication_before_dispatch(compact_request, compact_proposal, monkeypatch):
    import cafe.core.capabilities as capabilities
    from cafe.manager.delivery import publish_compact_pr
    root = Path(compact_request["project_root"])
    git(root, "remote", "set-url", "--push", "origin", git(root, "remote", "get-url", "origin"))
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("unexpected external dispatch")
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", transport)
    git(root, "remote", "set-url", "origin", "https://github.com/other/unapproved.git")
    with pytest.raises(ValueError):
        publish_compact_pr(issue, root, output)
    assert not calls
    assert not (issue / "delivery_result.json").exists()


def test_owned_publication_uses_real_publisher_and_authorized_push_repository(compact_request, compact_proposal, tmp_path, monkeypatch):
    import os
    from cafe.manager.delivery import prepare_compact_delivery, publish_compact_pr
    root = Path(compact_request["project_root"])
    push_url = git(root, "remote", "get-url", "origin")
    git(root, "remote", "set-url", "--push", "origin", push_url)
    git(root, "remote", "set-url", "origin", "https://github.com/other/fetch-only.git")
    compact_proposal["delivery_contract"] = prepare_compact_delivery(root,
        compact_request["compact_inputs"]["delivery_contract"], issue_name="sample")
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    binary = tmp_path / "github-bin"
    binary.mkdir()
    log = tmp_path / "github-calls.jsonl"
    gh = binary / "gh"
    gh.write_text("#!" + sys.executable + '\n' + '''import json, os, sys
args = sys.argv[1:]
repository = os.environ.get("GH_REPO", "example/project")
host = os.environ.get("GH_HOST", "github.com")
if repository.startswith("github.com/"):
    host, repository = repository.split("/", 1)
with open(os.environ["TEST_GH_LOG"], "a") as output:
    output.write(json.dumps({"args": args, "repository": repository, "host": host}) + "\\n")
if args[:2] == ["repo", "view"]:
    print("other/fetch-only" if "fetch-only" in args[2] else "example/project")
elif args[:2] == ["pr", "view"]:
    if len(args) > 2 and args[2].startswith("https://"):
        print(json.dumps({"url": args[2], "headRefName": "feature", "baseRefName": "main",
            "headRefOid": os.environ["TEST_HEAD"], "state": "OPEN",
            "headRepository": {"nameWithOwner": "example/project"}}))
    else:
        sys.exit(1)
elif args[:2] == ["pr", "create"]:
    print("https://" + host + "/" + repository + "/pull/1")
else:
    sys.exit(2)
''')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_REPO", "other/unapproved")
    monkeypatch.setenv("GH_HOST", "unapproved.example")
    monkeypatch.setenv("TEST_GH_LOG", str(log))
    monkeypatch.setenv("TEST_HEAD", git(root, "rev-parse", "HEAD"))
    result = publish_compact_pr(issue, root, output)
    assert result["delivered"] and result["pr"]["url"] == "https://github.com/example/project/pull/1"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call["args"][2] == push_url for call in calls if call["args"][:2] == ["repo", "view"])
    assert [(call["host"], call["repository"]) for call in calls if call["args"][:2] == ["pr", "create"]] == [("github.com", "example/project")]
    assert git(root, "ls-remote", push_url, "refs/heads/feature").split()[0] == result["commit"]


def test_endpoint_change_during_repository_lookup_blocks_external_dispatch(compact_request, compact_proposal, monkeypatch):
    import cafe.core.capabilities as capabilities
    from cafe.manager.delivery import publish_compact_pr
    root = Path(compact_request["project_root"])
    git(root, "remote", "set-url", "--push", "origin", git(root, "remote", "get-url", "origin"))
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    original = subprocess.run
    def repository_transport(argv, **kwargs):
        if argv[:3] == ["gh", "repo", "view"]:
            git(root, "remote", "set-url", "origin", "https://github.com/other/unapproved.git")
            return subprocess.CompletedProcess(argv, 0, "example/project\n", "")
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", repository_transport)
    calls = []
    def publish_transport(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("unexpected external mutation")
    monkeypatch.setitem(capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr", publish_transport)
    with pytest.raises(ValueError):
        publish_compact_pr(issue, root, output)
    assert not calls
    assert json.loads((issue / "delivery_result.json").read_text())["status"] == "unknown"


def native_delivery_ready(root, issue, context):
    """Use real checkpoints and the provider parser; replace only native transport."""
    from cafe.agents.cli.claude import ClaudeCLI
    from cafe.core.types import AgentConfig, AgentCLI
    from cafe.core.execution_checkpoints import checkpoint, require_verified_review
    from cafe.core.packet_io import canonical_json
    make_ready(root, issue)
    receipt = checkpoint(context, "before_review", round_id="review-round", parent_id="parent")
    assert receipt["passed"]
    blob = git(root, "hash-object", "-w", "--path=app.py", "app.py")
    assert git(root, "cat-file", "-p", blob)
    conclusion = {"findings": [], "targeted_tests": ["effective Git implementation inspected"]}
    adapter = ClaudeCLI(AgentConfig(name="parent", cli=AgentCLI.CLAUDE, model="test",
                                  native_review_configuration=context["review_configuration"]))
    stream = [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
        "name": "Agent", "id": "child", "input": {"subagent_type": "cafe_reviewer",
            "prompt": "CAFE_REVIEW_CHECKPOINT:" + receipt["receipt_id"]}}]}}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
            "tool_use_id": "child", "content": json.dumps(conclusion)}]}})]
    evidence = {"version": 1, "round_id": receipt["round_id"], "checkpoint": receipt,
        "invocations": [{"reviewer_id": "child", "parent_id": "parent",
            "configuration": context["review_configuration"], "terminal": "result", "exit_status": 0,
            "result_reference": "child", **conclusion}],
        "native_observations": {"version": 1, "parent_id": "parent",
            "observations": adapter.native_review_observations(stream)}}
    (issue / "execution_review.json").write_bytes(canonical_json(evidence))
    require_verified_review(context, evidence)
    return evidence


@pytest.mark.parametrize("change_after_review", [False, True])
def test_native_review_binds_effective_git_filter_contents(compact_request, tmp_path, monkeypatch, change_after_review):
    from tests.integration.test_compact_workflow import native_context
    from cafe.core.execution_checkpoints import require_verified_review
    compact_request["compact_inputs"]["delivery_contract"] = {"schema_version": 4,
        "route": "direct", "remote": "origin", "branch": "feature", "effects": ["commit", "push"]}
    root, issue, _, context = native_context(compact_request, tmp_path, monkeypatch)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    (root / "app.py").write_text('value = "reviewed"\n')
    def configure_filter():
        (root / ".git/info/attributes").write_text("app.py filter=reviewtest\n")
        git(root, "config", "filter.reviewtest.clean", "sed 's/reviewed/unreviewed/g'")
    if not change_after_review:
        configure_filter()
    evidence = native_delivery_ready(root, issue, context)
    if change_after_review:
        configure_filter()
    try:
        require_verified_review(context, evidence)
        current_review = True
    except ValueError:
        current_review = False
    assert closeout(root, issue, "--initialize").returncode == 0
    commit = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "0")
    push = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    if change_after_review:
        assert not current_review
        assert commit.returncode != 0 and push.returncode != 0
        assert not git(root, "ls-remote", "origin", "refs/heads/feature")
        assert not (issue / "delivery_result.json").exists()
    else:
        assert current_review and commit.returncode == 0 and push.returncode == 0
        assert git(root, "show", "HEAD:app.py") == 'value = "unreviewed"'
        assert json.loads((issue / "delivery_result.json").read_text())["delivered"]
    assert (root / "app.py").read_text() == 'value = "reviewed"\n'
