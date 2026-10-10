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

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


VALID_WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/integration-secret"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _exercise_normal_notification_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These journeys use temporary credentials and a mocked Slack transport."""
    monkeypatch.delenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", raising=False)


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
    test_credential = home / ".slack-webhook-test"
    test_credential.write_text(value, encoding="utf-8")
    test_credential.chmod(0o600)
    return credential


def _write_local_publication_setting(issue_dir: Path) -> None:
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "issue.yaml").write_text("pr:\n  auto_create: false\n", encoding="utf-8")


def _pause_for_output_review(issue_dir: Path, *, response: str = "ready_for_review"):
    _write_local_publication_setting(issue_dir)
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
    _write_local_publication_setting(issue_dir)
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
    repo_root.mkdir()
    home = tmp_path / "home-success"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []

    def _open_slack_request(request, *, timeout: float):
        posts.append((request, timeout))
        return _SlackResponse()

    monkeypatch.setattr(notification_mod, "_open_slack_request", _open_slack_request)
    monkeypatch.chdir(repo_root)

    result = _pause_for_output_review(Path(".cafe") / "issues" / "success")

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
    assert issue_dir.name in payload["text"]
    assert task.workflow_id not in payload["text"]
    assert task.id not in payload["text"]
    assert task.policy_id not in payload["text"]
    assert "cafe task" not in payload["text"]
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
    assert issue_dir.name in payload["text"]
    assert task.id not in payload["text"]
    assert "What you need to do: Handle a CAFE work item" in payload["text"]


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


def test_project_playbook_named_standard_receives_machine_controlled_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List 1: project provenance does not suppress core task notification."""
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
    home = tmp_path / "home-project-standard"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )
    _write_local_publication_setting(issue_dir)

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
    assert len(posts) == 1
    assert state.capability_receipts[0]["task_id"] == task.id


def test_global_playbook_receives_machine_controlled_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List 1: global provenance does not suppress core task notification."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "global-standard"
    issue_dir = repo_root / ".cafe" / "issues" / "global-standard"
    global_root = tmp_path / "global"
    global_playbooks = global_root / "playbooks"
    global_playbooks.mkdir(parents=True)
    builtin = PlaybookLoader().load("standard")
    builtin["steps"]["spec"]["initial_input"].pop("legacy_presentation", None)
    (global_playbooks / "standard.yaml").write_text(
        yaml.safe_dump(dict(builtin), sort_keys=False),
        encoding="utf-8",
    )
    global_standard = PlaybookLoader(project_root=repo_root, global_root=global_root).load(
        "standard"
    )
    home = tmp_path / "home-global-standard"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )
    _write_local_publication_setting(issue_dir)

    BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=global_standard,
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
    assert len(posts) == 1
    assert state.capability_receipts[0]["task_id"] == task.id


def test_disabled_machine_notification_leaves_a_durable_nonblocking_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List 2: an explicitly disabled transport does not block the pending task."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "disabled-repository"
    issue_dir = repo_root / ".cafe" / "issues" / "disabled"
    home = tmp_path / "home-disabled"
    (home / ".cafe").mkdir(parents=True)
    (home / ".cafe" / "config.yaml").write_text(
        "notifications:\n  human_tasks:\n    enabled: false\n    transport: slack\n",
        encoding="utf-8",
    )
    _set_home(monkeypatch, home)
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled must not post")),
    )

    _pause_for_output_review(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt = state.capability_receipts[0]
    assert task.status is HumanTaskStatus.PENDING
    assert state.current_step == "user"
    assert receipt["code"] == "human_task_notification_disabled"
    assert receipt["outcome"] == "disabled"


def test_unsupported_machine_notification_leaves_a_durable_skipped_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test List 2: unsupported machine transport is inspectable without a post."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / "unsupported-repository"
    issue_dir = repo_root / ".cafe" / "issues" / "unsupported"
    home = tmp_path / "home-unsupported"
    (home / ".cafe").mkdir(parents=True)
    (home / ".cafe" / "config.yaml").write_text(
        "notifications:\n  human_tasks:\n    enabled: true\n    transport: email\n",
        encoding="utf-8",
    )
    _set_home(monkeypatch, home)
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("skipped must not post")),
    )

    _pause_for_output_review(issue_dir)

    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    state = BlackboardStore(issue_dir).load_or_create("spec")
    receipt = state.capability_receipts[0]
    assert task.status is HumanTaskStatus.PENDING
    assert state.current_step == "user"
    assert receipt["code"] == "human_task_notification_transport_unsupported"
    assert receipt["outcome"] == "skipped"


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


def _notify_with_workflow_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    locale: str | None,
) -> str:
    """Run the production pause-and-notify path for one stored workflow locale."""
    import cafe.core.human_task_notifications as notification_mod

    repo_root = tmp_path / name
    repo_root.mkdir()
    home = tmp_path / f"home-{name}"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts: list = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append(request) or _SlackResponse(),
    )
    monkeypatch.chdir(repo_root)

    relative_issue_dir = Path(".cafe") / "issues" / name
    _write_local_publication_setting(relative_issue_dir)
    store = BlackboardStore(relative_issue_dir)
    state = store.load_or_create("spec")
    state.conversation_locale = locale
    state.conversation_locale_source = "explicit" if locale else None
    store.save(state)

    _pause_for_output_review(relative_issue_dir)

    assert len(posts) == 1
    return json.loads(posts[0].data)["text"]


def test_the_stored_workflow_locale_reaches_the_delivered_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration 1/4: delivered text follows the workflow's stored language."""
    traditional_chinese = _notify_with_workflow_locale(
        tmp_path, monkeypatch, name="zh-workflow", locale="zh-TW"
    )
    english = _notify_with_workflow_locale(
        tmp_path, monkeypatch, name="en-workflow", locale="en-US"
    )

    assert "確認結果" in traditional_chinese
    assert "確認結果" not in english
    assert english.isascii()


