"""U1–U5: exact authority, frozen selections and non-replayable operations."""

from copy import deepcopy

import pytest

from cafe.delivery.contracts import ActionProposal, approve_selection
from cafe.delivery.records import ActionStore


def proposal(**updates):
    value = {
        "version": 1,
        "workflow_id": "workflow",
        "approval_step": "package",
        "approval_iteration": 1,
        "repository": "owner/repo",
        "source_oid": "a" * 40,
        "source_branch": "feature",
        "target_branch": "develop",
        "target_oid": "b" * 40,
        "mode": "github",
        "strategy": "merge",
        "pr_number": 23,
        "destination": "",
        "issue_repository": "owner/repo",
        "proposals": [
            {"id": "FUP-001", "title": "First", "body": "Original draft", "evidence": "file:1"},
            {"id": "FUP-002", "title": "Second", "body": "Another draft", "evidence": "file:2"},
        ],
    }
    value.update(updates)
    return ActionProposal.model_validate(value)


def authority(p, decision="integrate_selected", feedback="FUP-002"):
    return {
        "workflow_id": p.workflow_id,
        "step": p.approval_step,
        "iteration": p.approval_iteration,
        "task_id": "task",
        "result_id": "result",
        "proposal_digest": p.digest,
        "decision": decision,
        "feedback": feedback,
    }


def test_selected_drafts_are_frozen_and_empty_selection_does_not_expand():
    p = proposal()
    selected = approve_selection(p, authority(p))
    assert [item.id for item in selected.selected] == ["FUP-002"]
    assert selected.selected[0].body == "Another draft"
    assert approve_selection(p, authority(p, "integrate_only", "")).selected == ()
    later = p.model_dump(mode="json")
    later["proposals"][1]["body"] = "Changed draft"
    assert selected.proposal_digest != ActionProposal.model_validate(later).digest


@pytest.mark.parametrize(
    "feedback", ["FUP-099", "FUP-001 FUP-001", "", "FUP-001 and everything else"]
)
def test_invalid_selection_never_expands(feedback):
    p = proposal()
    with pytest.raises(ValueError):
        approve_selection(p, authority(p, feedback=feedback))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step", "wrong"),
        ("iteration", 2),
        ("proposal_digest", "0" * 64),
        ("decision", "continue_without_issue"),
        ("task_id", ""),
        ("result_id", ""),
    ],
)
def test_review_or_stale_identity_is_not_action_authority(field, value):
    p = proposal()
    a = authority(p)
    a[field] = value
    with pytest.raises(ValueError):
        approve_selection(p, a)


@pytest.mark.parametrize(
    "updates",
    [
        {"source_oid": "HEAD"},
        {"target_branch": "--all"},
        {"repository": "bad"},
        {"mode": "local", "strategy": "merge", "destination": "relative"},
        {"mode": "github", "strategy": "ff-only"},
    ],
)
def test_incomplete_or_unsafe_operation_identity_is_rejected(updates):
    with pytest.raises(ValueError):
        proposal(**updates)


def test_attempts_survive_restart_and_unknown_is_not_replayable(tmp_path):
    snapshot = approve_selection(proposal(), authority(proposal()))
    store = ActionStore(tmp_path, snapshot)
    with store.locked():
        assert store.read("integration") is None
        store.start("integration")
    resumed = ActionStore(tmp_path, snapshot)
    assert resumed.read("integration")["state"] == "unknown"
    with resumed.locked(), pytest.raises(ValueError):
        resumed.start("integration")
    with resumed.locked():
        resumed.finish("integration", {"state": "succeeded", "commit": "c" * 40})
    assert ActionStore(tmp_path, snapshot).read("integration")["commit"] == "c" * 40


def test_marker_is_stable_and_scoped_to_selected_identity():
    p = proposal()
    s = approve_selection(p, authority(p))
    assert s.marker("FUP-002") == deepcopy(s).marker("FUP-002")
    assert s.marker("FUP-002") != approve_selection(
        proposal(workflow_id="other"), authority(proposal(workflow_id="other"))
    ).marker("FUP-002")
    with pytest.raises(ValueError):
        s.marker("FUP-001")


def test_registered_capability_requires_host_approval(tmp_path):
    from cafe.core.capabilities import (
        default_capability_definition_dirs,
        load_capability_registry,
        run_capability_request,
    )
    from cafe.delivery.service import action_request

    p = proposal()
    snapshot = approve_selection(p, authority(p))
    registry = load_capability_registry(default_capability_definition_dirs(tmp_path))
    request = action_request(registry, snapshot, tmp_path, "integration")
    run = run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request=request,
        output_file=tmp_path / "result.md",
    )
    assert not run.receipt["success"]
    assert run.receipt["outcome"] == "approval_required"


