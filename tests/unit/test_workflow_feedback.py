"""Invariant tests for the durable workflow-feedback ledger."""

from __future__ import annotations

import json

import pytest

from cafe.core.workflow_feedback import WorkflowFeedbackError, WorkflowFeedbackLedger


def _supervisor_feedback(issue_dir, *, selected_target=False, selected_continuation="pr"):
    """Persist one completed task through the owning record store."""
    from cafe.core.human_task_records import HumanTaskRecordStore

    playbook = {
        "steps": {
            "pr": {
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "local-review",
                        "outcomes": {} if selected_target else {"fix_now": "pr"},
                        "allowed_targets": [selected_continuation] if selected_target else [],
                        "feedback_delivery": {"source_kind": "local_review"},
                    }
                ]
            }
        }
    }
    records = HumanTaskRecordStore(issue_dir)
    task = records.materialize(
        workflow_id="workflow",
        step="pr",
        iteration=1,
        trigger="confirm_output",
        policy_id="local-review",
        prompt="Review",
        assignee_type="user",
        expected_result={
            "input_schema": "decision",
            "decisions": [{
                "id": "fix_now", "correction": True,
                "requires_target": selected_target,
            }],
            "allowed_targets": [selected_continuation] if selected_target else [],
        },
        continuations={} if selected_target else {"fix_now": "pr"},
    )
    result = records.complete(
        workflow_id="workflow",
        task_id=task.id,
        source="command",
        payload={
            "task": "local-review",
            "decision": "fix_now",
            "feedback": "Fix it.",
            "declared_continuation": selected_continuation if selected_target else "pr",
            "continuation": "develop",
            "supervisor_handoff_to": "develop",
            **({"target": selected_continuation} if selected_target else {}),
        },
    )
    _, entry = WorkflowFeedbackLedger(issue_dir).record(
        source_identity="local_review:pr:local-review:1",
        source_kind="local_review",
        target_step="develop",
        content="Fix it.",
    )
    return playbook, task, result, entry


def test_supervisor_feedback_ownership_binds_exact_completed_task_and_content(tmp_path):
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, task, result, entry = _supervisor_feedback(tmp_path)
    receipts = supervisor_feedback_receipts(
        tmp_path,
        playbook=playbook,
        workflow_id="workflow",
        target_step="develop",
        entries=(entry,),
    )
    assert receipts[entry.source_identity]["task_id"] == task.id
    assert receipts[entry.source_identity]["result_id"] == result.id
    assert (
        supervisor_feedback_receipts(
            tmp_path,
            playbook=playbook,
            workflow_id="workflow",
            target_step="develop",
            entries=(),
        )
        == {}
    )


@pytest.mark.parametrize("tamper", [None, "target", "declared", "allowed", "unexpected"])
@pytest.mark.parametrize("continuation", ["pr", "_done"])
def test_supervisor_selected_correction_target_is_bound_to_its_declaration(
    tmp_path, tamper, continuation
):
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, _, _, entry = _supervisor_feedback(
        tmp_path, selected_target=True, selected_continuation=continuation
    )
    if tamper:
        path = HumanTaskRecordStore(tmp_path).file_path
        raw = json.loads(path.read_text())
        if tamper == "target":
            raw["results"][0]["payload"]["target"] = "qa"
        elif tamper == "declared":
            raw["results"][0]["payload"]["declared_continuation"] = "qa"
        elif tamper == "allowed":
            raw["tasks"][0]["expected_result"]["allowed_targets"] = ["qa"]
        else:
            raw["tasks"][0]["expected_result"]["decisions"][0]["requires_target"] = False
        path.write_text(json.dumps(raw))
    if tamper:
        with pytest.raises(WorkflowFeedbackError):
            supervisor_feedback_receipts(
                tmp_path, playbook=playbook, workflow_id="workflow",
                target_step="develop", entries=(entry,),
            )
    else:
        receipts = supervisor_feedback_receipts(
            tmp_path, playbook=playbook, workflow_id="workflow",
            target_step="develop", entries=(entry,),
        )
        assert receipts[entry.source_identity]["declared_continuation"] == continuation


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", "Changed request"),
        ("source_kind", "forged"),
        ("target_step", "qa"),
    ],
)
def test_supervisor_feedback_rejects_stale_or_forged_entry(tmp_path, field, value):
    from dataclasses import replace
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, _, _, entry = _supervisor_feedback(tmp_path)
    with pytest.raises(WorkflowFeedbackError):
        supervisor_feedback_receipts(
            tmp_path,
            playbook=playbook,
            workflow_id="workflow",
            target_step="develop",
            entries=(replace(entry, **{field: value}),),
        )


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("tasks", "status", "cancelled"),
        ("tasks", "iteration", 2),
        ("tasks", "continuations", {"fix_now": "qa"}),
        ("results", "workflow_id", "another-workflow"),
        ("payload", "task", "forged-task"),
        ("payload", "declared_continuation", "qa"),
        ("payload", "continuation", "qa"),
        ("payload", "supervisor_handoff_to", "qa"),
        ("payload", "feedback", "Changed durable request"),
    ],
)
def test_supervisor_receipt_never_accepts_stale_task_or_result(tmp_path, section, field, value):
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, _, _, entry = _supervisor_feedback(tmp_path)
    path = HumanTaskRecordStore(tmp_path).file_path
    raw = json.loads(path.read_text())
    if section == "payload":
        raw["results"][0]["payload"][field] = value
    else:
        raw[section][0][field] = value
    path.write_text(json.dumps(raw))
    with pytest.raises((WorkflowFeedbackError, ValueError)):
        supervisor_feedback_receipts(
            tmp_path,
            playbook=playbook,
            workflow_id="workflow",
            target_step="develop",
            entries=(entry,),
        )


