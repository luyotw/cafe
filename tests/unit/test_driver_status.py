"""Durable workflow-driver status projection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cafe.core.blackboard import BlackboardStore
from cafe.services.summary_display import SummaryDisplay
from cafe.services.summary_service import SummaryService


def test_status_projects_policy_progress_and_decisions_without_session_identity(
    tmp_path: Path,
) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    root_config = issues_root / "issue432" / "issue.yaml"
    worktree = tmp_path / ".cafe" / "worktrees" / "issue432"
    active_dir = worktree / ".cafe" / "issues" / "issue432"
    active_config = active_dir / "issue.yaml"
    root_config.parent.mkdir(parents=True)
    active_dir.mkdir(parents=True)
    root_config.write_text(
        yaml.safe_dump({"issue_name": "issue432", "worktree_path": str(worktree)}),
        encoding="utf-8",
    )
    active_config.write_text(
        yaml.safe_dump(
            {
                "base_branch": "develop",
                "contract_version": 2,
                "driver": {
                    "mode": "delegated",
                    "cli": "codex",
                    "model": "gpt-5.6-codex",
                },
            }
        ),
        encoding="utf-8",
    )
    store = BlackboardStore(active_dir)
    state = store.load_or_create("develop")
    with store.driver_transaction(state) as persisted:
        persisted.driver_state = {
            "lifecycle": "paused",
            "pause_reason": "delegated_model_mismatch",
            "packets": {
                "1": {
                    "workflow_id": state.workflow_id,
                    "sequence": 1,
                    "completed_phase": "plan",
                    "requested_action": "develop",
                    "boundary_id": "plan:develop",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            },
            "decisions": {
                "1": {
                    "workflow_id": state.workflow_id,
                    "sequence": 1,
                    "requested_action": "develop",
                    "action": "pause",
                    "rationale": "review required",
                    "decided_at": "2026-01-01T00:00:01+00:00",
                }
            },
            "session": {
                "session_id": "secret-driver-session",
                "cli": "codex",
                "workflow_id": state.workflow_id,
            },
            "notification_guidance": {
                "proactive_events": [],
                "inspection_available": True,
                "inspection_command": "cafe status",
            },
            "model_mismatch": {
                "cli": "codex",
                "requested_model": "gpt-5.6-codex",
                "reported_model": "unexpected-model",
                "sequence": 1,
                "detected_at": "2026-01-01T00:00:02+00:00",
            },
        }

    status = SummaryService(issues_root=issues_root).load_driver_status("issue432")
    rendered = SummaryDisplay().format_driver_status(status)

    serialized = json.dumps(status)
    assert "secret-driver-session" not in serialized
    assert '"session"' not in serialized
    assert status["authority_path"] == str(active_config.resolve())
    assert status["policy"]["driver"]["mode"] == "delegated"
    assert status["progress"]["current_step"] == "develop"
    assert status["progress"]["requested_action"] == "develop"
    assert status["decisions"][0]["action"] == "pause"
    assert status["model_mismatch"]["reported_model"] == "unexpected-model"
    assert "execution" not in status["policy"]
    assert "fallback_reason" not in status
    assert "delegated" in rendered
    assert "gpt-5.6-codex" in rendered
    assert "unexpected-model" in rendered
    assert "Execution:" not in rendered
    assert "cafe status" in rendered
    assert "secret-driver-session" not in rendered


@pytest.mark.parametrize(
    "lifecycle",
    ["paused", "error", "permission", "human_task", "stopped", "complete"],
)
def test_status_projects_only_the_active_lifecycle_reason(tmp_path: Path, lifecycle: str) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    issue_dir = issues_root / "issue432"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(
            {
                "contract_version": 2,
                "driver": {"mode": "unattended"},
            }
        ),
        encoding="utf-8",
    )
    reason_key = "pause_reason" if lifecycle == "paused" else f"{lifecycle}_reason"
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("develop")
    with store.driver_transaction(state) as persisted:
        persisted.driver_state = {
            "lifecycle": lifecycle,
            reason_key: f"active {lifecycle}",
            ("error_reason" if lifecycle == "paused" else "pause_reason"): "stale",
            "session": {"session_id": "hidden-session"},
        }

    status = SummaryService(issues_root=issues_root).load_driver_status("issue432")
    rendered = SummaryDisplay().format_driver_status(status)

    assert status["reason"] == f"active {lifecycle}"
    assert "stale" not in json.dumps(status)
    assert "hidden-session" not in json.dumps(status)
    assert f"active {lifecycle}" in rendered


def test_status_projects_latest_worker_launch_before_blackboard_exists(tmp_path: Path) -> None:
    issues_root = tmp_path / ".cafe" / "issues"
    issue_dir = issues_root / "issue458"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump({"contract_version": 2, "driver": {"mode": "unattended"}}),
        encoding="utf-8",
    )
    (issue_dir / ".workflow-worker-launches.json").write_text(
        json.dumps(
            {
                "attempts": {
                    "worker-458": {
                        "worker_id": "worker-458",
                        "mode": "unattended",
                        "policy_digest": "not-exposed",
                        "status": "startup_failed",
                        "error_code": "worker_context_mismatch",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:01+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    status = SummaryService(issues_root=issues_root).load_driver_status("issue458")

    assert status["lifecycle"] == "not_started"
    assert status["worker"] == {
        "worker_id": "worker-458",
        "mode": "unattended",
        "status": "startup_failed",
        "error_code": "worker_context_mismatch",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:01+00:00",
    }
    assert "Worker: startup_failed reason=worker_context_mismatch" in SummaryDisplay().format_driver_status(
        status
    )
