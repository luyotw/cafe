"""Tests for workflow instance and blackboard."""

from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.core.workflow_instance import WorkflowInstance


def test_workflow_instance_load_or_create_and_transition(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-1"
    instance = WorkflowInstance.load_or_create(issue_dir, "default", "spec")
    assert instance.current_step == "spec"
    assert instance.file_path.exists()

    instance.transition_to("plan", "CAFE_CONFIRMED")
    loaded = WorkflowInstance.load(issue_dir)
    assert loaded is not None
    assert loaded.current_step == "plan"
    assert loaded.metadata["last_status_code"] == "CAFE_CONFIRMED"


def test_blackboard_store_records_artifacts_events_and_decisions(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-2"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.set_artifact(state, "spec", "spec/output.md")
    store.record_event(state, "step_completed", {"step": "spec"})
    store.record_decision(state, {"from": "spec", "to": "plan"})
    store.set_current_step(state, "plan")

    reloaded = store.load_or_create("spec")
    assert reloaded.current_step == "plan"
    assert reloaded.artifacts["spec"] == "spec/output.md"
    assert reloaded.events[0]["type"] == "step_completed"
    assert reloaded.decisions[0]["to"] == "plan"
