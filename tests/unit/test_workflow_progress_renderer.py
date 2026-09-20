"""Contract tests for the Driver-owned workflow progress renderer."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def _write_runtime(issue_dir: Path) -> None:
    issue_dir.mkdir(parents=True)
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
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
            ensure_ascii=False,
        ),
        encoding="utf-8",
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
        driver_state={"proactive_review": {"publish-draft": "in_progress"}},
        include_closeout=("deliver", "close"),
    )

    assert "○ 資料盤點" in rendered
    assert "○ publish-draft" in rendered
    assert "▶ publish-draft：driver 主動審查" in rendered
    assert "○ publish-draft：使用者確認（driver 不可代理）" in rendered
    assert "？ deliver（收尾）" in rendered
    assert "？ close（收尾）" in rendered
    assert "？ 狀態未知" in rendered
    assert "資料盤點" in rendered and "publish-draft" in rendered


def test_renderer_uses_current_iteration_and_revise_outcome_as_return_evidence(
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
        driver_state={"proactive_review": {"publish-draft": "completed"}},
    )

    assert "▶ 資料盤點 · 第 2 輪" in rendered
    assert "↩ publish-draft：使用者確認（driver 不可代理）" in rendered
    assert "↩ publish-draft → 資料盤點" in rendered
    assert "✓ publish-draft：使用者確認" not in rendered
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
            driver_state={"proactive_review": {"missing": "completed"}},
        )
    with pytest.raises(ValueError, match="invalid progress status"):
        module.render_progress(
            playbook=_custom_playbook(),
            contract=_contract(),
            locale="en",
            driver_state={"deliver": "done"},
            include_closeout=("deliver",),
        )


def test_closeout_visibility_is_explicit_and_custom_same_named_phase_is_distinct() -> None:
    playbook = _custom_playbook()
    playbook["steps"]["deliver"] = {"on": {"await_agent": "_done"}, "human_tasks": []}
    module = _module()

    hidden = module.render_progress(
        playbook=playbook,
        contract=_contract(),
        locale="en",
        driver_state={"deliver": "completed", "close": "pending"},
    )
    shown = module.render_progress(
        playbook=playbook,
        contract=_contract(),
        locale="en",
        driver_state={"deliver": "completed", "close": "pending"},
        include_closeout=("deliver", "close"),
    )

    assert "✓ deliver (closeout)" not in hidden
    assert "○ deliver" in hidden
    assert "○ deliver\n" in shown
    assert "✓ deliver (closeout)" in shown
    assert "○ close (closeout)" in shown


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
    blackboard["events"].extend(
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
    blackboard_path.write_text(json.dumps(blackboard, ensure_ascii=False), encoding="utf-8")
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
    )

    assert "! 資料盤點" in rendered
    assert "− publish-draft" in rendered
    assert "⏸ publish-draft: user confirmation (driver may not act)" in rendered
    assert "! Blocked" in rendered
    assert "− Skipped" in rendered


def test_direct_playbook_and_archived_issue_cli_are_supported(tmp_path: Path) -> None:
    archive = tmp_path / "archived" / "issue539"
    archive.mkdir(parents=True)
    (archive / "issue.yaml").write_text("playbook_id: direct-subagent-review\n", encoding="utf-8")
    (archive / "blackboard.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
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
            '{"deliver":"completed","close":"completed"}',
            "--show-deliver",
            "--show-close",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "✓ develop" in result.stdout
    assert "▶ pr" in result.stdout
    assert "✓ deliver (closeout)" in result.stdout
    assert "✓ close (closeout)" in result.stdout


def test_previous_revision_does_not_approve_the_new_iteration(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    _write_runtime(issue_dir)
    new_iteration = issue_dir / "publish-draft" / "iteration_002"
    new_iteration.mkdir(parents=True)
    (new_iteration / "iteration.json").write_text('{"iteration": 2}', encoding="utf-8")

    rendered = _module().render_progress(
        playbook=_custom_playbook(),
        contract=_contract(),
        locale="en",
        issue_dir=issue_dir,
    )

    assert "○ publish-draft: user confirmation (driver may not act)" in rendered
    assert "✓ publish-draft: user confirmation" not in rendered
    assert "↩ publish-draft → 資料盤點" in rendered


def test_cli_without_confirmed_contract_reports_unestablished_workflow(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps({"playbook_id": "direct-subagent-review", "events": []}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--issue-dir", str(issue_dir), "--locale", "zh-TW"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "流程尚未建立"


def test_retry_baton_and_non_topological_graph_do_not_invent_a_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
    )
    playbook = {
        "playbook": {"id": "non-topological"},
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
    )

    assert "↩ A → A" not in rendered
    assert "↩ C → B" not in rendered
    assert "└─ await_agent → C" in rendered
    assert "└─ await_agent → B" in rendered


def test_latest_iteration_metadata_cannot_be_overwritten_by_prior_completion(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "current_step": "review",
                "events": [
                    {
                        "event_type": "step_started",
                        "step": "review",
                        "data": {"step": "review", "attempt": 2},
                    }
                ],
            }
        ),
        encoding="utf-8",
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
    )

    assert "▶ review · iteration 2" in rendered
    assert "✓ review" not in rendered


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
    )

    assert "？ publish-draft: user confirmation (driver may not act)" in rendered
    assert "✓ publish-draft: user confirmation" not in rendered


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
    )

    assert "✓ publish-draft: user confirmation (driver may not act)" in rendered


def test_forward_skip_review_manual_handoff_is_not_a_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
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
    )

    assert "↩ develop → pr" not in rendered


def test_declared_correction_manual_handoff_is_a_formal_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
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
    )

    assert "↩ review → develop" in rendered


def test_delivered_pr_feedback_baton_is_a_formal_return(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "current_step": "develop",
                "events": [
                    {
                        "event_type": "step_started",
                        "step": "pr",
                        "data": {"step": "pr", "attempt": 2},
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
            }
        ),
        encoding="utf-8",
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
    )

    assert "↩ pr → develop" in rendered


def test_durable_blocked_event_overrides_completed_iteration_metadata(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
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
    )

    assert "! publish" in rendered
    assert "✓ publish" not in rendered