def test_an_unsupported_or_absent_locale_delivers_english_without_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration 4/6: the fallback is silent and a legacy record keeps no locale."""
    unsupported = _notify_with_workflow_locale(
        tmp_path, monkeypatch, name="ja-workflow", locale="ja-JP"
    )
    legacy = _notify_with_workflow_locale(tmp_path, monkeypatch, name="legacy", locale=None)

    assert unsupported.isascii()
    assert legacy.isascii()
    assert "ja-JP" not in unsupported
    legacy_state = json.loads(
        (tmp_path / "legacy" / ".cafe" / "issues" / "legacy" / "blackboard.json").read_text(
            encoding="utf-8"
        )
    )
    assert legacy_state.get("conversation_locale") is None


def test_a_background_callback_failure_notification_uses_the_stored_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration 3: the background path reads the stored value, never re-resolves."""
    import importlib.util

    import cafe.core.human_task_notifications as notification_mod

    callback_path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("workflow_event_callback_locale", callback_path)
    workflow_event_callback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow_event_callback)

    repo_root = tmp_path / "callback-repository"
    issue_dir = repo_root / ".cafe" / "issues" / "callback"
    issue_dir.mkdir(parents=True)
    home = tmp_path / "home-callback"
    home.mkdir()
    _write_credential(home)
    _set_home(monkeypatch, home)
    posts: list = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append(request) or _SlackResponse(),
    )
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    state.conversation_locale = "zh-TW"
    state.conversation_locale_source = "explicit"
    store.save(state)

    workflow_event_callback._notify_callback_failure(
        {"issue": "callback", "step": "develop", "event_type": "phase_terminal"},
        repository_root=repo_root,
        error=ValueError("state unreadable"),
    )

    assert len(posts) == 1
    assert "無法讀取自動通知所需的狀態或設定" in json.loads(posts[0].data)["text"]


def _named_store(home: Path) -> tuple[Path, str]:
    path = home / ".cafe/credentials.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    routed = "https://hooks.slack.com/services/T/B/named-integration-secret"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "slack": {
                    "destinations": {
                        "default": {"webhook_url": VALID_WEBHOOK},
                        "operations": {"webhook_url": routed},
                    }
                },
            }
        )
    )
    path.chmod(0o600)
    return path, routed


