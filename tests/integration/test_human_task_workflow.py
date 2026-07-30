"""End-to-end contract coverage for builtin human-task workflow handoffs."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import apply_human_task_payload


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
