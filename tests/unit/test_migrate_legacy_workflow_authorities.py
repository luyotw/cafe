import importlib.util
import json
from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore


SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_legacy_workflow_authorities.py"
SPEC = importlib.util.spec_from_file_location("legacy_authority_migration", SCRIPT)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def _write_legacy_issue(issue_dir: Path, workflow_id: str) -> None:
    issue_dir.mkdir(parents=True)
    callback = {
        "workflow_id": workflow_id,
        "event_id": "callback-id",
        "sequence": 7,
        "occurred_at": "2026-09-29T00:00:01+00:00",
        "event_type": "phase_terminal",
        "step": "review",
    }
    (issue_dir / "blackboard.json").write_text(json.dumps({
        "schema_version": 4,
        "current_step": "user",
        "playbook_id": "demo",
        "workflow_id": workflow_id,
        "events": [
            {"timestamp": "2026-09-29T00:00:00+00:00", "step": "build",
             "event_type": "step_completed", "message": "done", "data": {"step": "build"}},
            {"timestamp": callback["occurred_at"], "step": "review",
             "event_type": "workflow_event_callback_enqueued",
             "message": json.dumps(callback), "data": callback},
        ],
        "capability_receipts": [{"workflow_id": workflow_id, "capability": "demo"}],
    }), encoding="utf-8")
    (issue_dir / "human_tasks.json").write_text(json.dumps({"tasks": [{
        "id": "task", "workflow_id": workflow_id, "status": "pending",
    }]}), encoding="utf-8")


def test_migration_moves_verified_history_to_canonical_authorities(tmp_path: Path):
    issue_dir = tmp_path / "issue"
    workflow_id = "workflow-123"
    _write_legacy_issue(issue_dir, workflow_id)

    assert MIGRATION.migrate(issue_dir, workflow_id, apply=False) == (
        "validated legacy history: 2 events, 1 receipts"
    )
    assert not (issue_dir / "audit_events").exists()
    assert "migrated" in MIGRATION.migrate(issue_dir, workflow_id, apply=True)

    state = BlackboardStore(issue_dir).load_or_create("build")
    assert [event.event_type for event in state.events] == [
        "step_completed", "workflow_event_callback_enqueued"
    ]
    assert state.capability_receipts == [{"workflow_id": workflow_id, "capability": "demo"}]
    audit = json.loads((issue_dir / "audit_events" / "events" / "0000000002.json").read_text())
    assert audit["event_id"] == "callback-id"
    assert audit["delivery"] == "closed"
    board = json.loads((issue_dir / "blackboard.json").read_text())
    assert "events" not in board and "capability_receipts" not in board
    assert board["applied_event_sequence"] == 2
    assert MIGRATION.migrate(issue_dir, workflow_id, apply=True) == (
        "legacy workflow authorities already migrated"
    )


def test_migration_rejects_mismatched_human_task_identity(tmp_path: Path):
    issue_dir = tmp_path / "issue"
    _write_legacy_issue(issue_dir, "workflow-123")
    tasks = json.loads((issue_dir / "human_tasks.json").read_text())
    tasks["tasks"][0]["workflow_id"] = "other-workflow"
    (issue_dir / "human_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")

    with pytest.raises(ValueError, match="human task workflow identity"):
        MIGRATION.migrate(issue_dir, "workflow-123", apply=True)
    assert not (issue_dir / "audit_events").exists()


def test_migration_recovers_a_verified_partial_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    issue_dir = tmp_path / "issue"
    workflow_id = "workflow-123"
    _write_legacy_issue(issue_dir, workflow_id)
    original_replace = MIGRATION.os.replace
    published_audit = False

    def interrupt_after_audit(source: Path, destination: Path) -> None:
        nonlocal published_audit
        original_replace(source, destination)
        if destination == issue_dir / "audit_events" and not published_audit:
            published_audit = True
            raise OSError("simulated interruption after audit publish")

    monkeypatch.setattr(MIGRATION.os, "replace", interrupt_after_audit)
    with pytest.raises(OSError, match="simulated interruption"):
        MIGRATION.migrate(issue_dir, workflow_id, apply=True)
    assert (issue_dir / "audit_events").is_dir()
    assert not (issue_dir / "capability_receipts.json").exists()

    monkeypatch.setattr(MIGRATION.os, "replace", original_replace)
    assert "migrated" in MIGRATION.migrate(issue_dir, workflow_id, apply=True)
    assert (issue_dir / "capability_receipts.json").is_file()
    board = json.loads((issue_dir / "blackboard.json").read_text(encoding="utf-8"))
    assert "events" not in board and "capability_receipts" not in board
