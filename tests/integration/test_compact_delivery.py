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


@pytest.mark.parametrize("consumer", ["owner", "approval", "generic"])
@pytest.mark.parametrize("drift", ["none", "push", "fetch", "head", "rewrite", "replacement"])
def test_actual_publication_pins_target_through_last_repository_lookup(
    compact_request, tmp_path, monkeypatch, consumer, drift
):
    import os
    from types import SimpleNamespace
    from tests.integration.test_compact_workflow import native_context
    from cafe.manager.delivery import publish_compact_pr
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs
    from cafe.core.capability_approvals import CapabilityApprovalService
    from cafe.core.hooks.native import GitHubPRCreator
    from cafe.core.status_codes import PhaseStatusCode
    root = Path(compact_request["project_root"])
    other = tmp_path / "unapproved.git"
    git(tmp_path, "init", "--bare", "-q", str(other))
    authorized = git(root, "remote", "get-url", "--push", "origin")
    if drift == "fetch":
        git(root, "remote", "set-url", "--push", "origin", authorized)
    elif drift == "rewrite":
        git(root, "remote", "set-url", "origin", "cafe-original:")
        git(root, "config", "url." + authorized + ".insteadOf", "cafe-original:")
        git(root, "config", "url." + str(other) + ".insteadOf", authorized)
    root, issue, _, context = native_context(compact_request, tmp_path, monkeypatch)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    (root / "app.py").write_text('value = "reviewed"\n')
    git(root, "add", "app.py")
    git(root, "commit", "-qm", "reviewed implementation")
    authorized = git(root, "remote", "get-url", "--push", "origin")
    native_delivery_ready(root, issue, context)
    head = git(root, "rev-parse", "HEAD")
    if drift == "replacement":
        # The local replacement view passes current-content review; publication
        # must validate the original object that real Git sends to the remote.
        env = {**os.environ, "GIT_INDEX_FILE": str(root / ".git/publication-candidate-index")}
        def candidate_git(*args, input=None):
            return subprocess.run(["git", "-C", str(root), *args], env=env, input=input,
                capture_output=True, text=True, check=True, timeout=20).stdout.strip()
        candidate_git("read-tree", head)
        blob = candidate_git("hash-object", "-w", "--stdin", input='value = "unreviewed"\n')
        candidate_git("update-index", "--cacheinfo", "100644," + blob + ",app.py")
        selected = candidate_git("commit-tree", candidate_git("write-tree"), "-p", head,
                                 "-m", "unreviewed publication candidate")
        git(root, "replace", selected, head)
        git(root, "update-ref", "HEAD", selected)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nEvidence\n")
    binary = tmp_path / "github-bin"
    binary.mkdir()
    log = tmp_path / "github-calls.jsonl"
    gh = binary / "gh"
    gh.write_text("#!" + sys.executable + '\n' + '''import json, os, sys, subprocess
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["TEST_GH_LOG"])
previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
count = 1 + sum(call["args"][:2] == ["repo", "view"] for call in previous)
with log.open("a") as output:
    output.write(json.dumps({"args": args}) + "\\n")
if args[:2] == ["repo", "view"]:
    if count == int(os.environ["TEST_LAST_LOOKUP"]):
        root = os.environ["TEST_ROOT"]
        if os.environ["TEST_DRIFT"] == "push":
            subprocess.run(["git", "-C", root, "remote", "set-url", "--push", "origin", os.environ["TEST_OTHER"]], check=True)
        elif os.environ["TEST_DRIFT"] == "fetch":
            subprocess.run(["git", "-C", root, "remote", "set-url", "origin", "https://github.com/other/fetch.git"], check=True)
        elif os.environ["TEST_DRIFT"] == "head":
            Path(root, "app.py").write_text('value = "unreviewed"\\n')
            subprocess.run(["git", "-C", root, "add", "app.py"], check=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "late unreviewed contents"], check=True)
    print("example/project")
elif args[:2] == ["pr", "view"]:
    if args[2].startswith("https://"):
        print(json.dumps({"url": args[2], "headRefName": "feature", "baseRefName": "main",
            "headRefOid": os.environ["TEST_HEAD"], "state": "OPEN",
            "headRepository": {"nameWithOwner": "example/project"}}))
    else:
        sys.exit(1)
elif args[:2] == ["pr", "create"]:
    print("https://github.com/example/project/pull/1")
else:
    sys.exit(2)
''')
    gh.chmod(0o755)
    for key, value in {"TEST_GH_LOG": str(log), "TEST_LAST_LOOKUP": "1" if consumer == "generic" else "2",
                       "TEST_ROOT": str(root), "TEST_OTHER": str(other), "TEST_HEAD": head,
                       "TEST_DRIFT": drift}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    if consumer == "generic":
        request_file = output.parent / "request.json"
        request_file.write_text(json.dumps({"capability": "cafe.pr.publish", "args": {
            "output": output.relative_to(root).as_posix(), "base": "main", "remote": "origin"}}))
        phase = SimpleNamespace(issue_dir=issue, phase_dir=output.parent.parent, iteration=1,
            git_ops=SimpleNamespace(get_repo_root=lambda: root))
        def invoke_generic():
            return GitHubPRCreator().run(stage="publish_output", phase=phase, step_name="custom-publish",
            step_def={"capability_requests": ["cafe.pr.publish"], "behavior": {"publish_confirmation": True}},
            output_file=output, capability_request_file=request_file, status_code=PhaseStatusCode.CONFIRMED,
            validated_pr_auto_create=True, execution_context=context)
        if drift in {"none", "rewrite"}:
            invoke_generic()
        else:
            with pytest.raises(ValueError if drift == "replacement" else RuntimeError):
                invoke_generic()
            before = log.read_text() if log.exists() else ""
            with pytest.raises(ValueError):
                invoke_generic()
            assert (log.read_text() if log.exists() else "") == before
    else:
        registry = dict(load_capability_registry(default_capability_definition_dirs(root)))
        approval = {}
        if consumer == "approval":
            registry["cafe.pr.publish"] = registry["cafe.pr.publish"].model_copy(update={"approval": "required"})
            pending = publish_compact_pr(issue, root, output, registry=registry)
            assert pending["needs_human_task"] and not log.exists()
            service = CapabilityApprovalService(issue_dir=issue, workflow_id="workflow", step="delivery", iteration=1)
            task = service.inspect(pending["task_id"])
            service.record_decision(pending["task_id"], {"decision": "approve", "workflow_id": "workflow",
                "task_id": pending["task_id"], "request_fingerprint": task["fingerprint"],
                "correlation_id": task["correlation_id"]})
            approval = {"approval_task_id": pending["task_id"], "correlation_id": task["correlation_id"]}
        try:
            result = publish_compact_pr(issue, root, output, registry=registry, **approval)
        except ValueError:
            result = json.loads((issue / "delivery_result.json").read_text())
        if drift in {"none", "rewrite"}:
            assert result["delivered"]
        else:
            assert not result["delivered"] and result["status"] == "unknown"
            before = log.read_text() if log.exists() else ""
            with pytest.raises(ValueError):
                publish_compact_pr(issue, root, output, registry=registry, **approval)
            assert (log.read_text() if log.exists() else "") == before
    assert not git(other, "for-each-ref", "--format=%(objectname)", "refs/heads/feature")
    observed = git(Path(authorized), "for-each-ref", "--format=%(objectname)", "refs/heads/feature")
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    if drift == "replacement":
        assert not log.exists()
    if drift in {"none", "rewrite"}:
        assert observed.split()[0] == head
        assert any(call["args"][:2] == ["pr", "create"] for call in calls)
    else:
        assert not observed
        assert not any(call["args"][:2] == ["pr", "create"] for call in calls)


