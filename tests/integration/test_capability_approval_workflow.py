"""User journeys across capability hook, durable task, CLI, and resume boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cafe.core.capabilities as capability_module
from cafe.core.blackboard import BlackboardStore
from cafe.core.capabilities import CapabilityManifest, CapabilityRegistryError, ExecutionRequest
from cafe.core.capability_approvals import CapabilityApprovalService
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
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    output_file = iteration_dir / "output.md"
    output_file.write_text("# Output\n", encoding="utf-8")
    request_file = iteration_dir / "capability_request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    blackboards = BlackboardStore(issue_dir)
    state = blackboards.load_or_create("develop", playbook_id="standard")
    phase = SimpleNamespace(
        issue_dir=issue_dir,
        iteration=1,
        git_ops=SimpleNamespace(get_repo_root=lambda: tmp_path),
    )
    phase._get_issue_config_value = lambda *_args: True
    manifest = _manifest()
    calls: list[str] = []
    notifications: list[dict[str, object]] = []
    monkeypatch.setattr(
        "cafe.core.capabilities.load_capability_registry",
        lambda _paths: {manifest.id: manifest},
    )

    def adapter(**_kwargs: object) -> tuple[dict[str, object], None]:
        calls.append("mutated")
        return {}, None

    monkeypatch.setattr(capability_module, "HOST_CAPABILITY_ADAPTERS", {"open_current_pr": adapter})
    import cafe.core.workflow_runtime as workflow_runtime_mod

    monkeypatch.setattr(
        workflow_runtime_mod,
        "run_capability_request",
        lambda **kwargs: notifications.append(kwargs["capability_request"])
        or SimpleNamespace(
            receipt={
                "capability": "cafe.slack.human_task",
                "success": True,
                "outcome": "success",
            }
        ),
    )
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
    correlation_id = task.capability_approval["correlation_id"]  # type: ignore[index]
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
                    "correlation_id": correlation_id,
                }
            ),
            "--json",
        ],
    )
    resumed = hook.run(**kwargs)
    duplicate = hook.run(**kwargs)

    assert completed.exit_code == 0
    assert notifications == [
        {
            "capability": "cafe.slack.human_task",
            "args": {
                "repository": tmp_path.name,
                "workflow_id": task.workflow_id,
                "task_id": task.id,
                "step": "develop",
                "task_type": "capability-approval",
            },
            "effects": {
                "writes": [],
                "network_destinations": ["hooks.slack.com"],
                "browser_open": [],
            },
            "credentials": ["slack_human_task_webhook"],
            "permissions": {"network": ["hooks.slack.com"]},
        }
    ]
    assert calls == ["mutated"]
    assert resumed.events[-1]["success"] is True
    assert duplicate.events[-1]["success"] is True
    assert resumed.events[-1]["correlation_id"] == correlation_id
    assert duplicate.events[-1]["correlation_id"] == correlation_id
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
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("develop", playbook_id="standard")
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


@pytest.mark.parametrize("outcome", ["deny", "cancel", "expire"])
def test_denial_cancellation_and_expiry_are_durable_without_mutation(
    tmp_path: Path, outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 2: terminal decisions release wait without mutation."""
    manifest = _manifest()
    service = CapabilityApprovalService(
        issue_dir=tmp_path / outcome,
        workflow_id="workflow-one",
        step="develop",
        iteration=1,
    )
    request = ExecutionRequest.model_validate(
        {
            **_request(),
            "expires_at": "2000-01-01T00:00:00+00:00" if outcome == "expire" else None,
        }
    )
    task = service.request_approval(request=request, manifest=manifest)

    if outcome == "deny":
        approval = service.inspect(task.id)
        state = service.record_decision(
            task.id,
            {
                "decision": "deny",
                "workflow_id": "workflow-one",
                "task_id": task.id,
                "request_fingerprint": approval["fingerprint"],
                "correlation_id": approval["correlation_id"],
            },
        )
    elif outcome == "cancel":
        state = service.cancel(task.id, reason="operator cancelled")
    else:
        state = service.inspect(task.id)

    monkeypatch.setattr(
        capability_module,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )
    receipt = service.resume(
        task.id,
        correlation_id=service.inspect(task.id)["correlation_id"],
        request=request,
        registry={manifest.id: manifest},
        repo_root=tmp_path,
        output_file=tmp_path / "output.md",
    )

    assert state["state"] in {"denied", "cancelled", "expired"}
    assert receipt["outcome"] == state["state"]
    assert not receipt["executed"]
    assert service.store.get_wait_state(task.id).released_at is not None


