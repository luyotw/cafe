"""End-to-end contract coverage for builtin human-task workflow handoffs."""

from __future__ import annotations

import json
from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import apply_human_task_payload, resolve_step_human_task


def _paused_default_state(issue_dir: Path, *, from_step: str, intent: HandoffIntent):
    store = BlackboardStore(issue_dir)
    state = store.load_or_create(from_step, playbook_id="default")
    store.set_current_step(state, "user")
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=intent,
        source="test",
    )
    return store, state


def _materialize_default_task(
    issue_dir: Path,
    state: object,
    *,
    from_step: str,
    trigger: str,
):
    playbook = PlaybookLoader().load("default")
    policy, binding = resolve_step_human_task(
        playbook_data=playbook, step_name=from_step, trigger=trigger
    )
    return HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=state.workflow_id,
        step=from_step,
        iteration=1,
        trigger=trigger,
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )


def test_default_human_tasks_validate_and_route_all_user_handoff_patterns(tmp_path: Path) -> None:
    """Builtin policy responses share one validator and only declared routes advance."""
    playbook = PlaybookLoader().load("default")

    confirm_dir = tmp_path / ".cafe" / "issues" / "confirm"
    confirm_store, confirm_state = _paused_default_state(
        confirm_dir, from_step="spec", intent=HandoffIntent.CONFIRM_OUTPUT
    )
    confirmed = apply_human_task_payload(
        issue_dir=confirm_dir,
        playbook_data=playbook,
        blackboard=confirm_state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "confirm"},
        source="integration",
    )

    assert confirmed.target == "plan"
    assert confirm_store.load_or_create("spec").current_step == "plan"

    clarification_dir = tmp_path / ".cafe" / "issues" / "clarification"
    clarification_store, clarification_state = _paused_default_state(
        clarification_dir, from_step="develop", intent=HandoffIntent.NEED_CLARIFICATION
    )
    clarified = apply_human_task_payload(
        issue_dir=clarification_dir,
        playbook_data=playbook,
        blackboard=clarification_state,
        from_step="develop",
        trigger="need_clarification",
        raw_payload="Explain the required compatibility behavior.",
        source="integration",
    )

    assert clarified.target == "develop"
    assert (
        clarification_dir / "develop" / "iteration_001" / "user_input.md"
    ).read_text(encoding="utf-8") == "Explain the required compatibility behavior."
    assert clarification_store.load_or_create("develop").current_step == "develop"

    no_change_dir = tmp_path / ".cafe" / "issues" / "no-change"
    no_change_store, no_change_state = _paused_default_state(
        no_change_dir, from_step="develop", intent=HandoffIntent.NO_CHANGES_NEEDED
    )
    no_change = apply_human_task_payload(
        issue_dir=no_change_dir,
        playbook_data=playbook,
        blackboard=no_change_state,
        from_step="develop",
        trigger="no_changes_needed",
        raw_payload={"task": "no-change-decision", "decision": "agree"},
        source="integration",
    )

    assert no_change.target == "pr"
    assert no_change_store.load_or_create("develop").current_step == "pr"


def test_invalid_default_human_task_response_keeps_the_user_pause(tmp_path: Path) -> None:
    """Bad command or interactive data cannot mutate the paused handoff."""
    issue_dir = tmp_path / ".cafe" / "issues" / "invalid"
    playbook = PlaybookLoader().load("default")
    store, state = _paused_default_state(
        issue_dir, from_step="develop", intent=HandoffIntent.NO_CHANGES_NEEDED
    )

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="develop",
        trigger="no_changes_needed",
        raw_payload={"task": "no-change-decision", "decision": "unknown"},
        source="integration",
    )

    assert result.target is None
    assert result.rejection is not None
    reloaded = store.load_or_create("develop")
    assert reloaded.current_step == "user"
    assert reloaded.handoff_contract.to_owner == HandoffOwner.USER
    assert any(event.event_type == "human_task_rejected" for event in reloaded.events)


