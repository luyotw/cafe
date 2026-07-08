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
# BlackboardStore.load_handoff_contract 測試（reject-and-BatonRejected 行為）
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


def _write_baton(issue_dir: Path, payload: dict) -> None:
    """將 payload 寫入 next_step.txt。"""
    (issue_dir / "next_step.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestLoadHandoffContractBatonRejected:
    def test_invalid_to_owner_raises_baton_rejected(self, tmp_path: Path) -> None:
        """load_handoff_contract 遇到無效 to_owner 應拋出 BatonRejected。"""
        issue_dir = tmp_path / "issue-reject-1"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="human", to_step="user", intent="need_clarification"))

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        exc = exc_info.value
        assert exc.field == "to_owner"
        assert exc.invalid_value == "human"
        assert "agent" in exc.valid_values
        assert "user" in exc.valid_values
        assert "done" in exc.valid_values

    def test_invalid_intent_raises_baton_rejected(self, tmp_path: Path) -> None:
        """load_handoff_contract 遇到無效 intent 應拋出 BatonRejected。"""
        issue_dir = tmp_path / "issue-reject-2"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="agent", to_step="review", intent="confirmed"))

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        exc = exc_info.value
        assert exc.field == "intent"
        assert exc.invalid_value == "confirmed"
        assert "await_agent" in exc.valid_values

    def test_valid_baton_returns_contract_no_auto_corrected_event(self, tmp_path: Path) -> None:
        """合法 baton 正常回傳 HandoffContract，不產生 baton_auto_corrected 事件。"""
        issue_dir = tmp_path / "issue-reject-3"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="agent", to_step="review", intent="await_agent"))

        contract = store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        assert contract.to_owner == HandoffOwner.AGENT
        assert not any(e.event_type == "baton_auto_corrected" for e in state.events)

    def test_allow_legacy_text_json_invalid_raises_baton_rejected(self, tmp_path: Path) -> None:
        """JSON 解析成功但 enum 無效時，即使 allow_legacy_text=True 也拋 BatonRejected。"""
        issue_dir = tmp_path / "issue-reject-4"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        _write_baton(issue_dir, _base_payload(to_owner="human", to_step="user", intent="need_clarification"))

        with pytest.raises(BatonRejected):
            store.load_handoff_contract(state, allowed_steps=["develop", "review"], allow_legacy_text=True)

    @pytest.mark.parametrize(
        "missing_field",
        ["version", "from_step", "to_owner", "to_step", "intent"],
    )
    def test_missing_required_field_raises_baton_rejected(
        self,
        tmp_path: Path,
        missing_field: str,
    ) -> None:
        """缺少必要欄位時需直接拋 BatonRejected，不得走 legacy。"""
        issue_dir = tmp_path / "issue-reject-missing"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        payload = _base_payload()
        payload.pop(missing_field)
        _write_baton(issue_dir, payload)

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(state, allowed_steps=["develop", "review"])

        exc = exc_info.value
        assert exc.field == missing_field
        assert not exc.valid_values

    def test_allow_legacy_text_json_scalar_raises_baton_rejected(self, tmp_path: Path) -> None:
        """能 parse 的 JSON 非 object 時不可退回 legacy text。"""
        issue_dir = tmp_path / "issue-reject-scalar"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        (issue_dir / "next_step.txt").write_text('"review"', encoding="utf-8")

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(
                state,
                allowed_steps=["develop", "review"],
                allow_legacy_text=True,
            )

        exc = exc_info.value
        assert exc.field == "payload"
        assert exc.invalid_value == "str"
        assert exc.valid_values == ["JSON object"]

    def test_allow_legacy_text_non_json_falls_back_to_legacy_step(self, tmp_path: Path) -> None:
        """JSON 解析失敗（非 enum 問題）且 allow_legacy_text=True 時走 legacy step 解析。"""
        issue_dir = tmp_path / "issue-reject-5"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("develop")
        (issue_dir / "next_step.txt").write_text("review", encoding="utf-8")

        contract = store.load_handoff_contract(state, allowed_steps=["develop", "review"], allow_legacy_text=True)

        assert contract.to_step == "review"
        assert contract.source == "legacy_text"


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