def _callback_notification_module():
    import importlib.util

    path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("named_destination_callback", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("v1", [False, True])
@pytest.mark.parametrize("route", [False, True])
@pytest.mark.parametrize("worktree", [False, True])
def test_human_task_and_callback_failure_share_destination_and_keep_deduplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, v1: bool, route: bool, worktree: bool
) -> None:
    """Real callers use the same default/project route, including linked worktrees."""
    import subprocess

    import cafe.core.human_task_notifications as notification_mod
    from cafe.core.workflow_runtime import HumanTaskNotificationDispatcher

    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home)
    _, named = _named_store(home)
    if not v1:
        (home / ".cafe/credentials.yaml").unlink()
    parent = tmp_path / "repository"
    parent.mkdir()
    active = parent
    if worktree:
        subprocess.run(["git", "init", "-q", str(parent)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(parent),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "Initial",
            ],
            check=True,
            capture_output=True,
        )
        active = parent / ".cafe/worktrees/issue490"
        subprocess.run(
            ["git", "-C", str(parent), "worktree", "add", "-q", "-b", "issue490", str(active)],
            check=True,
            capture_output=True,
        )
    if route:
        projects = {str(parent): {"destination": "operations"} if v1 else {"webhook_url": named}}
        if worktree:
            projects[str(active)] = {"destination": "missing"} if v1 else {"webhook_url": "invalid"}
        config = home / ".cafe/config.yaml"
        config.write_text(
            yaml.safe_dump({"notifications": {"human_tasks": {"projects": projects}}})
        )
        config.chmod(0o600)
    _set_home(monkeypatch, home)
    posts = []
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append((request, timeout)) or _SlackResponse(),
    )
    issue_dir = active / ".cafe/issues/named-route"
    _pause_for_output_review(issue_dir)
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("spec")
    HumanTaskNotificationDispatcher(
        issue_dir=issue_dir, blackboard_store=store, blackboard=state
    ).notify(task)
    callback = _callback_notification_module()
    event = {
        "issue": issue_dir.name,
        "workflow_id": task.workflow_id,
        "step": "spec",
        "event_type": "phase_terminal",
    }
    callback._notify_callback_failure(
        event, repository_root=active, error=ValueError("private callback detail")
    )
    callback._notify_callback_failure(
        event, repository_root=active, error=ValueError("private callback detail")
    )

    assert len(posts) == 2
    expected = named if route else VALID_WEBHOOK
    assert [request.full_url for request, _ in posts] == [expected, expected]
    assert [timeout for _, timeout in posts] == [5.0, 4.0]
    payloads = [json.loads(request.data)["text"] for request, _ in posts]
    assert all(parent.name in payload for payload in payloads)
    assert payloads[0] != payloads[1]
    assert task.status is HumanTaskStatus.PENDING
    records = (issue_dir / "driver/callback_failure_notifications.json").read_text()
    assert list(json.loads(records)["records"].values())[0]["outcome"] == "sent"
    persisted = (issue_dir / "blackboard.json").read_text() + records
    assert expected not in persisted
    assert "private callback detail" not in persisted
    assert state.capability_receipts[0]["success"] is True


@pytest.mark.parametrize(
    "source", ["store_yaml", "store_utf8", "config_yaml", "missing_destination"]
)
def test_failed_named_notifications_leave_secret_free_exceptions_logs_receipts_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, source: str
) -> None:
    import traceback

    import cafe.core.human_task_notifications as notification_mod

    marker = "recognizable-secret-marker"
    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home)
    credential, _ = _named_store(home)
    repo = tmp_path / "repository"
    issue_dir = repo / ".cafe/issues/named-failure"
    expected_code = "slack_credentials_invalid"
    if source == "store_yaml":
        credential.write_text(f"version: 1\nslack: [{marker}")
    elif source == "store_utf8":
        credential.write_bytes(marker.encode() + b"\xff")
    elif source == "config_yaml":
        config = home / ".cafe/config.yaml"
        config.write_text(f"notifications: [{marker}")
        expected_code = "human_task_notification_config_invalid"
    else:
        config = home / ".cafe/config.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "notifications": {
                        "human_tasks": {"projects": {str(repo): {"destination": "missing"}}}
                    }
                }
            )
        )
        config.chmod(0o600)
        expected_code = "slack_credentials_destination_missing"
    _set_home(monkeypatch, home)
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda *args, **kwargs: pytest.fail("invalid credentials must not post"),
    )
    _pause_for_output_review(issue_dir)
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert state.capability_receipts[0]["code"] == expected_code
    assert HumanTaskRecordStore(issue_dir).tasks()[0].status is HumanTaskStatus.PENDING
    callback = _callback_notification_module()
    event = {"issue": issue_dir.name, "step": "spec", "event_type": "phase_terminal"}
    # Config errors disable delivery before invoking the shared credential resolver.
    if source == "config_yaml":
        callback._notify_callback_failure(
            event, repository_root=repo, error=ValueError("callback failed")
        )
    else:
        with pytest.raises(notification_mod.SlackNotificationError) as caught:
            callback._notify_callback_failure(
                event, repository_root=repo, error=ValueError("callback failed")
            )
        assert caught.value.code == expected_code
        error = caught.value
        while error is not None:
            assert marker not in repr(error)
            error = error.__cause__ or error.__context__
        assert marker not in "".join(traceback.format_exception(caught.value))
    persisted = (issue_dir / "blackboard.json").read_text()
    records = (issue_dir / "driver/callback_failure_notifications.json").read_text()
    assert marker not in persisted + records + caplog.text
    assert VALID_WEBHOOK not in persisted + records + caplog.text
    record = list(json.loads(records)["records"].values())[0]
    assert record["outcome"] == ("disabled" if source == "config_yaml" else "failed")
    assert record["notification_code"] == (
        expected_code if source == "config_yaml" else "SlackNotificationError"
    )


