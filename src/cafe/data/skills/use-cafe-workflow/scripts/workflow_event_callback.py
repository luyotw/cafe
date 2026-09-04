#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow drivers."""

from __future__ import annotations

import argparse
import importlib.util
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
from cafe.core.session import SessionStore
from cafe.core.session_continuation import SessionContinuation
from cafe.core.types import AgentCLI, AgentConfig, SessionData

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
_HOST_SESSION_KIND = "codex"


def _proactive_review_module():
    """Load the sibling skill helper when this script is imported directly in tests."""
    path = Path(__file__).with_name("proactive_review.py")
    spec = importlib.util.spec_from_file_location("cafe_proactive_review", path)
    if spec is None or spec.loader is None:  # pragma: no cover - static bundled path.
        raise RuntimeError("proactive review helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proactive_review_reconciliation(issue_name: Any, *, repository_root: Path) -> str | None:
    """Render bounded live-contract context without creating a worker gate."""
    if not isinstance(issue_name, str) or not issue_name or Path(issue_name).name != issue_name:
        return None
    module = _proactive_review_module()
    issue_dir = repository_root / ".cafe" / "issues" / issue_name
    try:
        obligations = module.review_obligations(issue_dir=issue_dir, project_root=repository_root)
    except module.ContractNotFoundError:
        return None
    except (module.StaleContractError, module.ReviewStateError):
        return (
            "The proactive review contract is stale or incomplete. Preserve its current "
            "state as reconfirmation-required; do not treat it as empty or clean. Present "
            "a complete proposal and obtain user reconfirmation through the existing path."
        )
    if not obligations:
        return "The confirmed proactive review policy selects no phases. Do not infer a reviewer."
    summary = "; ".join(
        f"{item['phase']}={item['status']} ({item['ordering']}, "
        f"{item['reviewer']['cli']}:{item['reviewer']['model']})"
        for item in obligations
    )
    return (
        "Reconcile these current proactive review obligations from durable state: "
        f"{summary}. They are quality evidence only: do not use a clean review to answer "
        "a HumanTask, confirmation, permission, capability, scope, or authority decision."
    )


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
    lines = [
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
    ]
    reconciliation = _proactive_review_reconciliation(
        event.get("issue"), repository_root=repository_root
    )
    if reconciliation is not None:
        lines.append(reconciliation)
    return "\n".join(lines)


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
    issue_name = event.get("issue")
    workflow_id = event.get("workflow_id")
    if not isinstance(issue_name, str) or not issue_name or Path(issue_name).name != issue_name:
        raise ValueError("workflow event callback has an invalid issue")
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
            raise ValueError("workflow event callback is stale")
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
    run_callback(raw_event, repository_root=Path.cwd().resolve())
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint.
    raise SystemExit(main())
