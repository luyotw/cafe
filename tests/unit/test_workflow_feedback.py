"""Invariant tests for the durable workflow-feedback ledger."""

from __future__ import annotations

import json

import pytest

from cafe.core.workflow_feedback import WorkflowFeedbackError, WorkflowFeedbackLedger


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