def test_matching_durable_completion_records_one_result_and_declared_continuation(
    tmp_path: Path,
) -> None:
    """IT-002/IT-003: interactive and command responses share durable completion guards."""
    playbook = PlaybookLoader().load("default")
    for source in ("interactive", "command"):
        issue_dir = tmp_path / ".cafe" / "issues" / source
        store, state = _paused_default_state(
            issue_dir, from_step="spec", intent=HandoffIntent.CONFIRM_OUTPUT
        )
        task = _materialize_default_task(
            issue_dir, state, from_step="spec", trigger="confirm_output"
        )

        result = apply_human_task_payload(
            issue_dir=issue_dir,
            playbook_data=playbook,
            blackboard=state,
            from_step="spec",
            trigger="confirm_output",
            raw_payload={
                "task": "output-review",
                "decision": "confirm",
                "human_task_id": task.id,
            },
            source=source,
        )

        records = HumanTaskRecordStore(issue_dir)
        assert result.target == "plan"
        assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED
        assert records.get_wait_state(task.id).released_at is not None
        assert len(records.results()) == 1
        assert store.load_or_create("spec").handoff_contract.to_step == "plan"


def test_durable_invalid_stale_and_cross_workflow_results_leave_the_pause_intact(
    tmp_path: Path,
) -> None:
    """IT-004: only the matching active task can create progress exactly once."""
    playbook = PlaybookLoader().load("default")
    issue_dir = tmp_path / ".cafe" / "issues" / "guarded"
    store, state = _paused_default_state(
        issue_dir, from_step="spec", intent=HandoffIntent.CONFIRM_OUTPUT
    )
    task = _materialize_default_task(
        issue_dir, state, from_step="spec", trigger="confirm_output"
    )

    invalid = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "unknown", "human_task_id": task.id},
        source="command",
    )

    unrelated = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload=json.dumps(
            {
                "task": "output-review",
                "decision": "confirm",
                "human_task_id": "another-workflow-task",
            }
        ),
        source="command",
    )
    HumanTaskRecordStore(issue_dir).cancel(
        workflow_id=state.workflow_id, task_id=task.id, reason="replaced handoff"
    )
    stale = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "confirm", "human_task_id": task.id},
        source="interactive",
    )

    records = HumanTaskRecordStore(issue_dir)
    assert invalid.target is None and invalid.rejection is not None
    assert unrelated.target is None and unrelated.rejection is not None
    assert stale.target is None and stale.rejection is not None
    assert store.load_or_create("spec").current_step == "user"
    assert records.get_task(task.id).status is HumanTaskStatus.CANCELLED
    assert records.results() == ()
    assert "rejected" in [event.event_type for event in records.lifecycle_events()]

    duplicate_dir = tmp_path / ".cafe" / "issues" / "duplicate"
    duplicate_store, duplicate_state = _paused_default_state(
        duplicate_dir, from_step="spec", intent=HandoffIntent.CONFIRM_OUTPUT
    )
    duplicate_task = _materialize_default_task(
        duplicate_dir, duplicate_state, from_step="spec", trigger="confirm_output"
    )
    completed = apply_human_task_payload(
        issue_dir=duplicate_dir,
        playbook_data=playbook,
        blackboard=duplicate_state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "confirm", "human_task_id": duplicate_task.id},
        source="command",
    )
    duplicate = apply_human_task_payload(
        issue_dir=duplicate_dir,
        playbook_data=playbook,
        blackboard=duplicate_state,
        from_step="spec",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "confirm", "human_task_id": duplicate_task.id},
        source="command",
    )

    assert completed.target == "plan"
    assert duplicate.target is None and duplicate.rejection is not None
    assert len(HumanTaskRecordStore(duplicate_dir).results()) == 1
    assert duplicate_store.load_or_create("spec").current_step == "plan"


def test_taskless_legacy_handoffs_continue_through_both_existing_transports(
    tmp_path: Path,
) -> None:
    """IT-006: old #345 pauses have no fabricated record but remain completable."""
    playbook = PlaybookLoader().load("default")
    for source in ("interactive", "command"):
        issue_dir = tmp_path / ".cafe" / "issues" / f"legacy-{source}"
        store, state = _paused_default_state(
            issue_dir, from_step="spec", intent=HandoffIntent.CONFIRM_OUTPUT
        )

        result = apply_human_task_payload(
            issue_dir=issue_dir,
            playbook_data=playbook,
            blackboard=state,
            from_step="spec",
            trigger="confirm_output",
            raw_payload={"task": "output-review", "decision": "confirm"},
            source=source,
        )

        assert result.target == "plan"
        assert not (issue_dir / "human_tasks.json").exists()
        assert store.load_or_create("spec").handoff_contract.to_step == "plan"