def test_hook_honors_request_declared_approval_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 2: the production request path terminalizes expiry."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "expired-hook"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    request_file = iteration_dir / "capability_request.json"
    request_file.write_text(
        json.dumps({**_request(), "expires_at": "2000-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    output_file = iteration_dir / "output.md"
    output_file.write_text("# Output\n", encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("develop", playbook_id="standard")
    phase = SimpleNamespace(
        issue_dir=issue_dir,
        iteration=1,
        git_ops=SimpleNamespace(get_repo_root=lambda: tmp_path),
    )
    phase._get_issue_config_value = lambda *_args: True
    manifest = _manifest()
    monkeypatch.setattr(
        "cafe.core.capabilities.load_capability_registry",
        lambda _paths: {manifest.id: manifest},
    )
    monkeypatch.setattr(
        capability_module,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )

    result = GitHubPRCreator().run(
        stage="publish_output",
        phase=phase,
        step_name="develop",
        step_def={"capability_requests": [manifest.id]},
        output_file=output_file,
        capability_request_file=request_file,
        blackboard_state=state,
        status_code=PhaseStatusCode.CONFIRMED,
    )

    assert result.events[-1]["code"] == "expired"
    task = HumanTaskRecordStore(issue_dir).tasks()[-1]
    assert task.capability_approval["expires_at"] == "2000-01-01T00:00:00+00:00"  # type: ignore[index]
    assert task.capability_approval["receipt"]["executed"] is False  # type: ignore[index]


def test_restrictive_policy_and_tampered_resume_remain_non_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 3/4: changed request or policy wins over approval."""
    manifest = _manifest()
    monkeypatch.setattr(
        capability_module,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )

    def approved_service(name: str) -> tuple[CapabilityApprovalService, str]:
        service = CapabilityApprovalService(
            issue_dir=tmp_path / name,
            workflow_id="workflow-one",
            step="develop",
            iteration=1,
        )
        task = service.request_approval(
            request=ExecutionRequest.model_validate(_request()), manifest=manifest
        )
        current = service.inspect(task.id)
        service.record_decision(
            task.id,
            {
                "decision": "approve",
                "workflow_id": "workflow-one",
                "task_id": task.id,
                "request_fingerprint": current["fingerprint"],
                "correlation_id": current["correlation_id"],
            },
        )
        return service, task.id

    policy_service, policy_task_id = approved_service("policy")
    restrictive = manifest.model_copy(update={"policy": "deny"})
    policy_receipt = policy_service.resume(
        policy_task_id,
        correlation_id=policy_service.inspect(policy_task_id)["correlation_id"],
        request=ExecutionRequest.model_validate(_request()),
        registry={manifest.id: restrictive},
        repo_root=tmp_path,
        output_file=tmp_path / "output.md",
    )
    tamper_service, tamper_task_id = approved_service("tamper")
    changed = {**_request(), "args": {"target_ref": "replacement"}}
    tamper_receipt = tamper_service.resume(
        tamper_task_id,
        correlation_id=tamper_service.inspect(tamper_task_id)["correlation_id"],
        request=ExecutionRequest.model_validate(changed),
        registry={manifest.id: manifest},
        repo_root=tmp_path,
        output_file=tmp_path / "output.md",
    )

    assert policy_receipt["outcome"] == "policy_rejected"
    assert tamper_receipt["outcome"] == "tampered"
    assert not policy_receipt["executed"]
    assert not tamper_receipt["executed"]


def test_hook_policy_load_failure_terminalizes_approved_exact_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List integration 4: unavailable current policy consumes the approval."""
    monkeypatch.chdir(tmp_path)
    issue_dir = tmp_path / ".cafe" / "issues" / "policy-unavailable"
    iteration_dir = issue_dir / "develop" / "iteration_001"
    iteration_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: standard\n", encoding="utf-8")
    request_file = iteration_dir / "capability_request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    output_file = iteration_dir / "output.md"
    output_file.write_text("# Output\n", encoding="utf-8")
    state = BlackboardStore(issue_dir).load_or_create("develop", playbook_id="standard")
    phase = SimpleNamespace(
        issue_dir=issue_dir,
        iteration=1,
        git_ops=SimpleNamespace(get_repo_root=lambda: tmp_path),
    )
    phase._get_issue_config_value = lambda *_args: True
    manifest = _manifest()
    registry: object = {manifest.id: manifest}
    monkeypatch.setattr("cafe.core.capabilities.load_capability_registry", lambda _paths: registry)
    monkeypatch.setattr(
        capability_module,
        "HOST_CAPABILITY_ADAPTERS",
        {"open_current_pr": lambda **_kwargs: (_ for _ in ()).throw(AssertionError())},
    )
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
    service = CapabilityApprovalService(
        issue_dir=issue_dir,
        workflow_id=state.workflow_id,
        step="develop",
        iteration=1,
    )
    approval = service.inspect(task_id)
    service.record_decision(
        task_id,
        {
            "decision": "approve",
            "workflow_id": state.workflow_id,
            "task_id": task_id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
        },
    )
    registry = CapabilityRegistryError("registry temporarily unreadable")

    def unavailable(_paths: object) -> object:
        assert isinstance(registry, CapabilityRegistryError)
        raise registry

    monkeypatch.setattr("cafe.core.capabilities.load_capability_registry", unavailable)
    rejected = hook.run(**kwargs)
    rejected_state = service.inspect(task_id)

    assert rejected.events[-1]["code"] == "policy_rejected"
    assert rejected.events[-1]["correlation_id"] == approval["correlation_id"]
    assert rejected_state["state"] == "policy_rejected"
    assert rejected_state["revalidation"]["reason_code"] == "registry_load_error"
    assert "registry temporarily unreadable" in rejected_state["revalidation"]["error_detail"]

    monkeypatch.setattr(
        "cafe.core.capabilities.load_capability_registry",
        lambda _paths: {manifest.id: manifest},
    )
    replacement = hook.run(**kwargs)

    assert replacement.events[0]["type"] == "capability_approval_pending"
    assert replacement.events[0]["task_id"] != task_id