@pytest.mark.parametrize("rewrite", [False, True])
@pytest.mark.parametrize("boundary", ["filter", "lookup", "transport", "pre_push"])
@pytest.mark.parametrize("drift", ["none", "head", "push", "both"])
def test_native_direct_push_keeps_reviewed_source_and_authorized_target(
    compact_request, tmp_path, monkeypatch, rewrite, boundary, drift
):
    import os
    import shlex
    import shutil
    from tests.integration.test_compact_workflow import native_context
    root = Path(compact_request["project_root"])
    authorized = Path(git(root, "remote", "get-url", "--push", "origin"))
    other = tmp_path / "unapproved.git"
    git(tmp_path, "init", "--bare", "-q", str(other))
    if rewrite:
        git(root, "remote", "set-url", "origin", "cafe-original:")
        git(root, "config", "url." + str(authorized) + ".insteadOf", "cafe-original:")
        git(root, "config", "url." + str(other) + ".insteadOf", str(authorized))
    compact_request["compact_inputs"]["delivery_contract"] = {"schema_version": 4,
        "route": "direct", "remote": "origin", "branch": "feature", "effects": ["commit", "push"]}
    root, issue, _, context = native_context(compact_request, tmp_path, monkeypatch)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    program = tmp_path / "late_git_input.py"
    control = root / ".git/late-delivery-input.json"
    fired = root / ".git/late-delivery-input-fired"
    checkpoint_path = issue / "delivery_action_checkpoint.json"
    # Only the external filter/hook program performs the mutation. All owning
    # guards, native parsing, public closeout and Git operations remain real.
    program.write_text('''import json, subprocess, sys
from pathlib import Path
root, control, fired, checkpoint = map(Path, sys.argv[2:6])
data = b"" if sys.argv[1] in {"aba", "lookup", "replacement", "transport"} else sys.stdin.buffer.read()
if control.exists():
    plan = json.loads(control.read_text())
    if plan["boundary"] == "aba" and sys.argv[1] == "aba":
        args = sys.argv[6:]
        stage = plan.get("stage", "select")
        if stage == "select" and "get-url" in args and checkpoint.stat().st_mtime_ns != plan["checkpoint_mtime"]:
            plan["stage"] = "restore"
            control.write_text(json.dumps(plan))
            subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", plan["unreviewed"]], check=True)
            (root / "app.py").write_text('value = "unreviewed"\\n')
            fired.write_text(plan["drift"])
        elif stage == "restore" and plan["baseline"] + "^{commit}" in args:
            plan["stage"] = "rearm"
            control.write_text(json.dumps(plan))
            subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", plan["reviewed"]], check=True)
            (root / "app.py").write_text('value = "reviewed"\\n')
        elif stage == "rearm" and "get-url" in args:
            control.unlink()
            subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", plan["unreviewed"]], check=True)
            (root / "app.py").write_text('value = "unreviewed"\\n')
    ready = plan["boundary"] != "aba" and sys.argv[1] == plan["boundary"] and (
        sys.argv[1] not in {"filter", "lookup", "replacement"} or checkpoint.stat().st_mtime_ns != plan["checkpoint_mtime"])
    if ready:
        control.unlink()
        if plan["drift"] in {"head", "both"}:
            subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", plan["unreviewed"]], check=True)
            if sys.argv[1] == "lookup":
                (root / "app.py").write_text('value = "unreviewed"\\n')
        if plan["drift"] in {"push", "both"}:
            subprocess.run(["git", "-C", str(root), "remote", "set-url", "--push", "origin", plan["other"]], check=True)
        fired.write_text(plan["drift"])
sys.stdout.buffer.write(data)
''')
    def command(mode):
        return shlex.join([sys.executable, str(program), mode, str(root),
                           str(control), str(fired), str(checkpoint_path)])
    (root / ".git/info/attributes").write_text("app.py filter=reviewtest\n")
    git(root, "config", "filter.reviewtest.clean", command("filter"))
    hook = root / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexec " + command("pre_push") + "\n")
    hook.chmod(0o755)
    (root / "app.py").write_text('value = "reviewed"\n')
    native_delivery_ready(root, issue, context)
    assert closeout(root, issue, "--initialize").returncode == 0
    commit = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "0")
    assert commit.returncode == 0, commit.stderr
    reviewed = git(root, "rev-parse", "HEAD")
    # Prepare an unreferenced commit without changing worktree, index or HEAD.
    env = {**os.environ, "GIT_INDEX_FILE": str(root / ".git/late-test-index")}
    def object_git(*args, input=None):
        return subprocess.run(["git", "-C", str(root), *args], env=env, input=input,
            text=True, capture_output=True, check=True, timeout=20).stdout.strip()
    object_git("read-tree", reviewed)
    blob = object_git("hash-object", "-w", "--stdin", input='value = "unreviewed"\n')
    object_git("update-index", "--cacheinfo", "100644," + blob + ",app.py")
    unreviewed = object_git("commit-tree", object_git("write-tree"), "-p", reviewed,
                            "-m", "unreviewed replacement")
    if boundary == "replacement":
        git(root, "replace", unreviewed, reviewed)
    control.write_text(json.dumps({"boundary": boundary, "drift": drift,
        "checkpoint_mtime": checkpoint_path.stat().st_mtime_ns,
        "unreviewed": unreviewed, "other": str(other), "reviewed": reviewed,
        "baseline": context["baseline_commit"]}))
    if boundary in {"aba", "lookup", "replacement", "transport"}:
        # The external CLI boundary changes inputs immediately before the real
        # Git process consumes argv. No Git command or result is simulated.
        real_git = shutil.which("git")
        binary = tmp_path / "git-bin"
        binary.mkdir()
        wrapper = binary / "git"
        wrapper.write_text("#!" + sys.executable + "\n" +
            "import os, subprocess, sys\n" +
            ("if True:\n" if boundary == "aba" else
             "if " + repr("get-url" if boundary in {"lookup", "replacement"} else "push") + " in sys.argv[1:]:\n") +
            "    subprocess.run(" + repr([sys.executable, str(program), boundary,
                str(root), str(control), str(fired), str(checkpoint_path)]) + " + sys.argv[1:], check=True)\n" +
            "os.execv(" + repr(real_git) + ", [" + repr(real_git) + ", *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    push = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    assert fired.read_text() == drift
    assert not git(other, "for-each-ref", "--format=%(objectname)", "refs/heads/feature")
    observed = git(authorized, "for-each-ref", "--format=%(objectname)", "refs/heads/feature")
    if drift == "none" or boundary not in {"aba", "filter", "lookup", "replacement"}:
        assert observed == reviewed
        assert git(authorized, "show", observed + ":app.py") == 'value = "reviewed"'
    else:
        assert not observed
    expected_bytes = ('value = "unreviewed"\n' if boundary == "lookup" and
                      drift in {"head", "both"} else 'value = "reviewed"\n')
    assert (root / "app.py").read_text() == expected_bytes
    evidence_path = issue / "delivery_result.json"
    if drift == "none":
        assert push.returncode == 0, push.stderr
        evidence = json.loads(evidence_path.read_text())
        assert evidence["delivered"] and evidence["commit"] == reviewed
    else:
        assert push.returncode != 0
        assert not evidence_path.exists()
        journal = json.loads((root / ".git/cafe/closeout/sample/workflow.json").read_text())
        assert [entry["status"] for entry in journal["commands"]["deliver"]] == ["succeeded", "unknown"]
    replay = closeout(root, issue, "--execute", "--stage", "deliver", "--index", "1")
    assert replay.returncode != 0
    assert git(authorized, "for-each-ref", "--format=%(objectname)", "refs/heads/feature") == observed
    assert not git(other, "for-each-ref", "--format=%(objectname)", "refs/heads/feature")


@pytest.mark.parametrize("rewrite", [False, True])
def test_native_direct_source_receipt_cannot_follow_a_restored_worktree(
    compact_request, tmp_path, monkeypatch, rewrite
):
    # Select the bad commit, restore the reviewed HEAD/bytes during validation,
    # then reselect bad HEAD before the final target check if validation allows it.
    test_native_direct_push_keeps_reviewed_source_and_authorized_target(
        compact_request, tmp_path, monkeypatch, rewrite, "aba", "head")


@pytest.mark.parametrize("rewrite", [False, True])
def test_native_direct_source_checks_original_objects_under_replacement_refs(
    compact_request, tmp_path, monkeypatch, rewrite
):
    # A local replacement view must not substitute the tree actually pushed.
    test_native_direct_push_keeps_reviewed_source_and_authorized_target(
        compact_request, tmp_path, monkeypatch, rewrite, "replacement", "head")
