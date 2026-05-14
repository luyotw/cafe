"""Tests for blackboard-backed workflow state."""

import json
from pathlib import Path

import pytest

from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardState,
    BlackboardStore,
    HandoffIntent,
    HandoffOwner,
    _normalize_baton_payload,
)
from cafe.core.workflow_models import BatonRejected


# ---------------------------------------------------------------------------
# BatonRejected 例外單元測試
# ---------------------------------------------------------------------------

class TestBatonRejected:
    def test_attributes_set_correctly(self) -> None:
        exc = BatonRejected(field="to_owner", invalid_value="human", valid_values=["agent", "user", "done"])
        assert exc.field == "to_owner"
        assert exc.invalid_value == "human"
        assert exc.valid_values == ["agent", "user", "done"]

    def test_valid_values_is_independent_copy(self) -> None:
        source = ["agent", "user", "done"]
        exc = BatonRejected(field="to_owner", invalid_value="human", valid_values=source)
        source.append("extra")
        assert "extra" not in exc.valid_values

    def test_str_contains_field_and_invalid_value(self) -> None:
        exc = BatonRejected(field="intent", invalid_value="bad_intent", valid_values=["await_agent"])
        msg = str(exc)
        assert "intent" in msg
        assert "bad_intent" in msg

    def test_is_exception_subclass(self) -> None:
        exc = BatonRejected(field="to_owner", invalid_value="x", valid_values=[])
        assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# _normalize_baton_payload 單元測試
# ---------------------------------------------------------------------------

def _base_payload(**overrides) -> dict:
    """最小合法 payload，方便各測試覆寫特定欄位。"""
    base = {
        "version": 1,
        "from_step": "develop",
        "to_owner": "agent",
        "to_step": "review",
        "intent": "await_agent",
        "status_code": "",
        "created_at": "2026-05-14T10:00:00+08:00",
        "source": "test",
    }
    base.update(overrides)
    return base


class TestNormalizeBatonPayload:
    # --- Task 1: to_owner 通用映射 ---

    def test_to_owner_human_normalized_to_user(self) -> None:
        """to_owner='human' 應正規化為 'user'。"""
        payload = _base_payload(to_owner="human", to_step="user")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["to_owner"] == "user"
        assert len(corrections) == 1
        assert corrections[0]["field"] == "to_owner"
        assert corrections[0]["original"] == "human"
        assert corrections[0]["corrected"] == "user"

    def test_to_owner_reviewer_normalized_to_user(self) -> None:
        """to_owner='reviewer' 應正規化為 'user'。"""
        payload = _base_payload(to_owner="reviewer", to_step="user")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["to_owner"] == "user"
        assert any(c["field"] == "to_owner" and c["corrected"] == "user" for c in corrections)

    def test_to_owner_developer_normalized_to_user(self) -> None:
        """to_owner='developer' 應正規化為 'user'。"""
        payload = _base_payload(to_owner="developer", to_step="user")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["to_owner"] == "user"
        assert any(c["field"] == "to_owner" and c["corrected"] == "user" for c in corrections)

    # --- Task 2: to_step==done 修正 ---

    def test_to_owner_forced_to_done_when_to_step_is_done(self) -> None:
        """to_step='done' 時，to_owner 應強制改為 'done'。"""
        payload = _base_payload(to_owner="agent", to_step="done", intent="workflow_complete")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["to_owner"] == "done"
        assert any(c["field"] == "to_owner" and c["corrected"] == "done" for c in corrections)

    def test_intent_complete_corrected_when_to_step_done(self) -> None:
        """to_step='done' 且 intent='complete' 時，應修正為 'workflow_complete'。"""
        payload = _base_payload(to_owner="done", to_step="done", intent="complete")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["intent"] == "workflow_complete"
        assert any(c["field"] == "intent" and c["corrected"] == "workflow_complete" for c in corrections)

    def test_intent_confirmed_corrected_when_to_step_done(self) -> None:
        """to_step='done' 且 intent='confirmed' 時，應修正為 'workflow_complete'。"""
        payload = _base_payload(to_owner="done", to_step="done", intent="confirmed")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["intent"] == "workflow_complete"

    def test_intent_done_corrected_when_to_step_done(self) -> None:
        """to_step='done' 且 intent='done' 時，應修正為 'workflow_complete'。"""
        payload = _base_payload(to_owner="done", to_step="done", intent="done")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["intent"] == "workflow_complete"

    def test_intent_other_not_corrected_when_to_step_done(self) -> None:
        """to_step='done' 但 intent 為其他合法值時，不應被修改。"""
        payload = _base_payload(to_owner="done", to_step="done", intent="manual_handoff")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["intent"] == "manual_handoff"
        assert not any(c["field"] == "intent" for c in corrections)

    # --- Task 3: playbook step intent 修正 ---

    def test_intent_confirmed_corrected_to_await_agent_for_playbook_step(self) -> None:
        """to_step 為 playbook step 且 intent='confirmed' 時，應修正為 'await_agent'。"""
        payload = _base_payload(to_owner="agent", to_step="review", intent="confirmed")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized["intent"] == "await_agent"
        assert any(c["field"] == "intent" and c["corrected"] == "await_agent" for c in corrections)

    def test_intent_confirmed_not_corrected_when_to_step_user(self) -> None:
        """to_step='user' 時，intent='confirmed' 不應套用 playbook-step 規則。"""
        payload = _base_payload(to_owner="user", to_step="user", intent="confirmed")
        normalized, corrections = _normalize_baton_payload(payload)
        # to_step='user' 不是 playbook step，不應被 playbook-step 規則修改
        assert normalized["intent"] == "confirmed"
        assert not any(c["field"] == "intent" and c["corrected"] == "await_agent" for c in corrections)

    # --- Task 4: 有效 baton 不變動 ---

    def test_valid_baton_passes_through_unchanged(self) -> None:
        """合法 baton payload 不應被修改，correction 清單應為空。"""
        payload = _base_payload(to_owner="agent", to_step="review", intent="await_agent")
        normalized, corrections = _normalize_baton_payload(payload)
        assert normalized == payload
        assert corrections == []

    def test_corrections_include_original_and_corrected_values(self) -> None:
        """每筆 correction 必須同時包含 original 與 corrected 欄位。"""
        payload = _base_payload(to_owner="human", to_step="user")
        _, corrections = _normalize_baton_payload(payload)
        for c in corrections:
            assert "field" in c
            assert "original" in c
            assert "corrected" in c


