#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow drivers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

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
DISPATCH_STATE_FILENAME = "dispatch_state.json"
LOCK_FILENAME = "session.lock"
_HOST_SESSION_KIND = "codex"


class _ExactSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_exact_mapping(
    loader: _ExactSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ExactSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_exact_mapping,
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


def _normalize_cli_entries(clis: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    if not clis:
        raise ValueError("event-driven clis must be non-empty")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_cli, raw_model in clis:
        if not isinstance(raw_cli, str) or not isinstance(raw_model, str) or not raw_model.strip():
            raise ValueError("event-driven CLI entries require exact cli and model values")
        try:
            cli = AgentCLI(raw_cli).value
        except ValueError as exc:
            raise ValueError("event-driven CLI is not supported") from exc
        if cli in seen:
            raise ValueError("event-driven clis must use distinct CLIs")
        seen.add(cli)
        normalized.append({"cli": cli, "model": raw_model})
    return normalized


def write_config(
    issue_dir: Path,
    *,
    cli: str | None = None,
    model: str | None = None,
    clis: Sequence[tuple[str, str]] | None = None,
) -> None:
    """Create the one skill-owned event-driven binding for an issue."""
    if clis is not None:
        if cli is not None or model is not None:
            raise ValueError("event-driven config cannot mix legacy and ordered forms")
        entries = _normalize_cli_entries(clis)
        proposed: dict[str, Any] = {
            "schema_version": 3,
            "mode": "event-driven",
            "clis": entries,
        }
        primary_cli = entries[0]["cli"]
    else:
        if not isinstance(cli, str) or not isinstance(model, str):
            raise ValueError("event-driven legacy config requires cli and model")
        try:
            AgentCLI(cli)
        except ValueError as exc:
            raise ValueError("event-driven CLI is not supported") from exc
        if not model.strip():
            raise ValueError("event-driven model must be exact and non-empty")
        proposed = {"schema_version": 1, "mode": "event-driven", "cli": cli, "model": model}
        primary_cli = cli
    driver_dir = _driver_dir(issue_dir)
    with _session_lock(driver_dir):
        existing = _load_config(driver_dir)
        # When a user launches CAFE from the Codex App, wake that visible
        # conversation.  The callback still receives only an opaque thread ID;
        # provider transport controls are deliberately not inherited.
        if primary_cli == AgentCLI.CODEX.value:
            host_session = _current_host_session_binding()
            if host_session is not None:
                proposed = {**proposed, "schema_version": 2, "host_session": host_session}
                if clis is not None:
                    proposed["schema_version"] = 3
        if existing is not None and existing != proposed:
            if (driver_dir / SESSION_FILENAME).exists() or (
                driver_dir / DISPATCH_STATE_FILENAME
            ).exists():
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
        loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_ExactSafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("event-driven config is unreadable") from exc
    if not isinstance(loaded, dict):
        raise ValueError("event-driven config must be a mapping")
    schema_version = loaded.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError("event-driven config is invalid")
    legacy_fields = {"schema_version", "mode", "cli", "model"}
    if loaded.get("mode") != "event-driven":
        raise ValueError("event-driven config is invalid")
    if schema_version == 3:
        if set(loaded) not in (
            {"schema_version", "mode", "clis"},
            {"schema_version", "mode", "clis", "host_session"},
        ):
            raise ValueError("event-driven config is invalid")
        raw_entries = loaded.get("clis")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("event-driven config clis are invalid")
        tuples: list[tuple[str, str]] = []
        for entry in raw_entries:
            if not isinstance(entry, dict) or set(entry) != {"cli", "model"}:
                raise ValueError("event-driven config clis are invalid")
            raw_cli, raw_model = entry.get("cli"), entry.get("model")
            if not isinstance(raw_cli, str) or not isinstance(raw_model, str):
                raise ValueError("event-driven config clis are invalid")
            tuples.append((raw_cli, raw_model))
        entries = _normalize_cli_entries(tuples)
        result = {"schema_version": 3, "mode": "event-driven", "clis": entries}
        primary_cli = entries[0]["cli"]
    else:
        valid_schema = (schema_version == 1 and set(loaded) == legacy_fields) or (
            schema_version == 2 and set(loaded) == legacy_fields | {"host_session"}
        )
        if not valid_schema:
            raise ValueError("event-driven config is invalid")
        cli = loaded.get("cli")
        model = loaded.get("model")
        if not isinstance(cli, str) or not isinstance(model, str) or not model.strip():
            raise ValueError("event-driven config is invalid")
        AgentCLI(cli)
        result = {
            "schema_version": schema_version,
            "mode": "event-driven",
            "cli": cli,
            "model": model,
        }
        primary_cli = cli
    host_session = loaded.get("host_session")
    if "host_session" in loaded and host_session is not None:
        if (
            primary_cli != AgentCLI.CODEX.value
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
    elif schema_version == 3 and "host_session" in loaded:
        raise ValueError("event-driven host session binding is invalid")
    return result


def _load_or_initialize_dispatch_state(
    driver_dir: Path,
    *,
    workflow_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Load the one authoritative version 3 state and enforce immutable policy."""
    if config.get("schema_version") != 3:
        raise ValueError("event-driven dispatch state requires schema version 3")
    path = driver_dir / DISPATCH_STATE_FILENAME
    if path.is_file() and not path.is_symlink():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("event-driven dispatch state is unreadable") from exc
        if not isinstance(state, dict) or set(state) != {
            "schema_version",
            "workflow_id",
            "policy",
            "active_index",
            "entries",
            "events",
            "updated_at",
        }:
            raise ValueError("event-driven dispatch state is invalid")
        if state.get("schema_version") != 1 or state.get("workflow_id") != workflow_id:
            raise ValueError("event-driven dispatch state belongs to another workflow")
        if state.get("policy") != config:
            raise ValueError("event-driven dispatch policy cannot change within a workflow")
        entries = state.get("entries")
        if not isinstance(entries, list) or len(entries) != len(config["clis"]):
            raise ValueError("event-driven dispatch state is invalid")
        for index, (entry, policy_entry) in enumerate(zip(entries, config["clis"])):
            if not isinstance(entry, dict) or set(entry) != {"index", "cli", "model", "session"}:
                raise ValueError("event-driven dispatch state is invalid")
            if (
                entry.get("index") != index
                or entry.get("cli") != policy_entry["cli"]
                or entry.get("model") != policy_entry["model"]
            ):
                raise ValueError("event-driven dispatch session provenance is invalid")
        active_index = state.get("active_index")
        if not isinstance(active_index, int) or not 0 <= active_index < len(entries):
            raise ValueError("event-driven dispatch state is invalid")
        if not isinstance(state.get("events"), dict):
            raise ValueError("event-driven dispatch state is invalid")
        return state
    if path.exists():
        raise ValueError("event-driven dispatch state is invalid")

    now = _now()
    host_session = config.get("host_session")
    entries = []
    for index, policy_entry in enumerate(config["clis"]):
        session = None
        if index == 0 and isinstance(host_session, dict):
            session = {
                "id": host_session["thread_id"],
                "source": "host_session",
                "acquired_at": now,
            }
        entries.append({"index": index, **policy_entry, "session": session})
    state = {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "policy": config,
        "active_index": 0,
        "entries": entries,
        "events": {},
        "updated_at": now,
    }
    _atomic_write(path, json.dumps(state, sort_keys=True).encode("utf-8"))
    return state


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
        if config["schema_version"] == 3:
            _load_or_initialize_dispatch_state(
                driver_dir,
                workflow_id=workflow_id,
                config=config,
            )
            return
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
    parser.add_argument("--entry", action="append", default=[])
    args = parser.parse_args(argv)
    if args.write_config:
        if not args.issue_dir:
            parser.error("--write-config requires --issue-dir")
        if args.entry:
            if args.cli is not None or args.model is not None:
                parser.error("--entry cannot be combined with --cli or --model")
            entries: list[tuple[str, str]] = []
            for value in args.entry:
                cli, separator, model = value.partition(":")
                if not separator or not cli or not model:
                    parser.error("--entry requires CLI:MODEL")
                entries.append((cli, model))
            write_config(Path(args.issue_dir), clis=entries)
        else:
            if not args.cli or not args.model:
                parser.error("--write-config requires --entry or --cli and --model")
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
