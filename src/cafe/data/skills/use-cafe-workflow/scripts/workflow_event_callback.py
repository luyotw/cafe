#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow drivers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.human_task_notifications import (
    build_workflow_callback_failure_message,
    load_human_task_notification_settings,
    load_slack_webhook_url,
    post_slack_notification,
)
from cafe.core.session import SessionStore
from cafe.core.session_continuation import SessionContinuation
from cafe.core.types import AgentCLI, AgentConfig, SessionData
from cafe.core.workflow_runtime import resolve_human_task_notification_repository_root

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is handled by the host.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable outside Windows.
    msvcrt = None  # type: ignore[assignment]


DRIVER_AGENT_NAME = "__cafe_event_driver__"
CONFIG_FILENAME = "config.yaml"
SESSION_FILENAME = "session.json"
LOCK_FILENAME = "session.lock"
FAILURE_NOTIFICATIONS_FILENAME = "callback_failure_notifications.json"
MAX_FAILURE_NOTIFICATIONS = 128
_HOST_SESSION_KIND = "codex"


class InvalidWorkflowEventError(ValueError):
    """The callback envelope cannot be safely associated with one issue."""


class StaleWorkflowEventError(ValueError):
    """The callback belongs to an earlier workflow and should be ignored."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _session_lock(driver_dir: Path) -> Iterator[None]:
    driver_dir.mkdir(parents=True, exist_ok=True)
    lock_path = driver_dir / LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no branch - platform-specific.
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            raise RuntimeError("event-driven callbacks require cross-process file locking")
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no branch - platform-specific.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _driver_dir(issue_dir: Path) -> Path:
    return issue_dir / "driver"


def _current_host_session_binding() -> dict[str, str] | None:
    """Return the current Codex App thread without retaining host controls.

    A thread ID is enough for ``codex queue``.  In particular, never
    persist ``CODEX_REMOTE_PAYLOAD``: it is a host transport bootstrap control,
    not an identity or a callback credential.
    """
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        return None
    return {"kind": _HOST_SESSION_KIND, "thread_id": thread_id}


def write_config(issue_dir: Path, *, cli: str, model: str) -> None:
    """Create the one skill-owned event-driven binding for an issue."""
    try:
        AgentCLI(cli)
    except ValueError as exc:
        raise ValueError("event-driven CLI is not supported") from exc
    if not model.strip():
        raise ValueError("event-driven model must be exact and non-empty")
    driver_dir = _driver_dir(issue_dir)
    with _session_lock(driver_dir):
        existing = _load_config(driver_dir)
        proposed = {"schema_version": 1, "mode": "event-driven", "cli": cli, "model": model}
        # When a user launches CAFE from the Codex App, wake that visible
        # conversation.  The callback still receives only an opaque thread ID;
        # provider transport controls are deliberately not inherited.
        if cli == AgentCLI.CODEX.value:
            host_session = _current_host_session_binding()
            if host_session is not None:
                proposed = {**proposed, "schema_version": 2, "host_session": host_session}
        if existing is not None and existing != proposed:
            session_path = driver_dir / SESSION_FILENAME
            if session_path.exists():
                raise ValueError("event-driven binding cannot change after session acquisition")
        _atomic_write(
            driver_dir / CONFIG_FILENAME,
            yaml.safe_dump(proposed, sort_keys=True).encode("utf-8"),
        )


def _load_config(driver_dir: Path) -> dict[str, Any] | None:
    path = driver_dir / CONFIG_FILENAME
    if not path.is_file() or path.is_symlink():
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("event-driven config is unreadable") from exc
    if not isinstance(loaded, dict):
        raise ValueError("event-driven config must be a mapping")
    expected = {"schema_version", "mode", "cli", "model"}
    schema_version = loaded.get("schema_version")
    valid_schema = (schema_version == 1 and set(loaded) == expected) or (
        schema_version == 2 and set(loaded) == expected | {"host_session"}
    )
    if not valid_schema or loaded.get("mode") != "event-driven":
        raise ValueError("event-driven config is invalid")
    cli = loaded.get("cli")
    model = loaded.get("model")
    if not isinstance(cli, str) or not isinstance(model, str) or not model.strip():
        raise ValueError("event-driven config is invalid")
    AgentCLI(cli)
    result: dict[str, Any] = {
        "schema_version": schema_version,
        "mode": "event-driven",
        "cli": cli,
        "model": model,
    }
    host_session = loaded.get("host_session")
    if host_session is not None:
        if (
            cli != AgentCLI.CODEX.value
            or not isinstance(host_session, dict)
            or set(host_session) != {"kind", "thread_id"}
            or host_session.get("kind") != _HOST_SESSION_KIND
            or not isinstance(host_session.get("thread_id"), str)
            or not host_session["thread_id"].strip()
        ):
            raise ValueError("event-driven host session binding is invalid")
        result["host_session"] = {
            "kind": _HOST_SESSION_KIND,
            "thread_id": host_session["thread_id"],
        }
    return result


class EventDriverSessionStore(SessionStore):
    """Persist exactly one callback target session below its issue skill state."""

    def __init__(self, driver_dir: Path, *, workflow_id: str, cli: AgentCLI, model: str) -> None:
        self.driver_dir = driver_dir
        self.workflow_id = workflow_id
        self.cli = cli
        self.model = model
        self._pending_session_id: str | None = None

    @property
    def path(self) -> Path:
        return self.driver_dir / SESSION_FILENAME

    def _load_raw(self) -> dict[str, Any] | None:
        if not self.path.is_file() or self.path.is_symlink():
            return None
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("event-driven session is unreadable") from exc
        if not isinstance(loaded, dict):
            raise ValueError("event-driven session is invalid")
        return loaded

    def load_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> Optional[SessionData]:
        if agent_name != DRIVER_AGENT_NAME or issue_name is not None or phase_name is not None:
            return None
        raw = self._load_raw()
        if raw is None:
            return None
        expected = {
            "schema_version",
            "workflow_id",
            "cli",
            "model",
            "session_id",
            "created_at",
            "last_used_at",
        }
        if set(raw) != expected:
            raise ValueError("event-driven session is invalid")
        if raw.get("schema_version") != 1 or raw.get("workflow_id") != self.workflow_id:
            raise ValueError("event-driven session belongs to another workflow")
        if raw.get("cli") != self.cli.value or raw.get("model") != self.model or cli != self.cli:
            raise ValueError("event-driven session identity does not match its config")
        session_id = raw.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("event-driven session is invalid")
        return SessionData(
            agent_name=DRIVER_AGENT_NAME,
            cli=cli,
            session_id=session_id,
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            last_used_at=datetime.fromisoformat(str(raw["last_used_at"])),
            phase_name=None,
        )

    def save_session(
        self,
        agent_name: str,
        cli: AgentCLI,
        session_id: str,
        issue_name: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> None:
        if (
            agent_name != DRIVER_AGENT_NAME
            or issue_name is not None
            or phase_name is not None
            or cli != self.cli
            or not session_id.strip()
        ):
            raise ValueError("event-driven session provenance is invalid")
        existing = self._load_raw()
        if existing is not None and existing.get("session_id") != session_id:
            raise ValueError("event-driven session identity cannot be replaced")
        self._pending_session_id = session_id

    def commit(self) -> None:
        """Persist a session only after the callback verifies reported identity."""
        existing = self._load_raw()
        session_id = self._pending_session_id or (
            existing.get("session_id") if existing is not None else None
        )
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("event-driven callback did not report a session ID")
        if existing is not None and existing.get("session_id") != session_id:
            raise ValueError("event-driven session identity cannot be replaced")
        now = _now()
        payload = {
            "schema_version": 1,
            "workflow_id": self.workflow_id,
            "cli": self.cli.value,
            "model": self.model,
            "session_id": session_id,
            "created_at": existing.get("created_at", now) if existing else now,
            "last_used_at": now,
        }
        _atomic_write(self.path, json.dumps(payload, sort_keys=True).encode("utf-8"))
        self._pending_session_id = None

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _callback_prompt(event: dict[str, Any], *, repository_root: Path) -> str:
    notice = json.dumps(event, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        (
            "You are the event-driven CAFE workflow driver.",
            "This is an asynchronous wake notification, not a workflow advancement gate.",
            "Read the builtin use-cafe-workflow skill and follow its current confirmed contract.",
            "First inspect current durable state with cafe status/show before acting; "
            "the event may be stale.",
            "Do not answer mandatory, user-required, clarification, permission, or "
            "capability tasks; only a user-facing driver turn may relay an explicit answer.",
            "You may complete a declared driver_confirmable task only after verifying its "
            "confirmed contract and evidence. Do not grant permissions/capabilities or wait "
            "for this callback.",
            "Do not assume you own a running background process. Only use an already "
            "reliable, authorized control path.",
            f"Repository: {repository_root}",
            f"Wake notice: {notice}",
        )
    )


def _queue_host_callback(
    prompt: str,
    *,
    thread_id: str,
    model: str,
    repository_root: Path,
) -> None:
    """Ask the Codex host daemon to wake its existing visible session."""
    subprocess.run(
        [
            "codex",
            "queue",
            "--thread",
            thread_id,
            "--message",
            prompt,
            "--model",
            model,
            "--cd",
            str(repository_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_callback(event: dict[str, Any], *, repository_root: Path) -> None:
    issue_name = _validated_issue_name(event)
    workflow_id = event.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("workflow event callback has an invalid workflow ID")
    issue_dir = repository_root / ".cafe" / "issues" / issue_name
    driver_dir = _driver_dir(issue_dir)
    with _session_lock(driver_dir):
        config = _load_config(driver_dir)
        if config is None:
            return
        from cafe.core.blackboard import BlackboardStore

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        if blackboard.workflow_id != workflow_id:
            raise StaleWorkflowEventError("workflow event callback is stale")
        cli = AgentCLI(config["cli"])
        store = EventDriverSessionStore(
            driver_dir,
            workflow_id=workflow_id,
            cli=cli,
            model=config["model"],
        )
        existing = store.load_session(DRIVER_AGENT_NAME, cli)
        host_session = config.get("host_session")
        host_thread_id = host_session["thread_id"] if isinstance(host_session, dict) else None
        if host_thread_id is not None:
            if existing is not None and existing.session_id != host_thread_id:
                raise ValueError("event-driven host session identity cannot be replaced")
            _queue_host_callback(
                _callback_prompt(event, repository_root=repository_root),
                thread_id=host_thread_id,
                model=config["model"],
                repository_root=repository_root,
            )
            if existing is None:
                store.save_session(DRIVER_AGENT_NAME, cli, host_thread_id)
            store.commit()
            return
        continuation = (
            SessionContinuation.resume_exact(cli, existing.session_id)
            if existing is not None
            else SessionContinuation.new()
        )
        manager = AgentManager(session_manager=store, issue_name=None, stream_agent_output=False)
        manager.register_agent(
            AgentConfig(
                name=DRIVER_AGENT_NAME, cli=cli, model=config["model"], clis=[], backup_clis=[]
            )
        )
        try:
            manager.execute(
                DRIVER_AGENT_NAME,
                _callback_prompt(event, repository_root=repository_root),
                allowed_tools=["Read", "Grep", "Glob", "Bash"],
                allowed_directories=[str(repository_root)],
                continuation=continuation,
            )
        except Exception:
            raise
        reported_model = manager._last_reported_model
        if (
            manager._last_cli != cli
            or (reported_model is not None and reported_model != config["model"])
            or not manager._last_session_id
            or (existing is not None and manager._last_session_id != existing.session_id)
            or (host_thread_id is not None and manager._last_session_id != host_thread_id)
        ):
            raise ValueError("event-driven driver identity mismatch")
        store.commit()


def _notify_callback_failure(
    event: dict[str, Any], *, repository_root: Path, error: Exception
) -> None:
    """Best-effort out-of-band notice when the primary callback path fails."""
    issue = _validated_issue_name(event)
    step = event.get("step")
    event_type = event.get("event_type")
    issue_dir = repository_root / ".cafe" / "issues" / issue
    driver_dir = _driver_dir(issue_dir)
    notification_root = resolve_human_task_notification_repository_root(issue_dir)
    error_code = _callback_error_code(error)
    notification_key = _callback_failure_key(event, error_code=error_code)
    with _session_lock(driver_dir):
        records = _load_callback_failure_notifications(driver_dir)
        existing = records.get(notification_key)
        if isinstance(existing, dict) and existing.get("outcome") in {"sent", "disabled"}:
            return
        settings = load_human_task_notification_settings()
        if not settings.enabled:
            records[notification_key] = {
                "occurred_at": _now(),
                "outcome": "disabled",
                "error_code": error_code,
                "notification_code": settings.code,
            }
            _write_callback_failure_notifications(driver_dir, records)
            return
        message = build_workflow_callback_failure_message(
            repository=notification_root.name,
            issue=issue,
            step=step if isinstance(step, str) else "",
            event_type=event_type if isinstance(event_type, str) else "",
            error_code=error_code,
        )
        try:
            webhook_url = load_slack_webhook_url(repository_root=notification_root)
            post_slack_notification(webhook_url, message, timeout_sec=4.0)
        except Exception as notification_error:
            records[notification_key] = {
                "occurred_at": _now(),
                "outcome": "failed",
                "error_code": error_code,
                "notification_code": type(notification_error).__name__,
            }
            _write_callback_failure_notifications(driver_dir, records)
            raise
        records[notification_key] = {
            "occurred_at": _now(),
            "outcome": "sent",
            "error_code": error_code,
            "notification_code": "slack_notification_sent",
        }
        _write_callback_failure_notifications(driver_dir, records)


def _callback_error_code(error: Exception) -> str:
    """Return an actionable, bounded code without exposing exception text."""
    if isinstance(error, FileNotFoundError):
        return "codex_queue_not_found"
    if isinstance(error, subprocess.TimeoutExpired):
        return "codex_queue_timeout"
    if isinstance(error, subprocess.CalledProcessError):
        return f"codex_queue_exit_{error.returncode}"
    return f"callback_{type(error).__name__}"


def _validated_issue_name(event: dict[str, Any]) -> str:
    """Return one safe issue directory name shared by execution and reporting."""
    issue = event.get("issue")
    if not isinstance(issue, str) or not issue or Path(issue).name != issue:
        raise InvalidWorkflowEventError("workflow event callback has an invalid issue")
    return issue


def _callback_failure_key(event: dict[str, Any], *, error_code: str) -> str:
    """Bind one notification to the durable event identity and stable failure."""
    bounded = {
        key: event.get(key)
        for key in ("workflow_id", "issue", "event_type", "step", "status_code", "task_id")
        if isinstance(event.get(key), (str, int))
    }
    bounded["error_code"] = error_code
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_callback_failure_notifications(driver_dir: Path) -> dict[str, dict[str, str]]:
    """Load bounded notification receipts; malformed state is replaced safely."""
    path = driver_dir / FAILURE_NOTIFICATIONS_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {}
    records = raw.get("records")
    if not isinstance(records, dict):
        return {}
    return {
        key: value
        for key, value in records.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _write_callback_failure_notifications(
    driver_dir: Path, records: dict[str, dict[str, str]]
) -> None:
    """Persist secret-free callback notification outcomes for diagnosis."""
    bounded_records = dict(list(records.items())[-MAX_FAILURE_NOTIFICATIONS:])
    payload = {"schema_version": 1, "records": bounded_records}
    _atomic_write(
        driver_dir / FAILURE_NOTIFICATIONS_FILENAME,
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-event")
    parser.add_argument("--write-config", action="store_true")
    parser.add_argument("--issue-dir")
    parser.add_argument("--cli")
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    if args.write_config:
        if not args.issue_dir or not args.cli or not args.model:
            parser.error("--write-config requires --issue-dir, --cli, and --model")
        write_config(Path(args.issue_dir), cli=args.cli, model=args.model)
        return 0
    if not args.workflow_event:
        parser.error("--workflow-event is required")
    raw_event = json.loads(args.workflow_event)
    if not isinstance(raw_event, dict):
        raise ValueError("workflow event callback must be an object")
    repository_root = Path.cwd().resolve()
    try:
        run_callback(raw_event, repository_root=repository_root)
    except (InvalidWorkflowEventError, StaleWorkflowEventError):
        raise
    except Exception as exc:
        try:
            _notify_callback_failure(raw_event, repository_root=repository_root, error=exc)
        except Exception:
            pass
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint.
    raise SystemExit(main())