def test_supervisor_receipt_rejects_stale_task_when_receiver_is_also_allowed(tmp_path):
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, _, _, entry = _supervisor_feedback(tmp_path, selected_target=True)
    binding = playbook["steps"]["pr"]["human_tasks"][0]
    binding["allowed_targets"].append("develop")
    binding["feedback_delivery"].update(todo_source="local_review", todo_id_prefix="LR")
    playbook["steps"]["develop"] = {}
    path = HumanTaskRecordStore(tmp_path).file_path
    raw = json.loads(path.read_text())
    raw["tasks"][0]["expected_result"]["allowed_targets"].append("develop")
    raw["tasks"][0]["iteration"] = 2
    path.write_text(json.dumps(raw))

    with pytest.raises(WorkflowFeedbackError, match="ownership proof is missing"):
        supervisor_feedback_receipts(
            tmp_path, playbook=playbook, workflow_id="workflow",
            target_step="develop", entries=(entry,),
        )


def test_later_normal_curator_task_is_not_owned_by_an_older_supervisor(tmp_path):
    from cafe.core.human_task_records import HumanTaskRecordStore
    from cafe.core.workflow_feedback import supervisor_feedback_receipts

    playbook, original, _, _ = _supervisor_feedback(tmp_path, selected_target=True)
    binding = playbook["steps"]["pr"]["human_tasks"][0]
    binding["allowed_targets"].append("develop")
    binding["feedback_delivery"].update(todo_source="local_review", todo_id_prefix="LR")
    records = HumanTaskRecordStore(tmp_path)
    policy = {**original.expected_result, "allowed_targets": ["pr", "develop"]}
    task = records.materialize(
        workflow_id="workflow", step="pr", iteration=2, trigger="confirm_output",
        policy_id="local-review", prompt="Review next revision", assignee_type="user",
        expected_result=policy, continuations={},
    )
    records.complete(
        workflow_id="workflow", task_id=task.id, source="command",
        payload={"task": "local-review", "decision": "fix_now", "target": "develop",
                 "feedback": "Next correction", "declared_continuation": "develop",
                 "continuation": "develop"},
    )
    _, entry = WorkflowFeedbackLedger(tmp_path).record(
        source_identity="local_review:pr:local-review:2", source_kind="local_review",
        target_step="develop", content="Next correction",
    )

    assert supervisor_feedback_receipts(
        tmp_path, playbook=playbook, workflow_id="workflow",
        target_step="develop", entries=(entry,),
    ) == {}


def _persisted_entry(**lifecycle: bool) -> dict[str, object]:
    return {
        "source_identity": "github-pr:348:comment-1",
        "source_kind": "github_pr",
        "target_step": "develop",
        "content": "Correct the missing validation.",
        "actionable": True,
        "consumed": False,
        "resolved": False,
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:00+00:00",
        **lifecycle,
    }


def _store_entries(ledger: WorkflowFeedbackLedger, *entries: dict[str, object]) -> None:
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(
        json.dumps({"version": 1, "entries": list(entries)}),
        encoding="utf-8",
    )


def test_new_unresolved_feedback_is_durable_and_actionable(tmp_path) -> None:
    """UT-001 — a new unresolved source item is written exactly once."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")

    created, entry = ledger.record(
        source_identity="github-pr:348:comment-1",
        source_kind="github_pr",
        target_step="develop",
        content="Correct the missing validation.",
    )

    assert created is True
    assert entry.actionable is True
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert payload["entries"][0]["source_identity"] == "github-pr:348:comment-1"
    assert ledger.pending(target_step="develop") == [entry]


def test_pending_snapshot_is_bounded_and_unchanged_by_late_feedback(tmp_path) -> None:
    """A curator's source context remains stable after the pre-prompt snapshot."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-snapshot")
    _created, first = ledger.record(
        source_identity="github-pr:508:comment-1",
        source_kind="github_pr",
        target_step="pr",
        content="First actionable comment.",
    )
    _created, second = ledger.record(
        source_identity="github-pr:508:comment-2",
        source_kind="github_pr",
        target_step="pr",
        content="Second actionable comment.",
    )
    snapshot = tmp_path / "issue-snapshot" / "pr" / "iteration_001" / "batch.json"

    assert ledger.write_pending_snapshot(path=snapshot, target_step="pr", limit=1) == (
        first.source_identity,
    )
    ledger.record(
        source_identity="github-pr:508:comment-late",
        source_kind="github_pr",
        target_step="pr",
        content="Arrived after the snapshot.",
    )

    persisted = json.loads(snapshot.read_text(encoding="utf-8"))
    assert [entry["source_identity"] for entry in persisted["entries"]] == [first.source_identity]
    assert [entry.source_identity for entry in ledger.pending(target_step="pr")] == [
        first.source_identity,
        second.source_identity,
        "github-pr:508:comment-late",
    ]


