"""Contract tests for the Driver-owned workflow progress renderer."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core.audit_events import AuditEventStore
from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts/render_workflow_progress.py"


def _module():
    spec = importlib.util.spec_from_file_location("workflow_progress_renderer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _custom_playbook() -> dict[str, object]:
    return {
        "playbook": {"id": "custom", "conversation_locale": "en-US"},
        "steps": {
            "資料盤點": {
                "assignee_type": "agent",
                "on": {"await_agent": "publish-draft", "need_clarification": "資料盤點"},
                "human_tasks": [],
            },
            "publish-draft": {
                "assignee_type": "agent",
                "on": {"await_agent": "_done", "manual_handoff": "資料盤點"},
                "human_tasks": [
                    {
                        "trigger": "confirm_output",
                        "task_id": "approval",
                        "outcomes": {"confirm": "_done", "revise": "資料盤點"},
                    }
                ],
            },
        },
        "entry_point": "資料盤點",
    }


def _contract() -> dict[str, object]:
    return {
        "confirmation_contract": {
            "user_required": ["publish-draft"],
            "driver_confirmable": [],
            "mandatory_human_stops": [],
        },
        "proactive_review": {
            "phase_decisions": [
                {
                    "phase": "資料盤點",
                    "decision": "not_required",
                    "rationale": "No gate.",
                },
                {
                    "phase": "publish-draft",
                    "decision": "required",
                    "rationale": "Review before confirmation.",
                },
            ]
        },
    }


def _unknown_closeout_state() -> dict[str, str]:
    return {"deliver": "unknown", "cleanup": "unknown"}


def _write_runtime_state(issue_dir: Path, state: dict) -> None:
    state = dict(state)
    events = state.pop("events", [])
    workflow_id = state.setdefault("workflow_id", "workflow-1")
    (issue_dir / "blackboard.json").write_text(json.dumps(state), encoding="utf-8")
    audit = AuditEventStore(issue_dir)
    if audit.root.exists():
        shutil.rmtree(audit.root)
    audit.initialize(workflow_id)
    for event in events:
        sequence = audit.reserve(workflow_id)
        audit.commit(
            workflow_id,
            {
                "timestamp": "2026-09-20T01:00:00+00:00",
                "step": "",
                "message": "",
                "data": {},
                **event,
                "workflow_id": workflow_id,
                "sequence": sequence,
                "event_id": f"event-{sequence}",
            },
        )


def _append_runtime_event(issue_dir: Path, event: dict) -> None:
    workflow_id = "workflow-1"
    audit = AuditEventStore(issue_dir)
    sequence = audit.reserve(workflow_id)
    audit.commit(
        workflow_id,
        {
            "timestamp": "2026-09-20T01:00:00+00:00",
            "step": "",
            "message": "",
            "data": {},
            **event,
            "workflow_id": workflow_id,
            "sequence": sequence,
            "event_id": f"event-{sequence}",
        },
    )


def _write_runtime(issue_dir: Path) -> None:
    issue_dir.mkdir(parents=True)
    _write_runtime_state(
        issue_dir,
        {
            "schema_version": 4,
            "workflow_id": "workflow-1",
            "playbook_id": "custom",
            "current_step": "資料盤點",
            "events": [
                {
                    "timestamp": "2026-09-20T01:00:00+00:00",
                    "step": "publish-draft",
                    "event_type": "step_started",
                    "message": "",
                    "data": {"step": "publish-draft", "attempt": 1},
                },
                {
                    "timestamp": "2026-09-20T01:01:00+00:00",
                    "step": "publish-draft",
                    "event_type": "step_completed",
                    "message": "",
                    "data": {"step": "publish-draft", "attempt": 1},
                },
                {
                    "timestamp": "2026-09-20T01:02:00+00:00",
                    "step": "publish-draft",
                    "event_type": "transition",
                    "message": "",
                    "data": {"from": "publish-draft", "to": "資料盤點"},
                },
                {
                    "timestamp": "2026-09-20T01:03:00+00:00",
                    "step": "資料盤點",
                    "event_type": "step_started",
                    "message": "",
                    "data": {"step": "資料盤點", "attempt": 2},
                },
            ],
            "handoff_contract": {
                "version": 1,
                "from_step": "資料盤點",
                "to_owner": "agent",
                "to_step": "資料盤點",
                "intent": "await_agent",
            },
        },
    )
    (issue_dir / "human_tasks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow_id": "workflow-1",
                "tasks": [
                    {
                        "id": "task-1",
                        "workflow_id": "workflow-1",
                        "step": "publish-draft",
                        "iteration": 1,
                        "trigger": "confirm_output",
                        "status": "completed",
                        "expected_result": {
                            "decisions": [
                                {"id": "confirm"},
                                {"id": "revise", "correction": True},
                            ]
                        },
                        "continuations": {"confirm": "_done", "revise": "資料盤點"},
                    }
                ],
                "results": [
                    {
                        "task_id": "task-1",
                        "workflow_id": "workflow-1",
                        "payload": {"decision": "revise", "continuation": "資料盤點"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    iteration = issue_dir / "資料盤點" / "iteration_002"
    iteration.mkdir(parents=True)
    (iteration / "iteration.json").write_text('{"iteration": 2}', encoding="utf-8")


def test_renderer_preserves_custom_phase_names_and_localizes_only_annotations() -> None:
    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="zh-TW",
        driver_state={
            "proactive_review": {"publish-draft": "in_progress"},
            "deliver": "unknown",
            "cleanup": "unknown",
        },
    )

    assert "○ 資料盤點 · 待執行" in rendered
    assert "○ publish-draft · 待執行" in rendered
    assert (
        "○ publish-draft · 待執行\n│\n"
        "▶\ufe0e publish-draft：流程管理員主動審查 · 進行中\n│\n"
        "○ publish-draft：使用者確認（流程管理員不可代理） · 待執行"
    ) in rendered
    assert "？ deliver（收尾） · 狀態未知" in rendered
    assert "？ cleanup（收尾） · 狀態未知" in rendered
    assert "\ufe0f" not in rendered
    assert "資料盤點" in rendered and "publish-draft" in rendered


def test_omitted_review_is_pending_until_its_phase_finishes(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "active",
            "events": [
                {
                    "event_type": "step_started",
                    "step": "completed",
                    "data": {"step": "completed", "attempt": 1},
                },
                {
                    "event_type": "step_completed",
                    "step": "completed",
                    "data": {"step": "completed", "attempt": 1},
                },
                {
                    "event_type": "step_started",
                    "step": "active",
                    "data": {"step": "active", "attempt": 1},
                },
            ],
        },
    )
    phases = ("completed", "active", "future")

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "review-defaults"},
            "steps": {
                "completed": {"on": {"await_agent": "active"}},
                "active": {"on": {"await_agent": "future"}},
                "future": {"on": {"await_agent": "_done"}},
            },
        },
        contract={
            "proactive_review": {
                "phase_decisions": [{"phase": phase, "decision": "required"} for phase in phases]
            }
        },
        locale="zh-TW",
        issue_dir=issue_dir,
        driver_state={"deliver": "pending", "cleanup": "pending"},
    )

    assert "？ completed：流程管理員主動審查 · 狀態未知" in rendered
    assert "○ active：流程管理員主動審查 · 待執行" in rendered
    assert "○ future：流程管理員主動審查 · 待執行" in rendered
    assert "？ active：流程管理員主動審查" not in rendered
    assert "？ future：流程管理員主動審查" not in rendered


def test_renderer_uses_current_iteration_and_revise_outcome_as_checkpoint_state(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    before = {
        path: path.read_bytes()
        for path in (issue_dir / "blackboard.json", issue_dir / "human_tasks.json")
    }

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="zh-TW",
        issue_dir=issue_dir,
        driver_state={
            "proactive_review": {"publish-draft": "completed"},
            "deliver": "unknown",
            "cleanup": "unknown",
        },
    )

    assert "▶\ufe0e 資料盤點 · 第 2 輪 · 進行中" in rendered
    assert "↩\ufe0e publish-draft：使用者確認（流程管理員不可代理） · 已退回" in rendered
    assert "✓ publish-draft：使用者確認（流程管理員不可代理） · 已完成" not in rendered
    assert "→" not in rendered
    assert before == {path: path.read_bytes() for path in before}


def test_driver_state_cannot_override_runtime_and_rejects_invalid_values(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    module = _module()

    with pytest.raises(ValueError, match="runtime phase"):
        module.render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            issue_dir=issue_dir,
            driver_state={"資料盤點": "completed"},
        )
    with pytest.raises(ValueError, match="unknown proactive review phase"):
        module.render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            driver_state={
                "proactive_review": {"missing": "completed"},
                "deliver": "pending",
                "cleanup": "pending",
            },
        )
    with pytest.raises(ValueError, match="invalid progress status"):
        module.render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            driver_state={"deliver": "done", "cleanup": "pending"},
        )
    with pytest.raises(ValueError, match="missing required closeout item: cleanup"):
        module.render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            driver_state={"deliver": "pending"},
        )


def test_required_closeouts_are_always_visible_and_custom_same_named_phase_is_distinct() -> None:
    playbook = _custom_playbook()
    playbook["steps"]["deliver"] = {"on": {"await_agent": "_done"}, "human_tasks": []}
    module = _module()

    rendered = module.render_progress(
        playbook=playbook,
        contract=_contract(),
        locale="en",
        driver_state={"deliver": "completed", "cleanup": "pending"},
    )

    assert "○ deliver · Pending" in rendered
    assert "○ deliver · Pending\n│" in rendered
    assert "✓ deliver (closeout) · Completed" in rendered
    assert "○ cleanup (closeout) · Pending" in rendered


def test_renderer_reports_missing_workflow_without_inventing_success() -> None:
    assert _module().render_progress(locale="zh-TW") == "流程尚未建立"
    assert _module().render_progress(locale="fr-FR") == "Workflow has not been established."


def test_effective_playbook_applies_issue_overrides(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "playbook_id: standard\nplaybook_overrides:\n  steps:\n    review:\n"
        "      max_attempts_per_cycle: 3\n",
        encoding="utf-8",
    )

    effective = _module().load_effective_playbook(
        project_root=PROJECT_ROOT,
        playbook_id="standard",
        issue_dir=issue_dir,
    )

    assert effective["steps"]["review"]["max_attempts_per_cycle"] == 3
    assert list(effective["steps"]) == list(PlaybookLoader().load("standard")["steps"])


def test_pending_confirmation_blocked_and_skipped_use_durable_evidence(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    blackboard_path = issue_dir / "blackboard.json"
    blackboard = json.loads(blackboard_path.read_text(encoding="utf-8"))
    events = list(AuditEventStore(issue_dir).iter_records(blackboard["workflow_id"]))
    events.extend(
        [
            {
                "event_type": "workflow_step_skipped",
                "step": "publish-draft",
                "data": {"step": "publish-draft"},
            },
            {
                "event_type": "workflow_blocked",
                "step": "資料盤點",
                "data": {"step": "資料盤點"},
            },
        ]
    )
    _write_runtime_state(issue_dir, {**blackboard, "events": events})
    tasks_path = issue_dir / "human_tasks.json"
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    tasks["tasks"][0]["status"] = "pending"
    tasks["results"] = []
    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en-US",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "! 資料盤點 · iteration 2 · Blocked" in rendered
    assert "− publish-draft · Skipped" in rendered
    assert (
        "⏸\ufe0e publish-draft: user confirmation (manager may not act) · Awaiting confirmation"
        in rendered
    )


def test_direct_playbook_and_archived_issue_cli_are_supported(tmp_path: Path) -> None:
    archive = tmp_path / "archived" / "issue539"
    archive.mkdir(parents=True)
    (archive / "issue.yaml").write_text("playbook_id: direct-subagent-review\n", encoding="utf-8")
    _write_runtime_state(
        archive,
        {
            "schema_version": 4,
            "workflow_id": "workflow-archive",
            "playbook_id": "direct-subagent-review",
            "current_step": "done",
            "events": [
                {
                    "event_type": "step_completed",
                    "step": "develop",
                    "data": {"step": "develop", "attempt": 1},
                },
                {
                    "event_type": "step_started",
                    "step": "pr",
                    "data": {"step": "pr", "attempt": 1},
                },
            ],
        },
    )
    contract_dir = archive / "driver"
    contract_dir.mkdir()
    (contract_dir / "contract.json").write_text(
        json.dumps(
            {
                "policy": {
                    "confirmation_contract": {
                        "user_required": [],
                        "driver_confirmable": [],
                        "mandatory_human_stops": ["pr"],
                    },
                    "proactive_review": {"phase_decisions": []},
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(PROJECT_ROOT),
            "--issue-dir",
            str(archive),
            "--locale",
            "en",
            "--driver-state",
            '{"deliver":"completed","cleanup":"completed"}',
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "✓ develop · Completed" in result.stdout
    assert "▶\ufe0e pr · In progress" in result.stdout
    assert "✓ deliver (closeout) · Completed" in result.stdout
    assert "✓ cleanup (closeout) · Completed" in result.stdout
    assert "\ufe0f" not in result.stdout


def test_cli_requires_both_fixed_closeout_states() -> None:
    base = [
        sys.executable,
        str(SCRIPT),
        "--project-root",
        str(PROJECT_ROOT),
        "--playbook",
        "direct-subagent-review",
    ]

    missing_state = subprocess.run(
        base,
        text=True,
        capture_output=True,
        check=False,
    )
    missing_cleanup = subprocess.run(
        [*base, "--driver-state", '{"deliver":"pending"}'],
        text=True,
        capture_output=True,
        check=False,
    )

    assert missing_state.returncode == 2
    assert "must provide required closeout items: deliver, cleanup" in missing_state.stderr
    assert missing_cleanup.returncode == 2
    assert "missing required closeout item: cleanup" in missing_cleanup.stderr


def test_previous_revision_does_not_approve_the_new_iteration(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    records_path = issue_dir / "human_tasks.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["tasks"][0]["completed_at"] = "2026-09-20T01:03:00+00:00"
    records["results"][0]["payload"] = {"decision": "confirm", "continuation": "_done"}
    records_path.write_text(json.dumps(records), encoding="utf-8")
    new_iteration = issue_dir / "publish-draft" / "iteration_002"
    new_iteration.mkdir(parents=True)
    (new_iteration / "iteration.json").write_text('{"iteration": 2}', encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "○ publish-draft: user confirmation (manager may not act) · Pending" in rendered
    assert "✓ publish-draft: user confirmation (manager may not act) · Completed" not in rendered
    assert "→" not in rendered


def test_post_confirmation_reentry_does_not_reopen_completed_confirmation(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    records_path = issue_dir / "human_tasks.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["tasks"][0]["continuations"]["confirm"] = "publish-draft"
    records["tasks"][0]["completed_at"] = "2026-09-20T01:04:00+00:00"
    records["results"][0]["payload"] = {
        "decision": "confirm",
        "continuation": "publish-draft",
    }
    records_path.write_text(json.dumps(records), encoding="utf-8")
    reentry = issue_dir / "publish-draft" / "iteration_005"
    reentry.mkdir(parents=True)
    (reentry / "iteration.json").write_text(
        '{"iteration": 5, "timestamp": "2026-09-20T01:04:02+00:00", "end_time": "2026-09-20T01:04:00+00:00"}',
        encoding="utf-8",
    )
    _append_runtime_event(
        issue_dir,
        {
            "timestamp": "2026-09-20T01:04:01+00:00",
            "step": "publish-draft",
            "event_type": "human_task_completed",
            "data": {
                "step": "publish-draft",
                "trigger": "confirm_output",
                "to_step": "publish-draft",
            },
        },
    )
    _append_runtime_event(
        issue_dir,
        {
            "timestamp": "2026-09-20T01:04:02+00:00",
            "step": "publish-draft",
            "event_type": "step_started",
            "data": {"step": "publish-draft", "attempt": 1},
        },
    )
    _append_runtime_event(
        issue_dir,
        {
            "timestamp": "2026-09-20T01:04:03+00:00",
            "step": "資料盤點",
            "event_type": "transition",
            "data": {
                "from": "publish-draft",
                "to": "資料盤點",
                "source_artifact": {"version": 3},
                "transition_intent": "manual_handoff",
            },
        },
    )

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "○ publish-draft: user confirmation (manager may not act) · Pending" in rendered

    _append_runtime_event(
        issue_dir,
        {
            "timestamp": "2026-09-20T01:04:04+00:00",
            "step": "資料盤點",
            "event_type": "transition",
            "data": {
                "from": "publish-draft",
                "to": "資料盤點",
                "source_artifact": {"version": 3},
                "transition_intent": "await_agent",
            },
        },
    )
    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "✓ publish-draft: user confirmation (manager may not act) · Completed" in rendered

    later_revision = issue_dir / "publish-draft" / "iteration_006"
    later_revision.mkdir(parents=True)
    (later_revision / "iteration.json").write_text(
        '{"iteration": 6, "timestamp": "2026-09-20T01:05:00+00:00"}',
        encoding="utf-8",
    )
    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "○ publish-draft: user confirmation (manager may not act) · Pending" in rendered


def test_cli_without_confirmed_contract_reports_unestablished_workflow(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(issue_dir, {"playbook_id": "direct-subagent-review", "events": []})

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--issue-dir", str(issue_dir), "--locale", "zh-TW"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "流程尚未建立"


def test_compact_spine_omits_raw_routes_without_inventing_a_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "A",
            "events": [],
            "handoff_contract": {
                "from_step": "A",
                "to_step": "A",
                "to_owner": "agent",
                "intent": "await_agent",
                "source": "workflow.start_step_override",
            },
        },
    )
    playbook = {
        "playbook": {"id": "non-topological"},
        "entry_point": "A",
        "steps": {
            "A": {"on": {"await_agent": "C"}},
            "B": {"on": {"await_agent": "_done"}},
            "C": {"on": {"await_agent": "B"}},
        },
    }

    rendered = _module().render_progress(
        playbook=playbook,
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "↩ A → A" not in rendered
    assert "↩ C → B" not in rendered
    assert "await_agent →" not in rendered
    assert rendered.splitlines() == [
        "○ A · Pending",
        "│",
        "○ C · Pending",
        "│",
        "○ B · Pending",
        "│",
        "？ deliver (closeout) · Unknown",
        "│",
        "？ cleanup (closeout) · Unknown",
    ]


def test_spine_separates_sibling_branches_and_unreachable_phases() -> None:
    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "branched"},
            "entry_point": "A",
            "steps": {
                "A": {"on": {"await_agent": "B", "no_changes_needed": "C"}},
                "B": {"on": {"await_agent": "D"}},
                "C": {"on": {"await_agent": "D"}},
                "D": {"on": {"await_agent": "_done"}},
                "unreachable": {"on": {"await_agent": "_done"}},
            },
        },
        contract={},
        locale="en",
        driver_state=_unknown_closeout_state(),
    )

    assert "○ A · Pending\n│\n○ B · Pending\n│\n○ D · Pending" in rendered
    assert "○ D · Pending\n\n○ C · Pending" in rendered
    assert "○ C · Pending\n\n○ unreachable · Pending" in rendered


def test_latest_iteration_metadata_cannot_be_overwritten_by_prior_completion(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "review",
            "events": [
                {
                    "event_type": "step_started",
                    "step": "review",
                    "data": {"step": "review", "attempt": 2},
                }
            ],
        },
    )
    previous = issue_dir / "review" / "iteration_001"
    previous.mkdir(parents=True)
    (previous / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "status_code": "confirmed",
                "end_time": "2026-09-20T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    current = issue_dir / "review" / "iteration_002"
    current.mkdir()
    (current / "iteration.json").write_text('{"iteration": 2}', encoding="utf-8")

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "rerun"},
            "steps": {"review": {"on": {"await_agent": "_done"}}},
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "▶\ufe0e review · iteration 2 · In progress" in rendered
    assert "✓ review · Completed" not in rendered


@pytest.mark.parametrize("payload", [{}, {"decision": "missing"}])
def test_completed_confirmation_requires_a_recognized_outcome(
    tmp_path: Path, payload: dict[str, str]
) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    records_path = issue_dir / "human_tasks.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["results"][0]["payload"] = payload
    records_path.write_text(json.dumps(records), encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "？ publish-draft: user confirmation (manager may not act) · Unknown" in rendered
    assert "✓ publish-draft: user confirmation (manager may not act) · Completed" not in rendered


def test_completed_confirmation_renders_only_a_declared_non_correction_outcome(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    records_path = issue_dir / "human_tasks.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["results"][0]["payload"] = {"decision": "confirm", "continuation": "_done"}
    records_path.write_text(json.dumps(records), encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "✓ publish-draft: user confirmation (manager may not act) · Completed" in rendered


@pytest.mark.parametrize("iteration_evidence", ["directory", "event"])
def test_retry_attempt_does_not_invalidate_confirmed_iteration(
    tmp_path: Path, iteration_evidence: str
) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    records_path = issue_dir / "human_tasks.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["tasks"][0]["iteration"] = 5
    records["results"][0]["payload"] = {"decision": "confirm", "continuation": "_done"}
    records_path.write_text(json.dumps(records), encoding="utf-8")
    event_data = {"step": "publish-draft", "attempt": 6}
    if iteration_evidence == "directory":
        iteration = issue_dir / "publish-draft" / "iteration_005"
        iteration.mkdir(parents=True)
        (iteration / "iteration.json").write_text('{"iteration": 5}', encoding="utf-8")
    else:
        event_data["iteration"] = {"number": 5}
    _append_runtime_event(
        issue_dir,
        {"event_type": "step_completed", "step": "publish-draft", "data": event_data},
    )
    # Callback attempt counters are not artifact revision numbers either.
    _append_runtime_event(
        issue_dir,
        {
            "event_type": "workflow_event_callback_enqueued",
            "step": "publish-draft",
            "data": {"step": "publish-draft", "attempt": 7},
        },
    )

    def render() -> str:
        return _module().render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            issue_dir=issue_dir,
            driver_state=_unknown_closeout_state(),
        )

    rendered = render()
    assert "✓ publish-draft · iteration 5 · Completed" in rendered
    assert "✓ publish-draft: user confirmation (manager may not act) · Completed" in rendered

    new_iteration = issue_dir / "publish-draft" / "iteration_006"
    new_iteration.mkdir(parents=True)
    (new_iteration / "iteration.json").write_text('{"iteration": 6}', encoding="utf-8")
    rendered = render()
    assert "publish-draft · iteration 6" in rendered
    assert "○ publish-draft: user confirmation (manager may not act) · Pending" in rendered


def test_forward_skip_review_manual_handoff_is_not_a_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "pr",
            "events": [
                {
                    "event_type": "transition",
                    "step": "develop",
                    "data": {
                        "from": "develop",
                        "to": "pr",
                        "status_code": "skip_review",
                        "transition_intent": "manual_handoff",
                    },
                }
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "skip-review"},
            "steps": {
                "develop": {
                    "on": {"await_agent": "review", "manual_handoff": "pr"},
                    "allowed_goto": ["pr"],
                },
                "review": {"on": {"await_agent": "pr"}, "allowed_goto": ["develop"]},
                "pr": {"on": {"await_agent": "_done"}, "allowed_goto": ["develop"]},
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    source_line = next(line for line in rendered.splitlines() if " develop · " in line)
    assert "↩" not in source_line
    assert "Returned" not in source_line


@pytest.mark.parametrize(
    ("source", "target"), [("review", "develop"), ("publish-draft", "資料盤點")]
)
def test_upstream_baton_without_feedback_returns_until_the_source_runs_again(
    tmp_path: Path, source: str, target: str
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    events = [
        {
            "event_type": "step_started",
            "step": source,
            "data": {"step": source, "attempt": 2},
        },
        {
            "event_type": "step_completed",
            "step": source,
            "data": {"step": source, "attempt": 2, "status_code": "BATON_MANUAL_HANDOFF"},
        },
        {
            "event_type": "transition",
            "step": source,
            "data": {
                "from": source,
                "to": target,
                "source": "baton",
                "status_code": "BATON_MANUAL_HANDOFF",
                "transition_intent": "manual_handoff",
            },
        },
        {
            "event_type": "step_started",
            "step": target,
            "data": {"step": target, "attempt": 3},
        },
    ]
    _write_runtime_state(issue_dir, {"current_step": target, "events": events})
    completed_iteration = issue_dir / source / "iteration_002"
    completed_iteration.mkdir(parents=True)
    (completed_iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "status_code": "BATON_MANUAL_HANDOFF",
                "end_time": "2026-09-23T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    target_iteration = issue_dir / target / "iteration_003"
    target_iteration.mkdir(parents=True)
    (target_iteration / "iteration.json").write_text('{"iteration": 3}', encoding="utf-8")
    playbook = {
        "playbook": {"id": "upstream-baton"},
        "steps": {
            target: {"on": {"await_agent": source}},
            source: {"on": {"await_agent": "_done", "manual_handoff": target}},
        },
    }
    module = _module()
    before = {path: path.read_bytes() for path in issue_dir.rglob("*.json")}

    rendered = module.render_progress(
        playbook=playbook,
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert not any(event["event_type"] == "workflow_feedback_delivered" for event in events)
    assert f"↩\ufe0e {source} · iteration 2 · Returned" in rendered
    assert f"✓ {source} · iteration 2 · Completed" not in rendered
    assert f"▶\ufe0e {target} · iteration 3 · In progress" in rendered
    assert before == {path: path.read_bytes() for path in before}

    latest_iteration = issue_dir / source / "iteration_003"
    latest_iteration.mkdir()
    for event_type, symbol, status in (
        ("step_started", "▶\ufe0e", "In progress"),
        ("step_completed", "✓", "Completed"),
    ):
        events.append(
            {"event_type": event_type, "step": source, "data": {"step": source, "attempt": 3}}
        )
        _write_runtime_state(issue_dir, {"current_step": source, "events": events})
        metadata = {"iteration": 3}
        if event_type == "step_completed":
            metadata.update(status_code="confirmed", end_time="2026-09-23T02:00:00+00:00")
        (latest_iteration / "iteration.json").write_text(json.dumps(metadata), encoding="utf-8")

        rendered = module.render_progress(
            playbook=playbook,
            contract={},
            locale="en",
            issue_dir=issue_dir,
            driver_state=_unknown_closeout_state(),
        )

        assert f"{symbol} {source} · iteration 3 · {status}" in rendered
        assert f"↩\ufe0e {source}" not in rendered
        assert f"{source} · iteration 2" not in rendered


@pytest.mark.parametrize("target", ["unrelated", "source"])
def test_manual_handoff_without_a_distinct_upstream_target_is_not_a_return(
    tmp_path: Path, target: str
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": target,
            "events": [
                {
                    "event_type": "step_completed",
                    "step": "source",
                    "data": {"step": "source", "attempt": 1},
                },
                {
                    "event_type": "transition",
                    "step": "source",
                    "data": {
                        "from": "source",
                        "to": target,
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "non-correction-baton"},
            "steps": {
                "source": {"on": {"await_agent": "_done", "manual_handoff": target}},
                "unrelated": {
                    "on": {"await_agent": "_done", "manual_handoff": "source"},
                    "allowed_goto": ["source"],
                },
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "✓ source · Completed" in rendered
    assert "↩\ufe0e source" not in rendered
    assert "Returned" not in rendered


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [("BATON_MANUAL_HANDOFF", "✓ C · Completed"), ("needs_changes", "↩\ufe0e C · Returned")],
)
def test_normal_cycle_requires_explicit_correction_to_infer_a_return(
    tmp_path: Path, status_code: str, expected: str
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "A",
            "events": [
                {"event_type": "step_completed", "step": "C", "data": {}},
                {
                    "event_type": "transition",
                    "step": "C",
                    "data": {
                        "from": "C",
                        "to": "A",
                        "status_code": status_code,
                        "transition_intent": "manual_handoff",
                    },
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "normal-cycle"},
            "steps": {
                "A": {"on": {"await_agent": "B"}},
                "B": {"on": {"await_agent": "C"}},
                "C": {"on": {"await_agent": "A", "manual_handoff": "A"}},
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert expected in rendered
    if status_code == "BATON_MANUAL_HANDOFF":
        assert "↩" not in rendered
        assert "Returned" not in rendered


def test_declared_correction_manual_handoff_is_a_formal_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "develop",
            "events": [
                {
                    "event_type": "transition",
                    "step": "review",
                    "data": {
                        "from": "review",
                        "to": "develop",
                        "status_code": "needs_changes",
                        "transition_intent": "manual_handoff",
                    },
                }
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "correction"},
            "steps": {
                "develop": {"on": {"await_agent": "review"}},
                "review": {
                    "on": {"await_agent": "_done", "manual_handoff": "develop"},
                    "allowed_goto": ["develop"],
                },
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "↩\ufe0e review · Returned" in rendered
    assert "→" not in rendered


def test_delivered_pr_feedback_baton_is_a_formal_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "develop",
            "events": [
                {
                    "event_type": "step_started",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": 2},
                },
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "pr",
                    "data": {
                        "step": "pr",
                        "delivery_id": "delivery-1",
                        "source_identities": ["github_pr:comment:1"],
                    },
                },
                {
                    "event_type": "transition",
                    "step": "pr",
                    "data": {
                        "from": "pr",
                        "to": "develop",
                        "source": "baton",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "direct"},
            "steps": {
                "develop": {"on": {"await_agent": "pr"}},
                "pr": {
                    "behavior": {
                        "feedback_target": "pr",
                        "feedback_artifact": "workflow_feedback",
                        "feedback_source_kind": "github_pr",
                        "feedback_todo_source": "pr_comment",
                        "feedback_todo_id_prefix": "PRC",
                    },
                    "on": {"manual_handoff": "develop"},
                    "allowed_goto": ["develop"],
                },
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "↩\ufe0e pr · iteration 2 · Returned" in rendered
    assert "→" not in rendered


def test_repeated_returns_project_only_latest_phase_states(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    events: list[dict[str, object]] = []
    for source_iteration, target_iteration in ((11, 6), (12, 7)):
        events.extend(
            [
                {
                    "event_type": "workflow_feedback_delivery_prepared",
                    "step": "pr",
                    "data": {
                        "step": "pr",
                        "iteration": {
                            "directory": f"pr/iteration_{source_iteration:03d}",
                            "number": source_iteration,
                        },
                        "source_identities": [f"local_review:pr:{source_iteration}"],
                    },
                },
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "pr",
                    "data": {
                        "step": "pr",
                        "source_identities": [f"local_review:pr:{source_iteration}"],
                    },
                },
                {
                    "event_type": "transition",
                    "step": "pr",
                    "data": {
                        "from": "pr",
                        "to": "develop",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
                {
                    "event_type": "step_started",
                    "step": "develop",
                    "data": {"step": "develop", "iteration": target_iteration},
                },
            ]
        )
    _write_runtime_state(issue_dir, {"current_step": "develop", "events": events})

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "direct"},
            "steps": {
                "develop": {"on": {"await_agent": "pr"}},
                "pr": {
                    "behavior": {
                        "feedback_target": "pr",
                        "feedback_artifact": "workflow_feedback",
                        "feedback_source_kind": "local_review",
                        "feedback_todo_source": "pr_comment",
                        "feedback_todo_id_prefix": "REV",
                    },
                    "on": {"manual_handoff": "develop"},
                    "allowed_goto": ["develop"],
                },
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "▶\ufe0e develop · iteration 7 · In progress" in rendered
    assert "↩\ufe0e pr · iteration 12 · Returned" in rendered
    assert "iteration 11" not in rendered
    assert "→" not in rendered


def test_default_projection_suppresses_superseded_returns_and_shows_active_checkpoints(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "pr",
            "events": [
                {
                    "event_type": "workflow_feedback_delivery_prepared",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": {"number": 13}},
                },
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "pr",
                    "data": {"step": "pr", "source_identities": ["review:13"]},
                },
                {
                    "event_type": "transition",
                    "step": "pr",
                    "data": {
                        "from": "pr",
                        "to": "develop",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
                {
                    "event_type": "step_started",
                    "step": "develop",
                    "data": {"step": "develop", "iteration": 6},
                },
                {
                    "event_type": "step_completed",
                    "step": "develop",
                    "data": {"step": "develop", "iteration": 6},
                },
                {
                    "event_type": "step_started",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": 14},
                },
                {
                    "event_type": "step_completed",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": 14},
                },
            ],
        },
    )
    (issue_dir / "human_tasks.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-pr-14",
                        "step": "pr",
                        "iteration": 14,
                        "trigger": "confirm_output",
                        "status": "pending",
                    }
                ],
                "results": [],
            }
        ),
        encoding="utf-8",
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "direct"},
            "steps": {
                "develop": {"on": {"await_agent": "pr"}},
                "pr": {"on": {"manual_handoff": "develop"}, "allowed_goto": ["develop"]},
            },
        },
        contract={
            "proactive_review": {"phase_decisions": [{"phase": "pr", "decision": "required"}]},
            "confirmation_contract": {
                "mandatory_human_stops": ["pr"],
                "driver_confirmable": [],
                "user_required": [],
            },
        },
        locale="zh-TW",
        issue_dir=issue_dir,
        driver_state={
            "proactive_review": {"pr": "completed"},
            "deliver": "unknown",
            "cleanup": "unknown",
        },
    )

    assert rendered == (
        "✓ develop · 第 6 輪 · 已完成\n│\n"
        "✓ pr · 第 14 輪 · 已完成\n│\n"
        "✓ pr：流程管理員主動審查 · 已完成\n│\n"
        "⏸\ufe0e pr：使用者確認（流程管理員不可代理） · 等待確認\n│\n"
        "？ deliver（收尾） · 狀態未知\n│\n"
        "？ cleanup（收尾） · 狀態未知"
    )
    assert "\ufe0f" not in rendered


def test_latest_return_is_phase_state_without_a_historical_arrow(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "pr",
            "events": [
                {
                    "event_type": "step_started",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": 14},
                },
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "pr",
                    "data": {"step": "pr", "source_identities": ["review:14"]},
                },
                {
                    "event_type": "transition",
                    "step": "pr",
                    "data": {
                        "from": "pr",
                        "to": "develop",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "direct"},
            "steps": {
                "develop": {"on": {"await_agent": "pr"}},
                "pr": {"on": {"manual_handoff": "develop"}, "allowed_goto": ["develop"]},
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "↩\ufe0e pr · iteration 14 · Returned" in rendered
    assert "→" not in rendered


def test_same_phase_task_return_projects_latest_confirmation_state(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    for iteration in (11, 12):
        iteration_dir = issue_dir / "pr" / f"iteration_{iteration:03d}"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "iteration.json").write_text(
            json.dumps({"iteration": iteration}), encoding="utf-8"
        )
    (issue_dir / "human_tasks.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-pr-11",
                        "step": "pr",
                        "iteration": 11,
                        "trigger": "confirm_output",
                        "status": "completed",
                        "expected_result": {"decisions": [{"id": "fix_now", "correction": True}]},
                        "continuations": {"fix_now": "pr"},
                    }
                ],
                "results": [
                    {
                        "task_id": "task-pr-11",
                        "payload": {"decision": "fix_now", "continuation": "pr"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "single-pr"},
            "steps": {"pr": {"on": {"await_agent": "_done"}}},
        },
        contract={
            "confirmation_contract": {
                "mandatory_human_stops": ["pr"],
                "driver_confirmable": [],
                "user_required": [],
            }
        },
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "↩\ufe0e pr: user confirmation (manager may not act) · Returned" in rendered
    assert "→" not in rendered


def test_cross_target_returns_project_only_each_phase_latest_state(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "A",
            "events": [
                {
                    "event_type": "step_started",
                    "step": "C",
                    "data": {"step": "C", "iteration": 1},
                },
                {
                    "event_type": "transition",
                    "step": "C",
                    "data": {
                        "from": "C",
                        "to": "B",
                        "status_code": "needs_changes",
                        "transition_intent": "manual_handoff",
                    },
                },
                {
                    "event_type": "step_started",
                    "step": "B",
                    "data": {"step": "B", "iteration": 2},
                },
                {
                    "event_type": "step_started",
                    "step": "C",
                    "data": {"step": "C", "iteration": 2},
                },
                {
                    "event_type": "transition",
                    "step": "C",
                    "data": {
                        "from": "C",
                        "to": "A",
                        "status_code": "needs_changes",
                        "transition_intent": "manual_handoff",
                    },
                },
                {
                    "event_type": "step_started",
                    "step": "A",
                    "data": {"step": "A", "iteration": 2},
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "cross-target"},
            "steps": {
                "A": {"on": {"await_agent": "B"}},
                "B": {"on": {"await_agent": "C"}},
                "C": {
                    "on": {"await_agent": "_done", "manual_handoff": "B"},
                    "allowed_goto": ["A", "B"],
                },
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "▶\ufe0e A · iteration 2 · In progress" in rendered
    assert "▶\ufe0e B · iteration 2 · In progress" in rendered
    assert "↩\ufe0e C · iteration 2 · Returned" in rendered
    assert "iteration 1" not in rendered
    assert "→" not in rendered


def test_runtime_and_task_return_history_is_suppressed(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    events: list[dict[str, object]] = []
    for source_iteration, target_iteration, timestamp in (
        (11, 6, "2026-09-21T10:00:00+02:00"),
        (13, 7, "2026-09-21T08:30:00-01:00"),
    ):
        events.extend(
            [
                {
                    "event_type": "workflow_feedback_delivery_prepared",
                    "step": "pr",
                    "data": {"step": "pr", "iteration": {"number": source_iteration}},
                },
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "pr",
                    "data": {"step": "pr", "source_identities": [f"review:{source_iteration}"]},
                },
                {
                    "timestamp": timestamp,
                    "event_type": "transition",
                    "step": "pr",
                    "data": {
                        "from": "pr",
                        "to": "develop",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
                {
                    "event_type": "step_started",
                    "step": "develop",
                    "data": {"step": "develop", "iteration": target_iteration},
                },
            ]
        )
    _write_runtime_state(issue_dir, {"current_step": "develop", "events": events})
    (issue_dir / "human_tasks.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-pr-12",
                        "step": "pr",
                        "iteration": 12,
                        "trigger": "confirm_output",
                        "status": "completed",
                        "expected_result": {"decisions": [{"id": "fix_now", "correction": True}]},
                        "continuations": {"fix_now": "develop"},
                    }
                ],
                "results": [
                    {
                        "task_id": "task-pr-12",
                        "completed_at": "2026-09-21T09:00:00+00:00",
                        "payload": {"decision": "fix_now", "continuation": "develop"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "interleaved"},
            "steps": {
                "develop": {"on": {"await_agent": "pr"}},
                "pr": {
                    "on": {"manual_handoff": "develop"},
                    "allowed_goto": ["develop"],
                },
            },
        },
        contract={
            "confirmation_contract": {
                "mandatory_human_stops": ["pr"],
                "driver_confirmable": [],
                "user_required": [],
            }
        },
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "▶\ufe0e develop · iteration 7 · In progress" in rendered
    assert "↩\ufe0e pr · iteration 13 · Returned" in rendered
    assert "○ pr: user confirmation (manager may not act) · Pending" in rendered
    assert "iteration 11" not in rendered
    assert "iteration 12" not in rendered
    assert "→" not in rendered


def test_forward_feedback_curation_delivery_is_not_a_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "consumer",
            "events": [
                {
                    "event_type": "workflow_feedback_delivered",
                    "step": "curator",
                    "data": {
                        "step": "curator",
                        "source_identities": ["external_note:1"],
                    },
                },
                {
                    "event_type": "transition",
                    "step": "curator",
                    "data": {
                        "from": "curator",
                        "to": "consumer",
                        "source": "baton",
                        "status_code": "BATON_MANUAL_HANDOFF",
                        "transition_intent": "manual_handoff",
                    },
                },
            ],
        },
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "feedback-curation"},
            "steps": {
                "curator": {
                    "behavior": {
                        "feedback_target": "curator",
                        "feedback_artifact": "workflow_feedback",
                        "feedback_source_kind": "external_note",
                        "feedback_todo_source": "review_note",
                        "feedback_todo_id_prefix": "REV",
                    },
                    "on": {"manual_handoff": "consumer"},
                    "allowed_goto": ["consumer"],
                },
                "consumer": {"on": {"await_agent": "_done"}},
            },
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    source_line = next(line for line in rendered.splitlines() if " curator · " in line)
    assert "↩" not in source_line
    assert "Returned" not in source_line


def test_durable_blocked_event_overrides_completed_iteration_metadata(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    _write_runtime_state(
        issue_dir,
        {
            "current_step": "publish",
            "events": [
                {
                    "event_type": "step_completed",
                    "step": "publish",
                    "data": {"step": "publish", "attempt": 1},
                },
                {
                    "event_type": "workflow_blocked",
                    "step": "publish",
                    "data": {"step": "publish", "missing_capabilities": ["demo.publish"]},
                },
            ],
        },
    )
    iteration = issue_dir / "publish" / "iteration_001"
    iteration.mkdir(parents=True)
    (iteration / "iteration.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "status_code": "confirmed",
                "end_time": "2026-09-20T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    rendered = _module().render_progress(
        playbook={
            "playbook": {"id": "blocked-publication"},
            "steps": {"publish": {"on": {"await_agent": "_done"}}},
        },
        contract={},
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert "! publish · Blocked" in rendered
    assert "✓ publish · Completed" not in rendered


@pytest.mark.parametrize(
    ("pause_code", "answered", "expected"),
    [
        ("BATON_NEED_CLARIFICATION", False, "⏸\ufe0e 資料盤點 · 等待回覆"),
        ("BATON_NEED_PERMISSION", False, "⏸\ufe0e 資料盤點 · 等待回覆"),
        ("BATON_NEED_CLARIFICATION", True, "✓ 資料盤點 · 已完成"),
    ],
)
def test_audit_pause_overrides_finished_draft_without_an_iteration_status_code(
    tmp_path: Path, pause_code: str, answered: bool, expected: str
) -> None:
    issue_dir = tmp_path / "issue"
    iteration = issue_dir / "資料盤點" / "iteration_001"
    iteration.mkdir(parents=True)
    events = [
        {"event_type": "step_started", "step": "資料盤點", "data": {"attempt": 1}},
        {"event_type": "step_completed", "step": "資料盤點", "data": {"attempt": 1}},
        {"event_type": "workflow_paused", "step": "資料盤點", "data": {"status_code": pause_code}},
    ]
    if answered:
        events.append({"event_type": "human_task_completed", "step": "資料盤點"})
    _write_runtime_state(issue_dir, {"current_step": "user", "events": events})
    (iteration / "iteration.json").write_text(
        json.dumps({"iteration": 1, "end_time": "2026-09-20T01:00:00+00:00"}),
        encoding="utf-8",
    )
    contract = _contract()
    contract["proactive_review"]["phase_decisions"].append(
        {"phase": "資料盤點", "decision": "required"}
    )
    before = {path: path.read_bytes() for path in issue_dir.rglob("*") if path.is_file()}

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=contract,
        locale="zh-TW",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert expected in rendered
    assert "○ publish-draft · 待執行" in rendered
    if not answered:
        assert "○ 資料盤點：流程管理員主動審查 · 待執行" in rendered
    assert "events" not in json.loads((issue_dir / "blackboard.json").read_text())
    assert before == {path: path.read_bytes() for path in issue_dir.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"end_time": "2026-09-20T01:00:00+00:00"}, "✓ 資料盤點 · Completed"),
        ({"timestamp": "2026-09-20T01:00:00+00:00"}, "▶\ufe0e 資料盤點 · In progress"),
    ],
)
def test_iteration_timestamps_work_without_status_codes_or_execution_events(
    tmp_path: Path, metadata: dict, expected: str
) -> None:
    issue_dir = tmp_path / "issue"
    iteration = issue_dir / "資料盤點" / "iteration_001"
    iteration.mkdir(parents=True)
    _write_runtime_state(issue_dir, {"current_step": "資料盤點"})
    (iteration / "iteration.json").write_text(json.dumps(metadata), encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
        driver_state=_unknown_closeout_state(),
    )

    assert expected in rendered
    assert "○ publish-draft · Pending" in rendered
