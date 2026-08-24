"""Regression coverage for interactive human-task presentation."""

from __future__ import annotations

from pathlib import Path

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui import cli_shared
from cafe.ui.human_tasks import resolve_step_human_task


def test_owner_declared_manual_handoff_uses_the_declared_durable_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner-declared waits must not fall through to the generic user menu."""
    issue_dir = tmp_path / ".cafe" / "issues" / "owner-wait"
    playbook = {
        "playbook": {"id": "owner-wait"},
        "steps": {
            "approval": {
                "skill": "phase",
                "role": "operator",
                "assignee_type": "human",
                "human_tasks": [{"trigger": "initial", "task_id": "approval", "outcomes": {}}],
                "on": {},
            }
        },
    }
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("approval", playbook_id="owner-wait")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="approval",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.MANUAL_HANDOFF,
        source="workflow.owner_human",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli_shared,
        "_handle_declared_human_task_handoff",
        lambda **kwargs: captured.update(kwargs) or "after",
    )
    monkeypatch.setattr(
        cli_shared,
        "_handle_user_phase_generic",
        lambda **_kwargs: pytest.fail("owner wait must use its durable task"),
    )

    target = cli_shared._handle_user_phase(
        issue_name="owner-wait",
        issue_dir=issue_dir,
        playbook_data=playbook,
        blackboard=blackboard,
    )

    assert target == "after"
    assert captured["trigger"] == "initial"


def test_no_change_handoff_shows_implementation_output_before_decision(
    tmp_path: Path, monkeypatch
) -> None:
    """The participant sees the implementation evidence before deciding no-change."""
    issue_dir = tmp_path / ".cafe" / "issues" / "no-change"
    output_file = issue_dir / "develop" / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("Implementation reasoning", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop", playbook_id="standard")
    displayed: list[Path] = []
    monkeypatch.setattr(cli_shared, "_print_output_file", displayed.append)
    monkeypatch.setattr(
        "cafe.ui.human_tasks.collect_human_task_payload",
        lambda policy, **_kwargs: {"task": policy.id, "decision": "agree"},
    )

    target = cli_shared._handle_declared_human_task_handoff(
        issue_name="no-change",
        issue_dir=issue_dir,
        blackboard=blackboard,
        from_step="develop",
        summary="",
        playbook_data=PlaybookLoader().load("standard"),
        trigger="no_changes_needed",
    )

    assert displayed == [output_file]
    assert target == "pr"


def test_interactive_handoff_forwards_the_active_durable_task_id(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-002: the production interactive caller binds its payload to the active wait."""
    issue_dir = tmp_path / ".cafe" / "issues" / "durable-interactive"
    playbook = PlaybookLoader().load("standard")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    store.update_handoff_contract(
        blackboard,
        from_step="spec",
        to_owner=HandoffOwner.USER,
        to_step="user",
        intent=HandoffIntent.CONFIRM_OUTPUT,
        source="test",
    )
    policy, binding = resolve_step_human_task(
        playbook_data=playbook, step_name="spec", trigger="confirm_output"
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=blackboard.workflow_id,
        step="spec",
        iteration=1,
        trigger="confirm_output",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    captured: dict[str, object] = {}

    def collect(policy, **kwargs):
        captured.update(kwargs)
        return {"task": policy.id, "decision": "confirm", "human_task_id": kwargs["human_task_id"]}

    monkeypatch.setattr("cafe.ui.human_tasks.collect_human_task_payload", collect)

    target = cli_shared._handle_declared_human_task_handoff(
        issue_name="durable-interactive",
        issue_dir=issue_dir,
        blackboard=blackboard,
        from_step="spec",
        summary="",
        playbook_data=playbook,
        trigger="confirm_output",
    )

    assert captured["human_task_id"] == task.id
    assert target == "plan"


@pytest.mark.parametrize(
    ("trigger", "response"),
    [
        ("need_clarification", {"feedback": "Preserve the legacy handoff."}),
        (
            "no_changes_needed",
            {"decision": "disagree", "feedback": "Cover the interrupted restart."},
        ),
    ],
)
def test_interactive_handoff_recovers_a_persisted_same_step_completion_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
    response: dict[str, str],
) -> None:
    """IT-001/IT-002: restart resumes a stored same-step response without prompting."""
    issue_dir = tmp_path / ".cafe" / "issues" / f"interrupted-{trigger}"
    playbook = PlaybookLoader().load("standard")
    phase_dir = issue_dir / "develop" / "iteration_001"
    phase_dir.mkdir(parents=True)
    (phase_dir / "context.json").write_text('{"end_time": "complete"}', encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    policy, binding = resolve_step_human_task(
        playbook_data=playbook, step_name="develop", trigger=trigger
    )
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=blackboard.workflow_id,
        step="develop",
        iteration=1,
        trigger=trigger,
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    payload = {"task": policy.id, **response, "human_task_id": task.id}
    monkeypatch.setattr(
        "cafe.ui.human_tasks.collect_human_task_payload", lambda *_args, **_kwargs: payload
    )

    with monkeypatch.context() as crashing:
        crashing.setattr(
            BlackboardStore,
            "set_current_step",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        with pytest.raises(RuntimeError, match="interrupted"):
            cli_shared._handle_declared_human_task_handoff(
                issue_name=f"interrupted-{trigger}",
                issue_dir=issue_dir,
                blackboard=blackboard,
                from_step="develop",
                summary="",
                playbook_data=playbook,
                trigger=trigger,
            )

    restarted = store.load_or_create("develop", playbook_id="standard")
    monkeypatch.setattr(
        "cafe.ui.human_tasks.collect_human_task_payload",
        lambda *_args, **_kwargs: pytest.fail("recovery must not re-prompt the participant"),
    )
    target = cli_shared._handle_declared_human_task_handoff(
        issue_name=f"interrupted-{trigger}",
        issue_dir=issue_dir,
        blackboard=restarted,
        from_step="develop",
        summary="",
        playbook_data=playbook,
        trigger=trigger,
    )

    records = HumanTaskRecordStore(issue_dir)
    assert target == "develop"
    assert len(records.results()) == 1
    assert (
        issue_dir / "develop" / "iteration_002" / "user_input.md"
    ).read_text(encoding="utf-8") == response["feedback"]
    assert store.load_or_create("develop").handoff_contract.to_step == "develop"


def test_interactive_handoff_recovers_dynamic_answers_from_completed_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-001/IT-002: dynamic answers resume from their completed task iteration."""
    issue_dir = tmp_path / ".cafe" / "issues" / "interrupted-dynamic-answers"
    playbook = PlaybookLoader().load("standard")
    phase_dir = issue_dir / "spec" / "iteration_001"
    phase_dir.mkdir(parents=True)
    (phase_dir / "context.json").write_text('{"end_time": "complete"}', encoding="utf-8")
    (phase_dir / "questions.xml").write_text(
        (
            "<questions>\n"
            "  <question id=\"scope\"><title>Scope?</title><options><option>Small</option>"
            "</options></question>\n"
            "</questions>"
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="standard")
    store.set_current_step(blackboard, "user")
    policy, binding = resolve_step_human_task(
        playbook_data=playbook, step_name="spec", trigger="need_clarification"
    )
    assert policy.questions_from_xml
    task = HumanTaskRecordStore(issue_dir).materialize(
        workflow_id=blackboard.workflow_id,
        step="spec",
        iteration=1,
        trigger="need_clarification",
        policy_id=policy.id,
        prompt=policy.prompt,
        expected_result=policy.model_dump(mode="json"),
        continuations=binding.outcomes,
        assignee_type="user",
    )
    payload = {
        "task": policy.id,
        "answers": {"scope": "Small"},
        "human_task_id": task.id,
    }
    monkeypatch.setattr(
        "cafe.ui.human_tasks.collect_human_task_payload", lambda *_args, **_kwargs: payload
    )

    with monkeypatch.context() as crashing:
        crashing.setattr(
            BlackboardStore,
            "set_current_step",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
        with pytest.raises(RuntimeError, match="interrupted"):
            cli_shared._handle_declared_human_task_handoff(
                issue_name="interrupted-dynamic-answers",
                issue_dir=issue_dir,
                blackboard=blackboard,
                from_step="spec",
                summary="",
                playbook_data=playbook,
                trigger="need_clarification",
            )
    persisted_input = issue_dir / "spec" / "iteration_002" / "user_input.md"
    assert persisted_input.read_text(encoding="utf-8") == "scope: Small"
    assert not (persisted_input.parent / "questions.xml").exists()

    restarted = store.load_or_create("spec", playbook_id="standard")
    monkeypatch.setattr(
        "cafe.ui.human_tasks.collect_human_task_payload",
        lambda *_args, **_kwargs: pytest.fail("recovery must not re-prompt the participant"),
    )
    target = cli_shared._handle_declared_human_task_handoff(
        issue_name="interrupted-dynamic-answers",
        issue_dir=issue_dir,
        blackboard=restarted,
        from_step="spec",
        summary="",
        playbook_data=playbook,
        trigger="need_clarification",
    )

    records = HumanTaskRecordStore(issue_dir)
    assert target == "spec"
    assert len(records.results()) == 1
    assert (issue_dir / "spec" / "iteration_002" / "user_input.md").read_text(
        encoding="utf-8"
    ) == "scope: Small"
    assert store.load_or_create("spec").handoff_contract.to_step == "spec"