def test_confirmation_rejects_invalid_declared_packet_contract_for_custom_steps(
    tmp_path: Path,
) -> None:
    """A packet consumer blocks confirmation before any fallback can be recorded."""
    issue_dir = tmp_path / ".cafe" / "issues" / "packet-confirmation"
    store, state = _paused_default_state(
        issue_dir, from_step="producer", intent=HandoffIntent.CONFIRM_OUTPUT
    )
    output = issue_dir / "producer" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# incomplete source\n", encoding="utf-8")
    store.set_artifact(state, "spec", str(output))
    playbook = {
        "steps": {
            "producer": {
                "skill": "cafe-spec",
                "output_artifact": "spec",
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "output-review",
                        "outcomes": {"confirm": "consumer", "revise": "producer"},
                    }
                ],
            },
            "consumer": {"skill": "cafe-develop", "input_artifacts": ["spec"]},
        }
    }

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="producer",
        trigger="confirm_output",
        raw_payload={"task": "output-review", "decision": "confirm"},
        source="integration",
    )

    assert result.target is None
    assert result.rejection is not None
    assert "producer -> consumer" in result.rejection.message
    assert "Downstream Contract" in result.rejection.message
    reloaded = store.load_or_create("producer")
    assert reloaded.current_step == "user"
    assert not any("fallback" in event.event_type for event in reloaded.events)


def test_tdd_no_change_agreement_skips_review_to_pr(tmp_path: Path) -> None:
    """Built-in TDD retains the established no-change continuation."""
    issue_dir = tmp_path / ".cafe" / "issues" / "tdd-no-change"
    playbook = PlaybookLoader().load("tdd")
    store, state = _paused_default_state(
        issue_dir, from_step="develop", intent=HandoffIntent.NO_CHANGES_NEEDED
    )

    result = apply_human_task_payload(
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=state,
        from_step="develop",
        trigger="no_changes_needed",
        raw_payload={"task": "no-change-decision", "decision": "agree"},
        source="integration",
    )

    assert result.target == "pr"
    assert store.load_or_create("develop").current_step == "pr"


def test_editorial_human_tasks_use_editorial_contracts_without_development_copy(
    tmp_path: Path,
) -> None:
    """A non-development flow confirms and clarifies through its own policies."""
    playbook = PlaybookLoader().load("editorial")

    approval_dir = tmp_path / ".cafe" / "issues" / "editorial-approval"
    approval_store, approval_state = _paused_default_state(
        approval_dir, from_step="brief", intent=HandoffIntent.CONFIRM_OUTPUT
    )
    approval = apply_human_task_payload(
        issue_dir=approval_dir,
        playbook_data=playbook,
        blackboard=approval_state,
        from_step="brief",
        trigger="confirm_output",
        raw_payload={"task": "editorial-output-review", "decision": "approve"},
        source="integration",
    )

    assert approval.target == "draft"
    assert approval.policy is not None
    assert "editorial brief" in approval.policy.prompt.lower()
    assert approval_store.load_or_create("brief").current_step == "draft"

    clarification_dir = tmp_path / ".cafe" / "issues" / "editorial-clarification"
    clarification_store, clarification_state = _paused_default_state(
        clarification_dir, from_step="brief", intent=HandoffIntent.NEED_CLARIFICATION
    )
    clarification = apply_human_task_payload(
        issue_dir=clarification_dir,
        playbook_data=playbook,
        blackboard=clarification_state,
        from_step="brief",
        trigger="need_clarification",
        raw_payload={"task": "editorial-clarification", "answers": {"audience": "Editors"}},
        source="integration",
    )

    assert clarification.target == "brief"
    assert (
        clarification_dir / "brief" / "iteration_001" / "user_input.md"
    ).read_text(encoding="utf-8") == "audience: Editors"
    assert clarification_store.load_or_create("brief").current_step == "brief"
