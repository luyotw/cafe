"""Regression coverage for interactive human-task presentation."""

from __future__ import annotations

from pathlib import Path

from cafe.core.blackboard import BlackboardStore
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui import cli_shared


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
