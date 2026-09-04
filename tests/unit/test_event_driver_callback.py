"""Skill-owned event driver binding and session persistence tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cafe.core.types import AgentCLI


def _callback_module():
    path = (
        Path(__file__).parents[2]
        / "src/cafe/data/skills/use-cafe-workflow/scripts/workflow_event_callback.py"
    )
    spec = importlib.util.spec_from_file_location("workflow_event_callback_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_host_session_binding(monkeypatch) -> None:
    """Keep the test process's Codex App thread out of ordinary callback tests."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)


def test_event_driver_config_is_per_issue_and_cannot_replace_session(tmp_path: Path) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="gpt-5.6-sol")

    config = (issue_dir / "driver" / "config.yaml").read_text(encoding="utf-8")
    assert "mode: event-driven" in config
    assert "model: gpt-5.6-sol" in config

    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id="workflow", cli=AgentCLI.CODEX, model="gpt-5.6-sol"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")
    store.commit()
    with pytest.raises(ValueError, match="cannot change"):
        callback.write_config(issue_dir, cli="codex", model="another-model")


def test_event_driver_session_rejects_a_different_workflow(tmp_path: Path) -> None:
    callback = _callback_module()
    driver_dir = tmp_path / "driver"
    writer = callback.EventDriverSessionStore(
        driver_dir, workflow_id="one", cli=AgentCLI.CODEX, model="exact"
    )
    writer.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")
    writer.commit()
    reader = callback.EventDriverSessionStore(
        driver_dir, workflow_id="two", cli=AgentCLI.CODEX, model="exact"
    )
    with pytest.raises(ValueError, match="another workflow"):
        reader.load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX)


def test_event_driver_stages_a_new_session_until_identity_is_verified(tmp_path: Path) -> None:
    callback = _callback_module()
    store = callback.EventDriverSessionStore(
        tmp_path / "driver", workflow_id="one", cli=AgentCLI.CODEX, model="exact"
    )

    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session")

    assert not store.path.exists()
    store.commit()
    assert store.load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX).session_id == "session"


def test_event_driver_refuses_callbacks_without_a_process_lock(tmp_path: Path) -> None:
    callback = _callback_module()
    callback.fcntl = None
    callback.msvcrt = None

    with pytest.raises(RuntimeError, match="cross-process file locking"):
        with callback._session_lock(tmp_path / "driver"):
            pass


def test_callback_acquires_then_exactly_resumes_its_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    continuations = []

    class FakeManager:
        def __init__(self, *, session_manager, **_kwargs):
            self.session_manager = session_manager
            self._last_cli = AgentCLI.CODEX
            self._last_reported_model = None  # Codex does not report this in standard JSONL.
            self._last_session_id = "session-1"

        def register_agent(self, _config):
            pass

        def execute(self, _name, _prompt, *, continuation, **_kwargs):
            continuations.append(continuation)
            self.session_manager.save_session(
                callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "session-1"
            )

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    event = {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"}
    callback.run_callback(event, repository_root=tmp_path)
    callback.run_callback(event, repository_root=tmp_path)

    assert continuations[0].is_exact is False
    assert continuations[1].is_exact is True
    assert continuations[1].session_id == "session-1"


def test_callback_queues_the_bound_codex_host_thread(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    assert callback._load_config(issue_dir / "driver")["host_session"] == {
        "kind": "codex",
        "thread_id": "visible-thread",
    }

    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    event = {"issue": "issue456", "workflow_id": workflow_id, "event_type": "human_task"}
    with patch.object(callback.subprocess, "run") as run:
        callback.run_callback(event, repository_root=tmp_path)

    command = run.call_args.args[0]
    assert command[:4] == ["codex", "queue", "--thread", "visible-thread"]
    assert command[command.index("--model") + 1] == "exact"
    assert command[command.index("--cd") + 1] == str(tmp_path)
    prompt = command[command.index("--message") + 1]
    assert "event-driven CAFE workflow driver" in prompt
    assert '"event_type": "human_task"' in prompt
    assert run.call_args.kwargs == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 30,
    }
    stored = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    ).load_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX)
    assert stored is not None
    assert stored.session_id == "visible-thread"