def test_alignment_checkpoint_baton_must_be_user_owned_user_target(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-align-baton"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")

    _write_baton(
        issue_dir,
        _base_payload(
            from_step="develop",
            to_owner="user",
            to_step="user",
            intent="alignment_checkpoint",
            status_code="alignment_checkpoint",
        ),
    )
    valid = store.load_handoff_contract(state, allowed_steps=["develop", "review"])
    assert valid.intent == HandoffIntent.ALIGNMENT_CHECKPOINT

    _write_baton(
        issue_dir,
        _base_payload(
            from_step="develop",
            to_owner="agent",
            to_step="review",
            intent="alignment_checkpoint",
            status_code="alignment_checkpoint",
        ),
    )
    with pytest.raises(ValueError, match="alignment_checkpoint"):
        store.load_handoff_contract(state, allowed_steps=["develop", "review"])


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


class TestLegacyKeyValueBaton:
    """Sibling of issue #357: agent-written multi-line key=value batons."""

    def _store_and_state(self, tmp_path: Path):
        issue_dir = tmp_path / "issue-legacy-kv"
        issue_dir.mkdir(parents=True)
        store = BlackboardStore(issue_dir)
        state = store.load_or_create("schema-comment")
        return issue_dir, store, state

    def test_multiline_key_value_with_free_text_parses_routing_fields(self, tmp_path: Path) -> None:
        issue_dir, store, state = self._store_and_state(tmp_path)
        (issue_dir / "next_step.txt").write_text(
            "to_step=user\nto_owner=user\nintent=need_clarification\n"
            "message=Schema comment updated. Please confirm schema and import 6 pending datasets, then reply to continue.\n",
            encoding="utf-8",
        )

        contract = store.load_handoff_contract(
            state,
            allowed_steps=["build", "review", "schema-comment"],
            allow_legacy_text=True,
        )

        assert contract.to_step == "user"
        assert contract.to_owner == HandoffOwner.USER
        assert contract.intent == HandoffIntent.NEED_CLARIFICATION
        assert contract.from_step == "schema-comment"

    def test_key_value_without_to_step_raises_baton_rejected(self, tmp_path: Path) -> None:
        issue_dir, store, state = self._store_and_state(tmp_path)
        (issue_dir / "next_step.txt").write_text(
            "to_owner=agent\nsummary=long text without routing target\n",
            encoding="utf-8",
        )

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(
                state,
                allowed_steps=["build", "review"],
                allow_legacy_text=True,
            )
        assert exc_info.value.field == "to_step"

    def test_single_step_name_still_parses_as_legacy(self, tmp_path: Path) -> None:
        issue_dir, store, state = self._store_and_state(tmp_path)
        (issue_dir / "next_step.txt").write_text("review\n", encoding="utf-8")

        contract = store.load_handoff_contract(
            state,
            allowed_steps=["build", "review"],
            allow_legacy_text=True,
        )
        assert contract.to_step == "review"
        assert contract.source == "legacy_text"

    def test_unknown_single_step_name_is_rejected_by_allowed_steps(self, tmp_path: Path) -> None:
        issue_dir, store, state = self._store_and_state(tmp_path)
        (issue_dir / "next_step.txt").write_text("no_such_step\n", encoding="utf-8")

        with pytest.raises(BatonRejected) as exc_info:
            store.load_handoff_contract(
                state,
                allowed_steps=["build", "review"],
                allow_legacy_text=True,
            )

        assert exc_info.value.field == "to_step"
        assert exc_info.value.invalid_value == "no_such_step"

    def test_invalid_intent_value_falls_back_to_step_derived_intent(self, tmp_path: Path) -> None:
        issue_dir, store, state = self._store_and_state(tmp_path)
        (issue_dir / "next_step.txt").write_text(
            "to_step=review\nto_owner=agent\nintent=not_a_real_intent\n",
            encoding="utf-8",
        )

        contract = store.load_handoff_contract(
            state,
            allowed_steps=["build", "review"],
            allow_legacy_text=True,
        )
        assert contract.to_step == "review"
        assert contract.intent == HandoffIntent.AWAIT_AGENT