def test_ledger_deduplicates_cross_form_consumed_and_resolved_items(tmp_path) -> None:
    """UT-002 — the ledger alone determines whether feedback can wake work."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")
    identity = "github-pr:348:comment-2"

    assert ledger.record(
        source_identity=identity,
        source_kind="github_review_comment",
        target_step="develop",
        content="Add a boundary case.",
    )[0]
    assert not ledger.record(
        source_identity=identity,
        source_kind="github_timeline_comment",
        target_step="develop",
        content="Add a boundary case.",
    )[0]
    assert ledger.consume(identity) is True
    assert ledger.pending(target_step="develop") == []

    assert not ledger.record(
        source_identity=identity,
        source_kind="github_review_comment",
        target_step="develop",
        content="Add a boundary case.",
    )[0]
    assert ledger.reconcile_resolved({identity}) == 1
    assert ledger.pending(target_step="develop") == []


def test_ledger_persists_distinct_delivered_and_excluded_dispositions(tmp_path) -> None:
    """UT-002 — terminal curation decisions remain distinguishable after reload."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")
    _created, delivered = ledger.record(
        source_identity="github-pr:348:comment-delivered",
        source_kind="github_pr",
        target_step="develop",
        content="Correct a concrete defect.",
    )
    _created, excluded = ledger.record(
        source_identity="github-pr:348:comment-excluded",
        source_kind="github_pr",
        target_step="develop",
        content="Looks good.",
    )

    assert ledger.settle_reviewed(
        [delivered.source_identity], [excluded.source_identity]
    ) == ([delivered], [excluded])

    reloaded = {entry.source_identity: entry for entry in ledger.load()}
    assert reloaded[delivered.source_identity].disposition == "delivered"
    assert reloaded[excluded.source_identity].disposition == "excluded"
    assert ledger.pending(target_step="develop") == []


@pytest.mark.parametrize(
    "lifecycle",
    [
        {"actionable": "false"},
        {"actionable": False, "consumed": "false"},
        {"actionable": False, "resolved": "false"},
    ],
)
def test_ledger_rejects_non_boolean_persisted_lifecycle_values(tmp_path, lifecycle) -> None:
    """UT-001 — persisted lifecycle values fail closed unless they are booleans."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")
    _store_entries(ledger, _persisted_entry(**lifecycle))

    with pytest.raises(WorkflowFeedbackError):
        ledger.load()


@pytest.mark.parametrize(
    "lifecycle",
    [
        {"actionable": True, "consumed": True, "resolved": False},
        {"actionable": True, "consumed": False, "resolved": True},
        {"actionable": False, "consumed": False, "resolved": False},
    ],
)
def test_ledger_rejects_impossible_persisted_lifecycle_states(tmp_path, lifecycle) -> None:
    """UT-002 — an entry must be pending, consumed, resolved, or both terminal states."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")
    _store_entries(ledger, _persisted_entry(**lifecycle))

    with pytest.raises(WorkflowFeedbackError):
        ledger.load()


def test_ledger_loads_all_supported_persisted_lifecycle_states(tmp_path) -> None:
    """UT-001/UT-002 — valid pending and terminal lifecycle states remain durable."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")
    _store_entries(
        ledger,
        _persisted_entry(source_identity="pending"),
        _persisted_entry(source_identity="consumed", actionable=False, consumed=True),
        _persisted_entry(source_identity="resolved", actionable=False, resolved=True),
        _persisted_entry(
            source_identity="consumed-resolved", actionable=False, consumed=True, resolved=True
        ),
    )

    assert [entry.source_identity for entry in ledger.load()] == [
        "pending",
        "consumed",
        "resolved",
        "consumed-resolved",
    ]


def test_persistence_failure_does_not_claim_feedback_was_recorded(tmp_path, monkeypatch) -> None:
    """UT-003 — a failed atomic write leaves no successful ledger state behind."""
    ledger = WorkflowFeedbackLedger(tmp_path / "issue-348")

    def fail_replace(*_args, **_kwargs) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("cafe.core.workflow_feedback.os.replace", fail_replace)

    with pytest.raises(WorkflowFeedbackError):
        ledger.record(
            source_identity="github-pr:348:comment-3",
            source_kind="github_pr",
            target_step="develop",
            content="This must not be acknowledged.",
        )

    assert not ledger.path.exists()