def test_pending_or_lost_response_is_not_success(tmp_path, monkeypatch):
    from cafe.delivery.operations import Commands, execute_action

    p = proposal()
    snapshot = approve_selection(p, authority(p, "integrate_only", ""))

    def git(self, root, *args):
        if args[0] == "status":
            return ""
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/owner/repo.git"
        return p.source_oid if args[0] == "rev-parse" else p.source_branch

    monkeypatch.setattr(Commands, "git", git)
    monkeypatch.setattr(
        Commands,
        "api",
        lambda *args, **kwargs: {
            "number": 23,
            "state": "open",
            "merged": False,
            "head": {"sha": p.source_oid, "ref": p.source_branch},
            "base": {
                "sha": p.target_oid,
                "ref": p.target_branch,
                "repo": {"full_name": p.repository},
            },
        },
    )
    calls = []
    monkeypatch.setattr(
        Commands, "run", lambda self, argv, **kwargs: (calls.append(argv) or ("", 0))
    )
    first = execute_action(tmp_path, tmp_path / "issue", snapshot, "integration")
    second = execute_action(tmp_path, tmp_path / "issue", snapshot, "integration")
    assert first["state"] == second["state"] == "unknown"
    assert len(calls) == 1


def test_issue_effect_before_receipt_is_reconciled_and_ambiguity_blocks(tmp_path, monkeypatch):
    from cafe.delivery.operations import Commands, execute_action

    p = proposal()
    snapshot = approve_selection(p, authority(p))
    store = ActionStore(tmp_path, snapshot)
    with store.locked():
        store.start("FUP-002")
    row = {
        "title": snapshot.selected[0].title,
        "body": snapshot.selected[0].body + "\n\n" + snapshot.marker("FUP-002"),
        "html_url": "https://github.com/owner/repo/issues/42",
    }
    monkeypatch.setattr(Commands, "api", lambda *args, **kwargs: [row])
    result = execute_action(tmp_path, tmp_path, snapshot, "FUP-002")
    assert result["state"] == "succeeded" and result["url"].endswith("/42")
    monkeypatch.setattr(Commands, "api", lambda *args, **kwargs: [row, row])
    assert execute_action(tmp_path, tmp_path, snapshot, "FUP-002")["state"] == "unknown"


def test_child_timeout_has_bounded_cleanup_and_actual_exit_status():
    import sys
    import time
    from cafe.delivery.operations import Commands, OperationError

    started = time.monotonic()
    with pytest.raises(OperationError) as caught:
        Commands(0.2).run([sys.executable, "-c", "import os,time; os.close(1); time.sleep(60)"])
    assert caught.value.state == "unknown"
    assert caught.value.returncode is not None and caught.value.returncode != 0
    assert time.monotonic() - started < 5


def test_reapproval_keeps_issue_identity_and_unknown_attempt(tmp_path):
    p = proposal()
    first = approve_selection(p, authority(p))
    store = ActionStore(tmp_path, first)
    with store.locked():
        store.start("FUP-002")
    later = p.model_copy(update={"approval_iteration": 2})
    a = authority(later)
    a.update(task_id="new-task", result_id="new-result")
    revised = approve_selection(later, a)
    assert revised.digest != first.digest
    assert revised.marker("FUP-002") == first.marker("FUP-002")
    assert ActionStore(tmp_path, revised).correlated_attempt("FUP-002")["state"] == "unknown"


@pytest.mark.parametrize(
    "data", [{"number": 23, "head": None, "base": {}}, {"number": 23, "head": [], "base": []}]
)
def test_malformed_predispatch_observation_does_not_create_unknown_effect(
    tmp_path, monkeypatch, data
):
    from cafe.delivery.operations import Commands, execute_action

    p = proposal()
    snapshot = approve_selection(p, authority(p, "integrate_only", ""))

    def git(self, root, *args):
        if args[0] == "status":
            return ""
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/owner/repo.git"
        return p.source_oid if args[0] == "rev-parse" else p.source_branch

    monkeypatch.setattr(Commands, "git", git)
    monkeypatch.setattr(Commands, "api", lambda *args, **kwargs: data)
    monkeypatch.setattr(
        Commands, "run", lambda *args, **kwargs: pytest.fail("no dispatch is permitted")
    )
    result = execute_action(tmp_path, tmp_path / "issue", snapshot, "integration")
    assert result["state"] == "blocked"
    assert ActionStore(tmp_path / "issue", snapshot).read("integration")["state"] == "blocked"
