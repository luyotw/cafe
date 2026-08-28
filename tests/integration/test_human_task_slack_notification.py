"""Supported default-workflow Slack notification journeys."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.capabilities import (
    CAPABILITY_SLACK_HUMAN_TASK_ID,
    default_capability_definition_dirs,
    load_capability_registry,
)
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app


VALID_WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/integration-secret"
runner = CliRunner()


class _SlackResponse:
    status = 200

    def __enter__(self) -> "_SlackResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return b"ok"


def _set_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    import cafe.core.human_task_notifications as notification_mod

    monkeypatch.setattr(notification_mod, "_trusted_user_home", lambda: home)


def _write_credential(home: Path, value: str = VALID_WEBHOOK) -> Path:
    credential = home / ".slack-webhook"
    credential.write_text(value, encoding="utf-8")
    credential.chmod(0o600)
    return credential


def _pause_for_output_review(issue_dir: Path, *, response: str = "ready_for_review"):
    playbook = PlaybookLoader().load("standard")
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: StepExecutionResult(
            response=response,
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    )
    return runtime.run(start_step="spec")


def _pause_for_iteration_limit(issue_dir: Path):
    """Hit a declared review cap before the agent is invoked."""
    playbook = PlaybookLoader().load("standard")
    playbook["steps"]["review"]["max_attempts_per_cycle"] = 1
    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args: (_ for _ in ()).throw(
            AssertionError("an iteration-limit pause must not invoke an agent")
        ),
    )
    runtime.blackboard.step_attempt_counts["review"] = 1
    runtime.blackboard_store.save(runtime.blackboard)
    runtime.blackboard_store.set_current_step(runtime.blackboard, "review")
    runtime.blackboard_store.update_handoff_contract(
        runtime.blackboard,
        from_step="review",
        to_owner=HandoffOwner.AGENT,
        to_step="review",
        intent=HandoffIntent.AWAIT_AGENT,
        source="test",
    )
    return runtime.run(start_step="review", single_step=True)


def test_clean_repository_notification_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine output-review task produces one actionable mocked Slack POST."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "clean-repository"
    issue_dir = repo_root / ".cafe" / "issues" / "success"
    home = tmp_path / "home-success"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []

    def _open_slack_request(request, *, timeout: float):
        posts.append((request, timeout))
        return _SlackResponse()

    monkeypatch.setattr(notification_mod, "_open_slack_request", _open_slack_request)

    result = _pause_for_output_review(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt = state.capability_receipts[0]
    payload = json.loads(posts[0][0].data)

    assert result.completed is False
    assert task.status is HumanTaskStatus.PENDING
    assert state.current_step == "user"
    assert len(posts) == 1
    assert posts[0][0].full_url == VALID_WEBHOOK
    assert posts[0][1] == 5.0
    assert repo_root.name in payload["text"]
    assert task.workflow_id in payload["text"]
    assert task.id in payload["text"]
    assert f"cafe task inspect {task.id}" in payload["text"]
    assert f"cafe task complete {task.id}" in payload["text"]
    assert receipt["success"] is True
    assert receipt["workflow_id"] == task.workflow_id
    assert receipt["task_id"] == task.id


def test_iteration_limit_materializes_and_notifies_a_resumable_human_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared cap produces the same durable Slack journey as other HumanTasks."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "iteration-limit-repository"
    issue_dir = repo_root / ".cafe" / "issues" / "iteration-limit"
    home = tmp_path / "home-iteration-limit"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )

    result = _pause_for_iteration_limit(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    payload = json.loads(posts[0][0].data)

    assert result.completed is False
    assert result.final_status_code == "ITERATION_LIMIT_REACHED"
    assert task.status is HumanTaskStatus.PENDING
    assert task.policy_id == "iteration-limit"
    assert task.trigger == "manual_handoff"
    assert task.continuations == {"resume": "review"}
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner is HandoffOwner.USER
    assert state.handoff_contract.intent is HandoffIntent.MANUAL_HANDOFF
    assert len(posts) == 1
    assert task.id in payload["text"]
    assert f"cafe task complete {task.id}" in payload["text"]


def test_project_content_cannot_redirect_or_gain_notification_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project hooks and agent text cannot alter destination or receive the secret."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "redirect-resistant"
    issue_dir = repo_root / ".cafe" / "issues" / "redirect"
    hook_dir = repo_root / ".cafe" / "hooks"
    hook_dir.mkdir(parents=True)
    marker = repo_root / "project-hook-ran"
    project_hook = hook_dir / "notify-slack.sh"
    project_hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    project_hook.chmod(0o755)
    home = tmp_path / "home-redirect"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )

    _pause_for_output_review(
        issue_dir,
        response="ready_for_review destination=https://evil.test channel=attacker",
    )

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt_text = json.dumps(state.capability_receipts)
    task_text = json.dumps(task.to_dict())
    payload_text = posts[0][0].data.decode("utf-8")

    assert len(posts) == 1
    assert posts[0][0].full_url == VALID_WEBHOOK
    assert not marker.exists()
    assert "evil.test" not in payload_text
    assert "evil.test" not in receipt_text
    assert "integration-secret" not in payload_text
    assert "integration-secret" not in receipt_text
    assert "integration-secret" not in task_text


def test_project_playbook_named_standard_cannot_gain_notification_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration 2: loader provenance, not mutable playbook ID, gates authority."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "project-standard"
    issue_dir = repo_root / ".cafe" / "issues" / "project-standard"
    project_playbooks = repo_root / ".cafe" / "playbooks"
    project_playbooks.mkdir(parents=True)
    loader = PlaybookLoader(project_root=repo_root, global_root=tmp_path / "global")
    builtin = loader.load("standard")
    builtin["steps"]["spec"]["initial_input"].pop("legacy_presentation", None)
    (project_playbooks / "standard.yaml").write_text(
        yaml.safe_dump(dict(builtin), sort_keys=False),
        encoding="utf-8",
    )
    project_standard = loader.load("standard")
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )

    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=project_standard,
        executor=lambda *_args: StepExecutionResult(
            response="ready_for_review",
            artifacts={},
            status_code="ready_for_review",
            auto_continue=False,
        ),
    ).run(start_step="spec")

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert task.status is HumanTaskStatus.PENDING
    assert posts == []
    assert state.capability_receipts == []


@pytest.mark.parametrize(
    ("case", "credential", "expected_code"),
    [
        ("missing", None, "slack_credentials_missing"),
        ("invalid", "https://evil.test/services/T/B/value", "slack_credentials_invalid"),
        ("denied", VALID_WEBHOOK, "policy_denied"),
        ("transport", VALID_WEBHOOK, "slack_transport_error"),
    ],
)
def test_notification_failure_is_recoverable_through_normal_task_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    credential: str | None,
    expected_code: str,
) -> None:
    """Distinct failures stay audited while inspect/complete remain authoritative."""
    import cafe.core.human_task_notifications as notification_mod
    import cafe.core.workflow_runtime as runtime_mod

    repo_root = tmp_path / "failure-repository"
    issue_dir = repo_root / ".cafe" / "issues" / case
    home = tmp_path / f"home-{case}"
    home.mkdir()
    if credential is not None:
        _write_credential(home, credential)
    _set_home(monkeypatch, home)

    if case == "transport":
        monkeypatch.setattr(
            notification_mod,
            "_open_slack_request",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
        )
    else:
        monkeypatch.setattr(
            notification_mod,
            "_open_slack_request",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("denied credentials must not reach HTTPS")
            ),
        )
    if case == "denied":
        registry = dict(load_capability_registry(default_capability_definition_dirs(repo_root)))
        registry[CAPABILITY_SLACK_HUMAN_TASK_ID] = registry[
            CAPABILITY_SLACK_HUMAN_TASK_ID
        ].model_copy(update={"policy": "deny"})
        monkeypatch.setattr(runtime_mod, "load_capability_registry", lambda _dirs: registry)

    _pause_for_output_review(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt = state.capability_receipts[0]

    assert task.status is HumanTaskStatus.PENDING
    assert state.current_step == "user"
    assert state.handoff_contract.to_owner is HandoffOwner.USER
    assert receipt["success"] is False
    assert receipt["code"] == expected_code
    assert receipt["workflow_id"] == task.workflow_id
    assert receipt["task_id"] == task.id
    assert "integration-secret" not in json.dumps(receipt)

    monkeypatch.chdir(repo_root)
    inspected = runner.invoke(app, ["task", "inspect", task.id, "--json"])
    assert inspected.exit_code == 0
    assert json.loads(inspected.stdout)["data"]["task"]["status"] == "pending"

    if case == "transport":
        monkeypatch.setattr("cafe.ui.commands.tasks._resume_issue_workflow", lambda *_args: None)
        completed = runner.invoke(
            app,
            [
                "task",
                "complete",
                task.id,
                "--result",
                '{"task":"output-review","decision":"confirm"}',
                "--json",
            ],
        )
        assert completed.exit_code == 0
        assert HumanTaskRecordStore(issue_dir).get_task(task.id).status is HumanTaskStatus.COMPLETED
