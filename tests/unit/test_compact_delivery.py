"""U8/I6/I7: exact authority, final eligibility and durable delivery outcomes."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compact_contract import compact_request, compact_proposal, activate


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_prepared_direct_route_binds_remote_and_exact_literal_commands(compact_request):
    from cafe.manager.delivery import prepare_compact_delivery
    root = Path(compact_request["project_root"])
    endpoint = {"schema_version": 4, "route": "direct", "remote": "origin",
                "branch": "feature", "effects": ["commit", "push"]}
    prepared = prepare_compact_delivery(root, endpoint, issue_name="sample")
    assert len(prepared["remote_identity"]) == 64
    assert prepared["closeout_plan"]["deliver"][1]["argv"] == [
        "git", "push", "origin", "HEAD:refs/heads/feature"]
    assert prepared["closeout_plan"]["cleanup"] == []


def test_delivery_guard_rejects_missing_review_and_changed_remote(compact_request, compact_proposal):
    from cafe.manager.delivery import validate_compact_action
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    with pytest.raises((ValueError, OSError)):
        validate_compact_action(issue, root)
    git(root, "remote", "set-url", "origin", "/different/remote")
    with pytest.raises((ValueError, OSError)):
        validate_compact_action(issue, root)


def make_ready(root, issue):
    from cafe.manager.file_scope import execution_scope_projection
    from cafe.core.execution_checkpoints import checkpoint
    from cafe.core.packet_io import atomic_write_bytes, canonical_json
    from cafe.core.blackboard import BlackboardStore, BlackboardState, HandoffOwner, HandoffIntent
    store = BlackboardStore(issue)
    board = BlackboardState(current_step="build", playbook_id="selected", workflow_id="workflow")
    store.save(board)
    board.current_step = "done"
    board.handoff_contract = store.build_handoff_contract(from_step="build",
        to_owner=HandoffOwner.DONE, to_step="done", intent=HandoffIntent.WORKFLOW_COMPLETE,
        status_code="READY", source="test")
    store.save(board)
    store.write_handoff_contract(board, board.handoff_contract)
    context = execution_scope_projection(issue, root)
    receipt = checkpoint(context, "before_review", round_id="round", parent_id="parent")
    invocation = {"parent_id": "parent", "reviewer_id": "native-child",
        "configuration": context["review_configuration"], "terminal": "result",
        "exit_status": 0, "findings": [], "targeted_tests": ["targeted passed"],
        "result_reference": "native/tool-result"}
    evidence = {"version": 1, "round_id": "round", "checkpoint": receipt,
        "invocations": [invocation], "native_observations": {"version": 1,
            "parent_id": "parent", "observations": [{**invocation,
                "receipt_id": receipt["receipt_id"], "observed_at": receipt["observed_at"]}]}}
    atomic_write_bytes(issue / "execution_review.json", canonical_json(evidence))
    atomic_write_bytes(issue / "execution_delivery.json", canonical_json({"version": 1,
        "authority_digest": context["authority_digest"], "endpoint": context["delivery_endpoint"],
        "checkpoint": checkpoint(context, "before_delivery", round_id="round", parent_id="parent")}))


def test_current_delivery_guard_rechecks_content_and_exact_target(compact_request, compact_proposal):
    from cafe.manager.delivery import validate_compact_action
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    (root / "app.py").write_text("approved change")
    make_ready(root, issue)
    context = validate_compact_action(issue, root)
    assert context["delivery_endpoint"]["target_branch"] == "main"
    (root / "app.py").write_text("later edit")
    with pytest.raises((ValueError, OSError)):
        validate_compact_action(issue, root)


def test_readiness_alone_never_reports_published_pr(compact_request, compact_proposal, monkeypatch):
    from cafe.manager.delivery import publish_compact_pr
    from cafe.core.capabilities import load_capability_registry, default_capability_definition_dirs
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    make_ready(root, issue)
    output = issue / "deliver/iteration_001/pr.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Change\n\nDelivery summary\n")
    registry = load_capability_registry(default_capability_definition_dirs(root))
    manifest = registry["cafe.pr.publish"]
    registry = {**registry, "cafe.pr.publish": manifest.model_copy(update={"policy": "deny"})}
    result = publish_compact_pr(issue, root, output, registry=registry)
    assert not result["delivered"]
    assert result["receipt"]["success"] is False
