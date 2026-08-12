"""Regression coverage for interactive human-task presentation."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui import cli_shared
from cafe.ui.human_tasks import resolve_step_human_task


def test_no_change_handoff_shows_implementation_output_before_decision(
    tmp_path: Path, monkeypatch
) -> None:
    """The participant sees the implementation evidence before deciding no-change."""
    issue_dir = tmp_path / ".cafe" / "issues" / "no-change"
    output_file = issue_dir / "develop" / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("Implementation reasoning", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("develop", playbook_id="default")
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
        playbook_data=PlaybookLoader().load("default"),
        trigger="no_changes_needed",
    )

    assert displayed == [output_file]
    assert target == "pr"


def test_interactive_handoff_forwards_the_active_durable_task_id(
    tmp_path: Path, monkeypatch
) -> None:
    """IT-002: the production interactive caller binds its payload to the active wait."""
    issue_dir = tmp_path / ".cafe" / "issues" / "durable-interactive"
    playbook = PlaybookLoader().load("default")
    store = BlackboardStore(issue_dir)
    blackboard = store.load_or_create("spec", playbook_id="default")
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
