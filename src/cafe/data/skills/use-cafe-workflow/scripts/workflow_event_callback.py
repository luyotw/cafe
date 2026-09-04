#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow drivers."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

import yaml

from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
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
            session = entry.get("session")
            if session is not None and (
                not isinstance(session, dict)
                or set(session) != {"id", "source", "acquired_at"}
                or not isinstance(session.get("id"), str)
                or not session["id"].strip()
                or session.get("source") not in {"host_session", "provider"}
                or not isinstance(session.get("acquired_at"), str)
                or not session["acquired_at"]
            ):
                raise ValueError("event-driven dispatch session provenance is invalid")
            host_session = config.get("host_session")
            if index == 0 and isinstance(host_session, dict):
                if session is None or session.get("id") != host_session["thread_id"]:
                    raise ValueError("event-driven host session conflicts with dispatch state")
            elif isinstance(session, dict) and session.get("source") == "host_session":
                raise ValueError("event-driven host session cannot bind a fallback")
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


def _write_dispatch_state(driver_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    updated["updated_at"] = _now()
    _atomic_write(
        driver_dir / DISPATCH_STATE_FILENAME,
        json.dumps(updated, sort_keys=True).encode("utf-8"),
    )
    return updated


def _entry_is_conforming(entry: dict[str, str]) -> bool:
    executor = AgentExecutor(
        AgentConfig(
            name=DRIVER_AGENT_NAME,
            cli=AgentCLI(entry["cli"]),
            model=entry["model"],
            clis=[],
            backup_clis=[],
        ),
        stream_output=False,
    )
    return executor.supports_event_driver()


def _read_legacy_session(
    driver_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Read and validate legacy session provenance without updating it."""
    path = driver_dir / SESSION_FILENAME
    if not path.is_file() or path.is_symlink():
        if path.exists():
            raise ValueError("event-driven session is invalid")
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("event-driven session is unreadable") from exc
    expected = {
        "schema_version",
        "workflow_id",
        "cli",
        "model",
        "session_id",
        "created_at",
        "last_used_at",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != expected
        or raw.get("schema_version") != 1
        or raw.get("cli") != config["cli"]
        or raw.get("model") != config["model"]
        or not isinstance(raw.get("workflow_id"), str)
        or not raw["workflow_id"]
        or not isinstance(raw.get("session_id"), str)
        or not raw["session_id"]
        or not isinstance(raw.get("created_at"), str)
        or not isinstance(raw.get("last_used_at"), str)
    ):
        raise ValueError("event-driven session is invalid")
    return raw


def _project_attempt(
    attempt: Any,
    *,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {
        "index",
        "stage",
        "status",
        "outcome",
        "reason",
        "session_id",
        "started_at",
        "finished_at",
    }
    if not isinstance(attempt, dict) or set(attempt) != expected:
        raise ValueError("event-driven dispatch attempt is invalid")
    index = attempt.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(entries):
        raise ValueError("event-driven dispatch attempt is invalid")
    if attempt.get("stage") not in {"bootstrap", "delivery"}:
        raise ValueError("event-driven dispatch attempt is invalid")
    if attempt.get("status") not in {"pending", "acquired", "accepted", "failed", "ambiguous"}:
        raise ValueError("event-driven dispatch attempt is invalid")
    return {
        "index": index,
        "cli": entries[index]["cli"],
        "stage": attempt["stage"],
        "status": attempt["status"],
        "outcome": attempt.get("outcome"),
        "reason": attempt.get("reason"),
        "session_id": attempt.get("session_id"),
        "started_at": attempt.get("started_at"),
        "finished_at": attempt.get("finished_at"),
    }


def _project_v3_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    entries = state["entries"]
    expected = {
        "event",
        "starting_index",
        "status",
        "attempts",
        "accepted_index",
        "takeover",
        "recovery_pending",
    }
    for event_id, event_state in state["events"].items():
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event-driven dispatch event is invalid")
        if not isinstance(event_state, dict) or set(event_state) != expected:
            raise ValueError("event-driven dispatch event is invalid")
        event = event_state.get("event")
        attempts = event_state.get("attempts")
        if (
            not isinstance(event, dict)
            or event.get("event_id") != event_id
            or not isinstance(event.get("sequence"), int)
            or isinstance(event.get("sequence"), bool)
            or not isinstance(event.get("occurred_at"), str)
            or not isinstance(attempts, list)
        ):
            raise ValueError("event-driven dispatch event is invalid")
        starting_index = event_state.get("starting_index")
        accepted_index = event_state.get("accepted_index")
        if (
            not isinstance(starting_index, int)
            or isinstance(starting_index, bool)
            or not 0 <= starting_index < len(entries)
            or (
                accepted_index is not None
                and (
                    not isinstance(accepted_index, int)
                    or isinstance(accepted_index, bool)
                    or not 0 <= accepted_index < len(entries)
                )
            )
            or not isinstance(event_state.get("recovery_pending"), bool)
        ):
            raise ValueError("event-driven dispatch event is invalid")
        projected.append(
            {
                "event_id": event_id,
                "sequence": event["sequence"],
                "occurred_at": event["occurred_at"],
                "event_type": event.get("event_type"),
                "status": event_state.get("status"),
                "starting_index": starting_index,
                "accepted_index": accepted_index,
                "attempts": [
                    _project_attempt(attempt, entries=entries) for attempt in attempts
                ],
                "takeover": copy.deepcopy(event_state.get("takeover")),
                "recovery_pending": event_state["recovery_pending"],
            }
        )
    return sorted(projected, key=lambda item: (item["sequence"], item["event_id"]))


def read_status(issue_dir: Path) -> dict[str, Any]:
    """Project exact event-driver state without locks, writes, or output inference."""
    driver_dir = _driver_dir(issue_dir)
    config = _load_config(driver_dir)
    if config is None:
        return {
            "configured": False,
            "schema_version": None,
            "mode": None,
            "workflow_id": None,
            "active_index": None,
            "entries": [],
            "events": [],
            "recovery_pending": False,
        }

    if config["schema_version"] in {1, 2}:
        session = _read_legacy_session(driver_dir, config)
        host_session = config.get("host_session")
        if session is not None:
            provenance = {
                "id": session["session_id"],
                "source": "legacy_session",
                "created_at": session["created_at"],
                "last_used_at": session["last_used_at"],
            }
        elif isinstance(host_session, dict):
            provenance = {"id": host_session["thread_id"], "source": "host_session"}
        else:
            provenance = None
        entry = {"cli": config["cli"], "model": config["model"]}
        return {
            "configured": True,
            "schema_version": config["schema_version"],
            "mode": "legacy_single_transport",
            "workflow_id": session["workflow_id"] if session is not None else None,
            "active_index": 0,
            "entries": [
                {
                    "index": 0,
                    **entry,
                    "conforming": _entry_is_conforming(entry),
                    "active": True,
                    "acquisition": {
                        "status": "acquired" if provenance is not None else "unacquired",
                        "session": provenance,
                    },
                }
            ],
            "events": [],
            "recovery_pending": False,
        }

    state_path = driver_dir / DISPATCH_STATE_FILENAME
    if not state_path.is_file() or state_path.is_symlink():
        if state_path.exists():
            raise ValueError("event-driven dispatch state is invalid")
        state = None
    else:
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("event-driven dispatch state is unreadable") from exc
        workflow_id = raw_state.get("workflow_id") if isinstance(raw_state, dict) else None
        if not isinstance(workflow_id, str) or not workflow_id:
            raise ValueError("event-driven dispatch state is invalid")
        state = _load_or_initialize_dispatch_state(
            driver_dir,
            workflow_id=workflow_id,
            config=config,
        )

    policy_entries = config["clis"]
    state_entries = state["entries"] if state is not None else [
        {"index": index, **entry, "session": None}
        for index, entry in enumerate(policy_entries)
    ]
    events = _project_v3_events(state) if state is not None else []
    active_index = state["active_index"] if state is not None else 0
    entries: list[dict[str, Any]] = []
    for index, (policy, state_entry) in enumerate(zip(policy_entries, state_entries)):
        session = copy.deepcopy(state_entry["session"])
        acquisition_status = "acquired" if session is not None else "unacquired"
        if session is None:
            bootstrap_attempts = [
                attempt
                for event in events
                for attempt in event["attempts"]
                if attempt["index"] == index and attempt["stage"] == "bootstrap"
            ]
            if bootstrap_attempts:
                acquisition_status = f"bootstrap_{bootstrap_attempts[-1]['status']}"
        entries.append(
            {
                "index": index,
                **policy,
                "conforming": _entry_is_conforming(policy),
                "active": index == active_index,
                "acquisition": {"status": acquisition_status, "session": session},
            }
        )
    return {
        "configured": True,
        "schema_version": 3,
        "mode": "ordered_transport_chain",
        "workflow_id": state["workflow_id"] if state is not None else None,
        "active_index": active_index,
        "entries": entries,
        "events": events,
        "recovery_pending": any(event["recovery_pending"] for event in events),
    }


def _bounded_event(event: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "workflow_id",
        "issue",
        "event_type",
        "event_id",
        "sequence",
        "occurred_at",
        "step",
        "status_code",
        "runtime",
        "attempt",
        "hop",
        "reason",
        "task_id",
    }
    return {key: value for key, value in event.items() if key in allowed}


def _ensure_dispatch_event(
    driver_dir: Path,
    state: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    event_id = event.get("event_id")
    sequence = event.get("sequence")
    occurred_at = event.get("occurred_at")
    if (
        not isinstance(event_id, str)
        or not event_id
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence <= 0
        or not isinstance(occurred_at, str)
        or not occurred_at
        or event.get("workflow_id") != state["workflow_id"]
    ):
        raise ValueError("workflow callback event identity is invalid")
    bounded = _bounded_event(event)
    existing = state["events"].get(event_id)
    if existing is not None:
        if not isinstance(existing, dict) or existing.get("event") != bounded:
            raise ValueError("workflow callback event conflicts with dispatch state")
        return state

    updated = copy.deepcopy(state)
    updated["events"][event_id] = {
        "event": bounded,
        "starting_index": state["active_index"],
        "status": "routing",
        "attempts": [],
        "accepted_index": None,
        "takeover": None,
        "recovery_pending": False,
    }
    return _write_dispatch_state(driver_dir, updated)


def _classify_provider_failure(error: BaseException) -> str:
    conclusive = {
        "cli_not_found",
        "cli_unavailable",
        "authentication",
        "model_not_found",
        "provider_overloaded",
        "rate_limit",
        "session_not_found",
        "transport_rejected",
        "queue_rejected",
        "invalid_acknowledgement",
    }
    if isinstance(error, AgentExecutionError) and error.error_type in conclusive:
        return "conclusive_nonacceptance"
    if isinstance(error, (FileNotFoundError, subprocess.CalledProcessError)):
        return "conclusive_nonacceptance"
    return "ambiguous"


def _append_pending_attempt(
    driver_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
    index: int,
    stage: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    attempts = updated["events"][event_id]["attempts"]
    attempts.append(
        {
            "index": index,
            "stage": stage,
            "status": "pending",
            "outcome": None,
            "reason": None,
            "session_id": None,
            "started_at": _now(),
            "finished_at": None,
        }
    )
    return _write_dispatch_state(driver_dir, updated)


def _finish_pending_attempt(
    driver_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
    status: str,
    outcome: str,
    reason: str,
    session_id: str | None = None,
    recovery_pending: bool = False,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    attempt = updated["events"][event_id]["attempts"][-1]
    if attempt.get("status") != "pending":
        raise ValueError("event-driven dispatch attempt is not pending")
    attempt.update(
        {
            "status": status,
            "outcome": outcome,
            "reason": reason,
            "session_id": session_id,
            "finished_at": _now(),
        }
    )
    if recovery_pending:
        updated["events"][event_id]["status"] = "recovery_pending"
        updated["events"][event_id]["recovery_pending"] = True
    return _write_dispatch_state(driver_dir, updated)


def _observed_session_ids(records: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(records, (list, tuple)):
        return result
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in ("thread_id", "session_id", "sessionId"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                result.add(value.strip())
    return result


def _acquire_v3_session(
    driver_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
    index: int,
    repository_root: Path,
    executor_factory=None,
) -> tuple[dict[str, Any], str]:
    """Acquire and atomically persist one provider-owned session."""
    if executor_factory is None:
        executor_factory = AgentExecutor
    entry = state["entries"][index]
    if entry["session"] is not None:
        return state, "acquired"
    event_state = state["events"].get(event_id)
    if not isinstance(event_state, dict):
        raise ValueError("event-driven dispatch event is missing")
    attempts = event_state.get("attempts")
    if isinstance(attempts, list) and attempts and attempts[-1].get("status") == "pending":
        return state, "ambiguous"

    state = _append_pending_attempt(
        driver_dir,
        state,
        event_id=event_id,
        index=index,
        stage="bootstrap",
    )
    executor = executor_factory(
        AgentConfig(
            name=DRIVER_AGENT_NAME,
            cli=AgentCLI(entry["cli"]),
            model=entry["model"],
            clis=[],
            backup_clis=[],
        ),
        stream_output=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="cafe-event-bootstrap-") as temporary:
            result = executor.execute_event_driver(
                'say "HI"',
                allowed_tools=[],
                allowed_directories=[],
                execution_control=AgentExecutionControl(
                    working_directory=Path(temporary),
                    max_duration_seconds=60,
                    max_output_bytes=64 * 1024,
                    max_output_lines=128,
                ),
            )
    except Exception as exc:
        classification = _classify_provider_failure(exc)
        state = _finish_pending_attempt(
            driver_dir,
            state,
            event_id=event_id,
            status="failed" if classification == "conclusive_nonacceptance" else "ambiguous",
            outcome=classification,
            reason=getattr(exc, "error_type", None) or type(exc).__name__,
            recovery_pending=classification == "ambiguous",
        )
        return state, classification

    session_id = getattr(result, "session_id", None)
    records = getattr(result, "records", ())
    if not isinstance(session_id, str) or not session_id.strip():
        observed_ids = _observed_session_ids(records)
        classification = "ambiguous" if len(observed_ids) > 1 else "conclusive_nonacceptance"
        state = _finish_pending_attempt(
            driver_dir,
            state,
            event_id=event_id,
            status="failed" if classification == "conclusive_nonacceptance" else "ambiguous",
            outcome=classification,
            reason="invalid_session_result",
            recovery_pending=classification == "ambiguous",
        )
        return state, classification

    updated = copy.deepcopy(state)
    now = _now()
    updated["entries"][index]["session"] = {
        "id": session_id.strip(),
        "source": "provider",
        "acquired_at": now,
    }
    attempt = updated["events"][event_id]["attempts"][-1]
    attempt.update(
        {
            "status": "acquired",
            "outcome": "session_acquired",
            "reason": "provider_evidence",
            "session_id": session_id.strip(),
            "finished_at": now,
        }
    )
    return _write_dispatch_state(driver_dir, updated), "acquired"


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


def _accept_delivery(
    driver_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
    index: int,
    session_id: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    event_state = updated["events"][event_id]
    attempt = event_state["attempts"][-1]
    if attempt.get("stage") != "delivery" or attempt.get("status") != "pending":
        raise ValueError("event-driven delivery attempt is not pending")
    now = _now()
    attempt.update(
        {
            "status": "accepted",
            "outcome": "durable_acceptance",
            "reason": "provider_acknowledgement",
            "session_id": session_id,
            "finished_at": now,
        }
    )
    event_state["status"] = "accepted"
    event_state["accepted_index"] = index
    event_state["recovery_pending"] = False
    starting_index = event_state["starting_index"]
    if index > starting_index:
        prior_failure = next(
            (
                item
                for item in reversed(event_state["attempts"][:-1])
                if item.get("outcome") == "conclusive_nonacceptance"
            ),
            None,
        )
        event_state["takeover"] = {
            "event_id": event_id,
            "sequence": event_state["event"]["sequence"],
            "occurred_at": event_state["event"]["occurred_at"],
            "from_index": starting_index,
            "to_index": index,
            "eligible_reason": prior_failure.get("reason") if prior_failure else None,
            "accepted_at": now,
        }
    updated["active_index"] = index
    return _write_dispatch_state(driver_dir, updated)


def _deliver_v3_callback(
    driver_dir: Path,
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    index: int,
    repository_root: Path,
    executor_factory=None,
) -> tuple[dict[str, Any], str]:
    if executor_factory is None:
        executor_factory = AgentExecutor
    event_id = event["event_id"]
    entry = state["entries"][index]
    session = entry.get("session")
    if not isinstance(session, dict):
        raise ValueError("actual callback requires durable session provenance")
    attempts = state["events"][event_id]["attempts"]
    if attempts and attempts[-1].get("status") == "pending":
        return state, "ambiguous"

    state = _append_pending_attempt(
        driver_dir,
        state,
        event_id=event_id,
        index=index,
        stage="delivery",
    )
    session_id = session["id"]
    acceptance_persisted = False
    acceptance_write_failed = False

    def persist_acceptance() -> None:
        nonlocal state, acceptance_persisted, acceptance_write_failed
        if acceptance_persisted:
            return
        try:
            state = _accept_delivery(
                driver_dir,
                state,
                event_id=event_id,
                index=index,
                session_id=session_id,
            )
        except Exception:
            acceptance_write_failed = True
            raise
        acceptance_persisted = True

    try:
        if index == 0 and session.get("source") == "host_session":
            _queue_host_callback(
                _callback_prompt(event, repository_root=repository_root),
                thread_id=session_id,
                model=entry["model"],
                repository_root=repository_root,
            )
            accepted = True
            records: Any = ()
            reported_session_id = session_id
        else:
            executor = executor_factory(
                AgentConfig(
                    name=DRIVER_AGENT_NAME,
                    cli=AgentCLI(entry["cli"]),
                    model=entry["model"],
                    session_id=session_id,
                    clis=[],
                    backup_clis=[],
                ),
                stream_output=False,
            )
            result = executor.execute_event_driver(
                _callback_prompt(event, repository_root=repository_root),
                expected_session_id=session_id,
                event_id=event_id,
                on_acceptance=persist_acceptance,
                allowed_tools=["Read", "Grep", "Glob", "Bash"],
                allowed_directories=[str(repository_root)],
                execution_control=AgentExecutionControl(
                    max_duration_seconds=60,
                    max_output_bytes=64 * 1024,
                    max_output_lines=128,
                ),
            )
            accepted = bool(getattr(result, "accepted", False))
            records = getattr(result, "records", ())
            reported_session_id = getattr(result, "session_id", None)
    except Exception as exc:
        if acceptance_write_failed:
            raise
        if acceptance_persisted:
            return state, "accepted"
        classification = _classify_provider_failure(exc)
        state = _finish_pending_attempt(
            driver_dir,
            state,
            event_id=event_id,
            status="failed" if classification == "conclusive_nonacceptance" else "ambiguous",
            outcome=classification,
            reason=getattr(exc, "error_type", None) or type(exc).__name__,
            session_id=session_id,
            recovery_pending=classification == "ambiguous",
        )
        return state, classification

    if acceptance_persisted:
        return state, "accepted"
    if accepted and reported_session_id == session_id:
        return (
            _accept_delivery(
                driver_dir,
                state,
                event_id=event_id,
                index=index,
                session_id=session_id,
            ),
            "accepted",
        )

    observed_ids = _observed_session_ids(records)
    conflicting = bool(observed_ids and observed_ids != {session_id})
    classification = "ambiguous" if accepted or conflicting else "conclusive_nonacceptance"
    state = _finish_pending_attempt(
        driver_dir,
        state,
        event_id=event_id,
        status="ambiguous" if classification == "ambiguous" else "failed",
        outcome=classification,
        reason="conflicting_session_evidence" if conflicting else "invalid_acknowledgement",
        session_id=session_id,
        recovery_pending=classification == "ambiguous",
    )
    return state, classification


def _exhaust_event(
    driver_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    event_state = updated["events"][event_id]
    event_state["status"] = "exhausted"
    event_state["recovery_pending"] = True
    return _write_dispatch_state(driver_dir, updated)


def _run_v3_callback(
    driver_dir: Path,
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    repository_root: Path,
    executor_factory=None,
) -> dict[str, Any]:
    """Run serial acquisition/delivery attempts until first acceptance or recovery."""
    if executor_factory is None:
        executor_factory = AgentExecutor
    event_id = event["event_id"]
    event_state = state["events"][event_id]
    if event_state["status"] in {"accepted", "exhausted", "recovery_pending"}:
        return state
    attempts = event_state["attempts"]
    if attempts and attempts[-1].get("status") == "pending":
        return state
    index = event_state["starting_index"]
    if attempts:
        last = attempts[-1]
        index = last["index"]
        if last.get("outcome") == "conclusive_nonacceptance":
            index += 1

    while index < len(state["entries"]):
        state, acquisition = _acquire_v3_session(
            driver_dir,
            state,
            event_id=event_id,
            index=index,
            repository_root=repository_root,
            executor_factory=executor_factory,
        )
        if acquisition == "ambiguous":
            return state
        if acquisition == "conclusive_nonacceptance":
            index += 1
            continue

        state, delivery = _deliver_v3_callback(
            driver_dir,
            state,
            event,
            index=index,
            repository_root=repository_root,
            executor_factory=executor_factory,
        )
        if delivery in {"accepted", "ambiguous"}:
            return state
        index += 1

    return _exhaust_event(driver_dir, state, event_id=event_id)


def _event_is_durable(blackboard, event: dict[str, Any]) -> bool:
    return any(
        entry.event_type == "workflow_event_callback_enqueued"
        and entry.data.get("event_id") == event.get("event_id")
        and entry.data.get("sequence") == event.get("sequence")
        and entry.data.get("occurred_at") == event.get("occurred_at")
        for entry in blackboard.events
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
            if not _event_is_durable(blackboard, event):
                raise ValueError("workflow event callback is not durable")
            state = _load_or_initialize_dispatch_state(
                driver_dir,
                workflow_id=workflow_id,
                config=config,
            )
            state = _ensure_dispatch_event(driver_dir, state, event)
            _run_v3_callback(
                driver_dir,
                state,
                event,
                repository_root=repository_root,
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
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--issue-dir")
    parser.add_argument("--cli")
    parser.add_argument("--model")
    parser.add_argument("--entry", action="append", default=[])
    args = parser.parse_args(argv)
    if args.status:
        if args.write_config or args.workflow_event or not args.issue_dir:
            parser.error("--status requires only --issue-dir")
        print(json.dumps(read_status(Path(args.issue_dir)), ensure_ascii=False, sort_keys=True))
        return 0
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