@pytest.mark.parametrize("available", [False, True])
def test_test_run_human_task_and_callback_never_open_normal_credentials_or_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    import os

    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    _write_credential(home)
    credential, named = _named_store(home)
    credential.write_text("invalid production store")
    # Even an invalid project map is irrelevant to an isolated test notification.
    config = home / ".cafe/config.yaml"
    config.write_text("notifications:\n  human_tasks:\n    projects: false\n")
    config.chmod(0o600)
    if available:
        test_file = home / ".cafe/test-slack-webhook"
        test_file.write_text(named)
        test_file.chmod(0o600)
    _set_home(monkeypatch, home)
    monkeypatch.setattr(notification_mod, "_login_user_home", lambda: home)
    monkeypatch.setenv("CAFE_TEST_RUN_SLACK_NOTIFICATIONS", "1")
    posts, opened = [], []
    original = os.open

    def record_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(notification_mod.os, "open", record_open)
    monkeypatch.setattr(
        notification_mod,
        "_open_slack_request",
        lambda request, *, timeout: posts.append(request) or _SlackResponse(),
    )
    repo = tmp_path / "repository"
    issue_dir = repo / ".cafe/issues/test-isolation"
    _pause_for_output_review(issue_dir)
    state = BlackboardStore(issue_dir).load_or_create("spec")
    callback = _callback_notification_module()
    event = {"issue": issue_dir.name, "step": "spec", "event_type": "phase_terminal"}
    if available:
        callback._notify_callback_failure(event, repository_root=repo, error=ValueError("callback"))
        assert [post.full_url for post in posts] == [named, named]
        assert state.capability_receipts[0]["success"] is True
    else:
        with pytest.raises(notification_mod.SlackNotificationError) as caught:
            callback._notify_callback_failure(
                event, repository_root=repo, error=ValueError("callback")
            )
        assert caught.value.code == "slack_credentials_missing"
        assert state.capability_receipts[0]["code"] == "slack_credentials_missing"
        assert posts == []
    assert credential not in opened
    assert home / ".slack-webhook" not in opened


def test_disabled_notifications_require_neither_valid_store_nor_legacy_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    import cafe.core.human_task_notifications as notification_mod

    home = tmp_path / "home"
    home.mkdir()
    credential, _ = _named_store(home)
    credential.write_text("invalid store")
    (home / ".cafe/config.yaml").write_text("notifications:\n  human_tasks:\n    enabled: false\n")
    _set_home(monkeypatch, home)
    opened = []
    original = os.open

    def record_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(notification_mod.os, "open", record_open)
    repo = tmp_path / "repository"
    issue_dir = repo / ".cafe/issues/disabled-store"
    _pause_for_output_review(issue_dir)
    callback = _callback_notification_module()
    callback._notify_callback_failure(
        {"issue": issue_dir.name, "step": "spec", "event_type": "phase_terminal"},
        repository_root=repo,
        error=ValueError("callback"),
    )
    state = BlackboardStore(issue_dir).load_or_create("spec")
    assert state.capability_receipts[0]["code"] == "human_task_notification_disabled"
    assert credential not in opened
    assert home / ".slack-webhook" not in opened
