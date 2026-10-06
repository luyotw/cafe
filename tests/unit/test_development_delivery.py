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
