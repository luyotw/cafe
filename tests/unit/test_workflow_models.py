"""Tests for blackboard-backed workflow state."""

import json
from pathlib import Path

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardState, BlackboardStore, HandoffOwner


def test_blackboard_load_or_create_persists_current_step_and_playbook(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-1"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec", playbook_id="default")
    assert state.current_step == "spec"
    assert state.playbook_id == "default"

    store.set_current_step(state, "plan")
    loaded = store.load_or_create("spec", playbook_id="default")
    assert loaded.current_step == "plan"
    assert loaded.playbook_id == "default"


def test_blackboard_store_records_artifacts_events_and_decisions(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-2"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    store.put_artifact(
        state,
        ArtifactEntry(
            name="spec",
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by="spec",
            path="spec/output.md",
            summary="initial spec",
        ),
    )
    store.log_event(state, "spec", "step_completed", '"spec" updated', {"step": "spec"})
    store.record_decision(state, "spec", "transition", "advance to plan", "spec")
    store.set_current_step(state, "plan")
    store.set_handoff_summary(state, "developer owns the next step")

    reloaded = store.load_or_create("spec")
    assert reloaded.current_step == "plan"
    assert reloaded.schema_version == 1
    assert reloaded.handoff_summary == "developer owns the next step"
    assert reloaded.artifacts["spec"].path == "spec/output.md"
    assert reloaded.artifacts["spec"].version == 1
    assert reloaded.events[0].event_type == "step_completed"
    assert reloaded.decisions[0].decision == "transition"


def test_blackboard_generate_digest_and_get_events_since(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-3"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    store.put_artifact(
        state,
        ArtifactEntry(
            name="code",
            kind=ArtifactKind.WORKSPACE,
            version=2,
            updated_by="develop",
            path="develop/iteration_002/output.md",
            summary="implement auth flow",
            base_sha="abc1234",
            head_sha="def5678",
        ),
    )
    store.log_event(state, "develop", "artifact_updated", '"code" updated', {"version": 2})
    first_timestamp = state.events[0].timestamp
    store.log_event(state, "review", "decision", "needs changes", {"target": "develop"})

    recent = store.get_events_since(state, first_timestamp)
    assert len(recent) == 2

    digest = store.generate_digest(state, for_step="review", since=first_timestamp)
    assert "## Blackboard" in digest
    assert "code | workspace | v2" in digest
    assert "git diff abc1234...def5678" in digest


def test_blackboard_can_rebuild_from_iteration_artifacts(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-4"
    spec_iteration = issue_dir / "spec" / "iteration_001"
    spec_iteration.mkdir(parents=True, exist_ok=True)
    (spec_iteration / "artifact.json").write_text(
        json.dumps(
            {
                "name": "spec",
                "kind": "document",
                "version": 1,
                "updated_by": "spec",
                "updated_at": "2026-04-08T10:00:00+08:00",
                "path": "spec/iteration_001/output.md",
                "summary": "initial spec",
            }
        ),
        encoding="utf-8",
    )
    code_iteration = issue_dir / "develop" / "iteration_002"
    code_iteration.mkdir(parents=True, exist_ok=True)
    (code_iteration / "artifact.json").write_text(
        json.dumps(
            {
                "name": "code",
                "kind": "workspace",
                "version": 2,
                "updated_by": "develop",
                "updated_at": "2026-04-08T11:00:00+08:00",
                "path": "develop/iteration_002/output.md",
                "summary": "implement auth flow",
                "base_sha": "abc1234",
                "head_sha": "def5678",
            }
        ),
        encoding="utf-8",
    )

    store = BlackboardStore(issue_dir)
    rebuilt = store.rebuild_from_iterations(initial_step="spec")

    assert rebuilt.current_step == "develop"
    assert rebuilt.artifacts["spec"].version == 1
    assert rebuilt.artifacts["code"].head_sha == "def5678"
    assert rebuilt.events[-1].event_type == "rebuild"


def test_blackboard_from_dict_ignores_legacy_top_level_owner() -> None:
    """Legacy blackboard.json may carry a stale top-level owner; baton is authoritative."""
    contract = {
        "version": 1,
        "from_step": "plan",
        "to_owner": "agent",
        "to_step": "develop",
        "intent": "await_agent",
        "status_code": "",
        "created_at": "2026-05-14T10:00:00+08:00",
        "source": "test",
    }
    base: dict = {
        "schema_version": 1,
        "current_step": "plan",
        "playbook_id": "default",
        "artifacts": {},
        "events": [],
        "decisions": [],
        "handoff_summary": "",
        "handoff_contract": contract,
        "updated_at": "2026-05-14T10:00:00+08:00",
    }
    with_legacy = dict(base, owner="user")
    without_legacy = dict(base)

    a = BlackboardState.from_dict(with_legacy, initial_step="spec")
    b = BlackboardState.from_dict(without_legacy, initial_step="spec")
    assert a == b
    assert a.handoff_contract is not None
    assert a.handoff_contract.to_owner == HandoffOwner.AGENT
    assert a.handoff_contract.to_step == "develop"


def test_blackboard_from_dict_ignores_legacy_owner_without_contract() -> None:
    """Legacy top-level owner is ignored even when handoff_contract is absent."""
    base: dict = {
        "schema_version": 1,
        "current_step": "plan",
        "playbook_id": "default",
        "artifacts": {},
        "events": [],
        "decisions": [],
        "handoff_summary": "",
        "updated_at": "2026-05-14T10:00:00+08:00",
    }
    with_legacy = dict(base, owner="user")
    without_legacy = dict(base)

    a = BlackboardState.from_dict(with_legacy, initial_step="spec")
    b = BlackboardState.from_dict(without_legacy, initial_step="spec")
    assert a == b
    assert a.handoff_contract is None


def test_blackboard_saved_json_has_no_top_level_owner(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-owner-omit"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec", playbook_id="default")
    store.set_current_step(state, "plan")

    raw = json.loads(store.file_path.read_text(encoding="utf-8"))
    assert "owner" not in raw


def test_blackboard_rebuild_save_has_no_top_level_owner(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-rebuild-owner"
    spec_iteration = issue_dir / "spec" / "iteration_001"
    spec_iteration.mkdir(parents=True, exist_ok=True)
    (spec_iteration / "artifact.json").write_text(
        json.dumps(
            {
                "name": "spec",
                "kind": "document",
                "version": 1,
                "updated_by": "spec",
                "updated_at": "2026-05-14T09:00:00+08:00",
                "path": "spec/iteration_001/output.md",
                "summary": "s",
            }
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    store.rebuild_from_iterations(initial_step="spec")
    raw = json.loads(store.file_path.read_text(encoding="utf-8"))
    assert "owner" not in raw
