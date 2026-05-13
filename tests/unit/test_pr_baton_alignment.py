"""Tests for playbook baton alignment after PR step execution."""

import json
from pathlib import Path

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.phases.generic_workflow_step import align_pr_baton_after_execution


def _bootstrap_issue(issue_dir: Path) -> None:
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_step": "pr",
                "playbook_id": "default",
                "artifacts": {},
                "events": [],
                "decisions": [],
                "handoff_summary": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_align_pr_baton_updates_when_needs_changes_and_stale(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-baton"
    _bootstrap_issue(issue_dir)

    playbook = {"steps": {"pr": {}, "develop": {}, "review": {}}}
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "agent",
                "to_step": "pr",
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-05-10T12:00:00+08:00",
                "source": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr", playbook_id="default")

    align_pr_baton_after_execution(
        issue_dir=issue_dir,
        playbook=playbook,
        blackboard_state=state,
        step_name="pr",
        status_code="CAFE_NEEDS_CHANGES",
    )

    loaded = BlackboardStore(issue_dir).load_or_create("pr", playbook_id="default")
    contract = loaded.handoff_contract
    assert contract is not None
    assert contract.to_step == "develop"
    assert contract.to_owner == HandoffOwner.AGENT
    assert contract.intent == HandoffIntent.AWAIT_AGENT


def test_align_pr_baton_noop_when_status_not_needs_changes(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo-pr-skip"
    _bootstrap_issue(issue_dir)

    playbook = {"steps": {"pr": {}, "develop": {}}}
    (issue_dir / "next_step.txt").write_text(
        json.dumps(
            {
                "version": 1,
                "from_step": "pr",
                "to_owner": "agent",
                "to_step": "pr",
                "intent": "await_agent",
                "status_code": "",
                "created_at": "2026-05-10T12:00:00+08:00",
                "source": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr", playbook_id="default")

    align_pr_baton_after_execution(
        issue_dir=issue_dir,
        playbook=playbook,
        blackboard_state=state,
        step_name="pr",
        status_code="CAFE_CONFIRMED",
    )

    loaded = BlackboardStore(issue_dir).load_or_create("pr", playbook_id="default")
    assert loaded.handoff_contract is not None
    assert loaded.handoff_contract.to_step == "pr"
