"""Delivery uses ordinary automatic ownership and one fresh observation (U6/I9/I11)."""

from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.git import GitOperations
from tests.integration.integration_fixture import create_journey


def test_declared_verifier_observes_once_and_records_owner_lifecycle(tmp_path, monkeypatch):
    journey = create_journey(tmp_path / "repo", monkeypatch)
    assert journey.complete("ship").rejection is None
    journey.select()
    journey.runtime().run()
    assert journey.complete("confirm").rejection is None
    journey.runtime().run()
    journey.human_integrate()
    observations = []
    original = GitOperations.observe_integration

    def observe(operations, *args):
        observations.append(args)
        return original(operations, *args)

    monkeypatch.setattr("cafe.core.git.GitOperations.observe_integration", observe)
    result = journey.runtime().run()
    assert result.completed
    assert len(observations) == 1
    events = journey.state().events
    assert any(e.event_type == "automatic_step_completed" for e in events)
    assert not any(
        t.status.value == "pending" for t in HumanTaskRecordStore(journey.issue_dir).tasks()
    )


def test_direct_completion_without_fresh_proof_does_not_inspect(tmp_path, monkeypatch):
    journey = create_journey(tmp_path / "repo", monkeypatch)
    assert journey.complete("ship").rejection is None
    journey.select()
    journey.runtime().run()
    assert journey.complete("confirm").rejection is None
    journey.runtime().run()
    journey.human_integrate()
    monkeypatch.setattr(
        "cafe.core.git.GitOperations.observe_integration",
        lambda *args: (_ for _ in ()).throw(AssertionError("terminal gate must not inspect")),
    )
    result = journey.runtime()._emit_complete(
        current_step="land",
        status_code="test",
        next_step="_done",
        runtime="test",
        reason="direct",
        update_contract=True,
    )
    assert not result.completed
    assert journey.state().current_step != "done"


def test_public_catalog_cannot_disable_terminal_prerequisite(tmp_path, monkeypatch):
    import yaml
    import pytest
    from cafe.playbooks.loader import PlaybookLoader

    journey = create_journey(tmp_path / "repo", monkeypatch)
    path = journey.root / ".cafe/playbooks/custom-delivery.yaml"
    data = yaml.safe_load(path.read_text())
    data.pop("terminal_prerequisite")
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        PlaybookLoader(project_root=journey.root).load("custom-delivery", strict=True)


def test_intervening_workflow_transition_discards_observation(tmp_path, monkeypatch):
    from cafe.core.blackboard import BlackboardStore

    journey = create_journey(tmp_path / "repo", monkeypatch)
    journey.complete("ship")
    journey.select()
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    journey.human_integrate()
    original = GitOperations.observe_integration

    def observe(operations, *args):
        store = BlackboardStore(journey.issue_dir)
        board = store.load_read_only()
        store.record_event(
            board, "transition", {"from": "inspect", "to": "land", "status_code": "manual_handoff"}
        )
        return original(operations, *args)

    monkeypatch.setattr(GitOperations, "observe_integration", observe)
    assert not journey.runtime().run().completed
    assert journey.state().current_step != "done"


def test_public_catalog_rejects_verifier_executable_inputs(tmp_path, monkeypatch):
    import pytest
    import yaml
    from cafe.playbooks.loader import PlaybookLoader

    journey = create_journey(tmp_path / "repo", monkeypatch)
    path = journey.root / ".cafe/playbooks/custom-delivery.yaml"
    data = yaml.safe_load(path.read_text())
    data["steps"]["inspect"]["automatic"]["inputs"] = {"command": "git merge main"}
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        PlaybookLoader(project_root=journey.root).load("custom-delivery", strict=True)


def test_agent_review_context_accepts_only_its_real_confirmation_task(tmp_path, monkeypatch):
    journey = create_journey(tmp_path / "repo", monkeypatch, agent_review=True)
    task = journey.pending()
    assert task.trigger == "confirm_output"
    assert journey.complete("ship", task).rejection is None
    journey.select()
    journey.runtime().run()
    assert journey.complete("confirm").rejection is None
    journey.runtime().run()
    journey.human_integrate()
    assert journey.runtime().run().completed


def test_resume_preserves_pending_action_from_pre_context_records(tmp_path, monkeypatch):
    import json

    journey = create_journey(tmp_path / "repo", monkeypatch)
    journey.complete("ship")
    journey.select()
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    task = journey.pending()
    path = journey.issue_dir / "human_tasks.json"
    raw = json.loads(path.read_text())
    for entry in raw["tasks"]:
        if entry["id"] == task.id:
            entry.pop("context")
    path.write_text(json.dumps(raw))
    assert not journey.runtime().run().completed
    assert journey.pending().id == task.id
    assert journey.complete("performed", journey.pending()).rejection is None
    journey.human_integrate()
    assert journey.runtime().run().completed
