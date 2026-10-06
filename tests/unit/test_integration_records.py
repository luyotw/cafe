"""Durable selection/report/proof correlation and crash invariants (U3/U6/U7/U8)."""

import pytest

from cafe.core.integration_records import IntegrationRecordStore


@pytest.fixture
def records(tmp_path):
    return IntegrationRecordStore(tmp_path, "workflow-a")


def proposal():
    return {
        "target": "local_branch",
        "repository": "/tmp/reviewed",
        "source_commit": "a" * 40,
        "feature_branch": "feature",
        "target_branch": "main",
    }


def confirmed(records):
    rev = records.propose(
        proposal(), {"task_id": "review", "result_id": "accept", "source_identity": "hash"}
    )
    records.associate_task(rev, "choose", "confirmation")
    records.confirm(rev, "choose", "confirmation-result")
    records.associate_task(rev, "action", "action")
    return rev


def test_report_is_not_proof_and_duplicate_is_idempotent(records):
    rev = confirmed(records)
    records.report(rev, "action", "result-1", "performed")
    records.report(rev, "action", "result-1", "performed")
    assert not records.qualifies()
    assert len(records.read()["reports"]) == 1
    records.record_attempt(
        rev, {"success": True, "reason": "ancestry", "observed": {"target_head": "a" * 40}}
    )
    assert records.qualifies()


def test_selection_change_invalidates_proof_and_stale_reports(records):
    old = confirmed(records)
    records.record_attempt(old, {"success": True, "reason": "merged", "observed": {}})
    new = records.propose(
        proposal() | {"target_branch": "other"},
        {"task_id": "review", "result_id": "accept", "source_identity": "hash"},
    )
    assert new != old and not records.qualifies()
    with pytest.raises(ValueError):
        records.report(old, "action", "late", "performed")
    with pytest.raises(ValueError):
        records.record_attempt(old, {"success": True, "reason": "stale", "observed": {}})


def test_conflict_survives_restart_without_qualifying(records, tmp_path):
    rev = confirmed(records)
    records.report(rev, "action", "conflict", "blocked")
    fresh = IntegrationRecordStore(tmp_path, "workflow-a")
    assert fresh.read()["reports"][0]["outcome"] == "blocked"
    assert not fresh.qualifies()


def test_missing_human_report_is_explicit_and_proof_failure_is_atomic(records, monkeypatch):
    rev = confirmed(records)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("cafe.core.integration_records.atomic_write_bytes", fail)
    with pytest.raises(OSError):
        records.record_attempt(
            rev, {"success": True, "reason": "already integrated", "observed": {}}
        )
    assert not records.qualifies()
    assert records.read()["reports"] == []


def test_reconcile_task_association_reuses_exact_id(records):
    rev = confirmed(records)
    records.associate_task(rev, "action", "action")
    assert records.read()["selections"][0]["tasks"]["action"] == "action"
    with pytest.raises(ValueError):
        records.associate_task(rev, "foreign", "action")


def test_corrupt_foreign_or_unconfirmed_proof_fails_closed(records, tmp_path):
    rev = records.propose(proposal(), {"result_id": "accept"})
    with pytest.raises(ValueError):
        records.record_attempt(rev, {"success": True, "reason": "forged", "observed": {}})
    with pytest.raises(ValueError):
        IntegrationRecordStore(tmp_path, "other").read()
    records.file_path.write_text('{"schema_version":99}')
    with pytest.raises(ValueError):
        records.read()
