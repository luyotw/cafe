"""User journeys across capability hook, durable task, CLI, and resume boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cafe.core.capabilities as capability_module
from cafe.core.blackboard import BlackboardStore
from cafe.core.capabilities import CapabilityManifest
from cafe.core.hooks.native import GitHubPRCreator
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.status_codes import PhaseStatusCode
from cafe.ui.cli import app

runner = CliRunner()


def _manifest() -> CapabilityManifest:
    return CapabilityManifest.model_validate(
        {
            "id": "demo.mutate",
            "version": 1,
            "implementation": "open_current_pr",
            "arguments": {
                "required": ["target_ref"],
                "properties": {"target_ref": {"type": "string", "enum": ["current_pr"]}},
            },
            "outputs": {"required": [], "properties": {}},
            "effects": {
                "writes": ["artifact.json"],
                "network_destinations": ["api.example.test"],
                "browser_open": [],
            },
            "credentials": ["example-token"],
            "permissions": {"network": ["api.example.test"]},
            "idempotency": "unsafe",
            "risk": "high",
            "approval": "required",
            "policy": "allow",
        }
    )


def _request() -> dict[str, object]:
    return {
        "capability": "demo.mutate",
        "args": {"target_ref": "current_pr"},
        "effects": {
            "writes": ["artifact.json"],
            "network_destinations": ["api.example.test"],
            "browser_open": [],
        },
        "credentials": ["example-token"],
        "permissions": {"network": ["api.example.test"]},
    }


def test_approve_restart_and_duplicate_resume_execute_exact_request_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 1/5/6/7: exact CLI approval survives restart and runs once."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "approval"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: default\n", encoding="utf-8")
    output_file = iteration_dir / "output.md"
    output_file.write_text("# Output\n", encoding="utf-8")
    request_file = iteration_dir / "capability_request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    blackboards = BlackboardStore(issue_dir)
    state = blackboards.load_or_create("develop", playbook_id="default")
    phase = SimpleNamespace(
        issue_dir=issue_dir,
        iteration=1,
        git_ops=SimpleNamespace(get_repo_root=lambda: tmp_path),
    )
    phase._get_issue_config_value = lambda *_args: True
    manifest = _manifest()
    calls: list[str] = []
    monkeypatch.setattr(
        "cafe.core.capabilities.load_capability_registry",
        lambda _paths: {manifest.id: manifest},
    )

    def adapter(**_kwargs: object) -> tuple[dict[str, object], None]:
        calls.append("mutated")
        return {}, None

    monkeypatch.setattr(capability_module, "HOST_CAPABILITY_ADAPTERS", {"open_current_pr": adapter})
    hook = GitHubPRCreator()
    kwargs = {
        "stage": "publish_output",
        "phase": phase,
        "step_name": "develop",
        "step_def": {"capability_requests": [manifest.id]},
        "output_file": output_file,
        "capability_request_file": request_file,
        "blackboard_state": state,
        "status_code": PhaseStatusCode.CONFIRMED,
    }

    pending = hook.run(**kwargs)
    task_id = str(pending.events[0]["task_id"])
    task = HumanTaskRecordStore(issue_dir).get_task(task_id)
    fingerprint = task.capability_approval["fingerprint"]  # type: ignore[index]
    monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)

    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task_id,
            "--result",
            json.dumps(
                {
                    "decision": "approve",
                    "workflow_id": state.workflow_id,
                    "task_id": task_id,
                    "request_fingerprint": fingerprint,
                }
            ),
            "--json",
        ],
    )
    resumed = hook.run(**kwargs)
    duplicate = hook.run(**kwargs)

    assert completed.exit_code == 0
    assert calls == ["mutated"]
    assert resumed.events[-1]["success"] is True
    assert duplicate.events[-1]["success"] is True
    detail = runner.invoke(app, ["task", "inspect", task_id, "--json"])
    inspected = json.loads(detail.stdout)["data"]["task"]["capability_approval"]
    assert inspected["state"] == "succeeded"
    assert inspected["attempt"]["state"] == "finished"


def test_malformed_and_mismatched_cli_decisions_leave_request_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 3/6: generic and mismatched consent cannot release wait."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "blocked"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: default\n", encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("develop", playbook_id="default")
    from cafe.core.capabilities import ExecutionRequest
    from cafe.core.capability_approvals import CapabilityApprovalService

    service = CapabilityApprovalService(
        issue_dir=issue_dir,
        workflow_id=state.workflow_id,
        step="develop",
        iteration=1,
    )
    task = service.request_approval(
        request=ExecutionRequest.model_validate(_request()), manifest=_manifest()
    )

    affirmative = runner.invoke(app, ["task", "complete", task.id, "--result", '"yes"', "--json"])
    mismatched = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            json.dumps(
                {
                    "decision": "approve",
                    "workflow_id": state.workflow_id,
                    "task_id": "another-task",
                    "request_fingerprint": "wrong",
                }
            ),
            "--json",
        ],
    )

    assert affirmative.exit_code != 0
    assert mismatched.exit_code != 0
    assert service.inspect(task.id)["state"] == "pending"
    assert service.store.get_wait_state(task.id).released_at is None
