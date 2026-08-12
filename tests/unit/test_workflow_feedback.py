"""Invariant tests for the durable workflow-feedback ledger."""

from __future__ import annotations

import json

import pytest

from cafe.core.workflow_feedback import WorkflowFeedbackError, WorkflowFeedbackLedger


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