def test_bound_host_thread_queue_failure_never_creates_a_new_session(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.setenv("CODEX_THREAD_ID", "visible-thread")
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    failure = subprocess.CalledProcessError(1, ["codex", "queue"], stderr="not found")
    with patch.object(callback.subprocess, "run", side_effect=failure) as run:
        with pytest.raises(subprocess.CalledProcessError):
            callback.run_callback(
                {
                    "issue": "issue456",
                    "workflow_id": workflow_id,
                    "event_type": "human_task",
                },
                repository_root=tmp_path,
            )

    assert run.call_count == 1
    assert not (issue_dir / "driver" / "session.json").exists()


def test_callback_failure_sends_a_best_effort_slack_notice(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    failure = subprocess.CalledProcessError(1, ["codex", "queue"])

    def fail_callback(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(callback, "run_callback", fail_callback)
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(callback, "load_slack_webhook_url", lambda **_kwargs: "webhook")
    notices = []
    monkeypatch.setattr(
        callback,
        "post_slack_notification",
        lambda webhook, message, *, timeout_sec: notices.append(
            (webhook, message.to_slack_payload(), timeout_sec)
        ),
    )
    event = {
        "issue": "issue456",
        "workflow_id": "workflow",
        "event_type": "human_task",
        "step": "spec",
    }

    with pytest.raises(subprocess.CalledProcessError):
        callback.main(["--workflow-event", json.dumps(event)])

    assert notices == [
        (
            "webhook",
            {
                "text": "\n".join(
                    (
                        "CAFE event callback 執行失敗",
                        f"專案：{tmp_path.name}",
                        "對話：issue456",
                        "目前階段：需求規格",
                        "事件：human_task",
                        "錯誤：codex_queue_exit_1",
                        "工作流程狀態已保存，請回到 CAFE 的「issue456」工作項目查看。",
                    )
                )
            },
            4.0,
        )
    ]


def test_callback_failure_uses_canonical_repository_route_and_deduplicates(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    active_root = tmp_path / "linked-worktree"
    canonical_root = tmp_path / "main-repository"
    issue_dir = active_root / ".cafe" / "issues" / "issue456"
    issue_dir.mkdir(parents=True)
    canonical_root.mkdir()
    monkeypatch.setattr(
        callback,
        "resolve_human_task_notification_repository_root",
        lambda resolved_issue_dir: (
            canonical_root
            if resolved_issue_dir == issue_dir
            else pytest.fail("unexpected issue directory")
        ),
    )
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    routed_roots = []
    monkeypatch.setattr(
        callback,
        "load_slack_webhook_url",
        lambda *, repository_root: routed_roots.append(repository_root) or "webhook",
    )
    payloads = []
    monkeypatch.setattr(
        callback,
        "post_slack_notification",
        lambda _webhook, message, *, timeout_sec: payloads.append(
            (message.to_slack_payload(), timeout_sec)
        ),
    )
    event = {
        "issue": "issue456",
        "workflow_id": "workflow",
        "event_type": "human_task",
        "step": "spec",
        "task_id": "task-one",
    }
    error = subprocess.CalledProcessError(2, ["codex", "queue"])

    callback._notify_callback_failure(event, repository_root=active_root, error=error)
    callback._notify_callback_failure(event, repository_root=active_root, error=error)

    assert routed_roots == [canonical_root]
    assert len(payloads) == 1
    assert "專案：main-repository" in payloads[0][0]["text"]
    assert "錯誤：codex_queue_exit_2" in payloads[0][0]["text"]
    receipts = json.loads(
        (issue_dir / "driver" / callback.FAILURE_NOTIFICATIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert list(receipts["records"].values())[0]["outcome"] == "sent"


def test_callback_and_slack_failure_leave_a_durable_receipt(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    issue_dir.mkdir(parents=True)
    monkeypatch.setattr(
        callback,
        "load_human_task_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(callback, "load_slack_webhook_url", lambda **_kwargs: "webhook")

    def fail_slack(*_args, **_kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(callback, "post_slack_notification", fail_slack)
    event = {
        "issue": "issue456",
        "workflow_id": "workflow",
        "event_type": "human_task",
        "step": "spec",
    }

    with pytest.raises(TimeoutError, match="offline"):
        callback._notify_callback_failure(
            event,
            repository_root=tmp_path,
            error=subprocess.CalledProcessError(2, ["codex", "queue"]),
        )

    receipts = json.loads(
        (issue_dir / "driver" / callback.FAILURE_NOTIFICATIONS_FILENAME).read_text(encoding="utf-8")
    )
    receipt = list(receipts["records"].values())[0]
    assert receipt == {
        "error_code": "codex_queue_exit_2",
        "notification_code": "TimeoutError",
        "occurred_at": receipt["occurred_at"],
        "outcome": "failed",
    }


def test_slack_notice_failure_does_not_replace_the_callback_error(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)

    def fail_callback(*_args, **_kwargs):
        raise RuntimeError("callback failed")

    def fail_notification(*_args, **_kwargs):
        raise OSError("slack unavailable")

    monkeypatch.setattr(callback, "run_callback", fail_callback)
    monkeypatch.setattr(callback, "_notify_callback_failure", fail_notification)

    with pytest.raises(RuntimeError, match="callback failed"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "issue456",
                        "workflow_id": "workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )


def test_stale_callback_is_rejected_without_a_failure_notification(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    notifications = []

    def stale_callback(*_args, **_kwargs):
        raise callback.StaleWorkflowEventError("stale")

    monkeypatch.setattr(callback, "run_callback", stale_callback)
    monkeypatch.setattr(
        callback,
        "_notify_callback_failure",
        lambda *_args, **_kwargs: notifications.append("unexpected"),
    )

    with pytest.raises(callback.StaleWorkflowEventError, match="stale"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "issue456",
                        "workflow_id": "old-workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )

    assert notifications == []


def test_invalid_issue_is_rejected_without_writing_a_failure_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    callback = _callback_module()
    monkeypatch.chdir(tmp_path)
    escaped_driver = tmp_path / "outside" / "driver"

    with pytest.raises(callback.InvalidWorkflowEventError, match="invalid issue"):
        callback.main(
            [
                "--workflow-event",
                json.dumps(
                    {
                        "issue": "../../outside",
                        "workflow_id": "workflow",
                        "event_type": "human_task",
                    }
                ),
            ]
        )

    assert not escaped_driver.exists()


def test_callback_identity_mismatch_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing")
    store.commit()
    original = store.path.read_bytes()

    class FakeManager:
        def __init__(self, *, session_manager, **_kwargs):
            self.session_manager = session_manager
            self._last_cli = AgentCLI.CODEX
            self._last_reported_model = "wrong"
            self._last_session_id = "existing"

        def register_agent(self, _config):
            pass

        def execute(self, *_args, **_kwargs):
            self.session_manager.save_session(
                callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing"
            )

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    with pytest.raises(ValueError, match="identity mismatch"):
        callback.run_callback(
            {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"},
            repository_root=tmp_path,
        )
    assert store.path.read_bytes() == original


def test_callback_session_conflict_keeps_existing_session(tmp_path: Path, monkeypatch) -> None:
    callback = _callback_module()
    issue_dir = tmp_path / ".cafe" / "issues" / "issue456"
    callback.write_config(issue_dir, cli="codex", model="exact")
    from cafe.agents.executor import AgentExecutionError
    from cafe.core.blackboard import BlackboardStore

    workflow_id = BlackboardStore(issue_dir).load_or_create("spec").workflow_id
    store = callback.EventDriverSessionStore(
        issue_dir / "driver", workflow_id=workflow_id, cli=AgentCLI.CODEX, model="exact"
    )
    store.save_session(callback.DRIVER_AGENT_NAME, AgentCLI.CODEX, "existing")
    store.commit()
    original = store.path.read_bytes()

    class FakeManager:
        def __init__(self, **_kwargs):
            pass

        def register_agent(self, _config):
            pass

        def execute(self, *_args, **_kwargs):
            raise AgentExecutionError("conflict", error_type="SESSION_CONFLICT")

    monkeypatch.setattr(callback, "AgentManager", FakeManager)
    with pytest.raises(AgentExecutionError, match="conflict"):
        callback.run_callback(
            {"issue": "issue456", "workflow_id": workflow_id, "event_type": "phase_terminal"},
            repository_root=tmp_path,
        )
    assert store.path.read_bytes() == original