# ---------------------------------------------------------------------------
# BlackboardStore.load_handoff_contract 整合測試（使用 tmp_path）
# ---------------------------------------------------------------------------

def _write_baton(issue_dir: Path, payload: dict) -> None:
    """將 payload 寫入 next_step.txt。"""
    (issue_dir / "next_step.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestLoadHandoffContractNormalization:
    def test_auto_corrects_to_owner_human_and_logs_event(self, tmp_path: Path) -> None:
        """load_handoff_contract 應自動修正 to_owner=human 並記錄 baton_auto_corrected 事件。"""
        issue_dir = tmp_path / "issue-norm-1"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="human", to_step="user", intent="need_clarification"))

        contract = store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        assert contract.to_owner == HandoffOwner.USER
        auto_events = [e for e in state.events if e.event_type == "baton_auto_corrected"]
        assert len(auto_events) == 1
        corrections = auto_events[0].data.get("corrections", [])
        assert any(c["field"] == "to_owner" and c["original"] == "human" and c["corrected"] == "user" for c in corrections)

    def test_valid_baton_no_event_logged(self, tmp_path: Path) -> None:
        """合法 baton 通過 load_handoff_contract 後不應有 baton_auto_corrected 事件。"""
        issue_dir = tmp_path / "issue-norm-2"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="agent", to_step="review", intent="await_agent"))

        contract = store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        assert contract.to_owner == HandoffOwner.AGENT
        assert not any(e.event_type == "baton_auto_corrected" for e in state.events)

    def test_corrected_but_still_invalid_raises(self, tmp_path: Path) -> None:
        """修正後仍不合法的 baton（to_step 不在 allowed_steps）應拋出 ValueError。"""
        issue_dir = tmp_path / "issue-norm-3"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        # to_owner='human' 會被修正為 'user'，但 to_step='nonexistent' 不在 allowed_steps
        _write_baton(issue_dir, _base_payload(to_owner="human", to_step="nonexistent", intent="await_agent"))

        with pytest.raises(ValueError):
            store.load_handoff_contract(state, allowed_steps=["develop", "review"])


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
