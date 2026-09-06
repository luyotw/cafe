#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow drivers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import yaml

from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
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
DISPATCH_STATE_FILENAME = "dispatch_state.json"
LOCK_FILENAME = "session.lock"
FAILURE_NOTIFICATIONS_FILENAME = "callback_failure_notifications.json"
MAX_FAILURE_NOTIFICATIONS = 128
MAX_CALLBACK_INPUT_BYTES = 256 * 1024
_HOST_SESSION_KIND = "codex"
_CONTRACT_CALLBACK_CONFIG_SCHEMA = 4


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


def _read_bounded_text(path: Path, *, label: str) -> str:
    """Read callback inputs only after type and size checks."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} is unsafe")
    if metadata.st_size > MAX_CALLBACK_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CALLBACK_INPUT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if len(content) > MAX_CALLBACK_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the maximum bounded size")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is unreadable") from exc


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


def _prepared_workflow_id(issue_dir: Path) -> str:
    """Read the WorkflowInstance identity established by ``cafe prepare``."""
    path = issue_dir / "blackboard.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("event-driven ordered policy requires a prepared workflow")
    try:
        document = json.loads(_read_bounded_text(path, label="event-driven workflow state"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("event-driven workflow state is unreadable") from exc
    workflow_id = document.get("workflow_id") if isinstance(document, dict) else None
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("event-driven ordered policy requires a prepared workflow")
    return workflow_id


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
    """Create a legacy event binding only when no Driver contract exists."""
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
    prepared = issue_dir / "blackboard.json"
    if prepared.is_file() and not prepared.is_symlink():
        from cafe.driver import (
            DriverContractMissingError,
            EventCallbackRequest,
            event_callback_projection,
        )

        try:
            event_callback_projection(
                EventCallbackRequest(
                    issue_dir=issue_dir,
                    issue_name=issue_dir.name,
                    workflow_id=_prepared_workflow_id(issue_dir),
                )
            )
        except DriverContractMissingError:
            pass
        else:
            raise ValueError("contract-managed event drivers do not write legacy config")
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
            if (
                proposed["schema_version"] == 3
                or existing["schema_version"] == 3
                or (driver_dir / SESSION_FILENAME).exists()
                or (driver_dir / DISPATCH_STATE_FILENAME).exists()
            ):
                raise ValueError("event-driven binding cannot change within a prepared workflow")
        if proposed["schema_version"] == 3:
            _load_or_initialize_dispatch_state(
                driver_dir,
                workflow_id=_prepared_workflow_id(issue_dir),
                config=proposed,
            )
        _atomic_write(
            driver_dir / CONFIG_FILENAME,
            yaml.safe_dump(proposed, sort_keys=True).encode("utf-8"),
        )


def _load_config(driver_dir: Path) -> dict[str, Any] | None:
    path = driver_dir / CONFIG_FILENAME
    if not path.is_file() or path.is_symlink():
        return None
    try:
        loaded = yaml.load(
            _read_bounded_text(path, label="event-driven config"), Loader=_ExactSafeLoader
        )
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


def _contract_callback_config(
    *, issue_dir: Path, issue_name: str, workflow_id: str
) -> dict[str, Any] | None:
    """Derive one callback-only transport view from the durable contract.

    This value is intentionally never written as ``config.yaml``.  Mutable
    dispatch state retains only its sessions and event history and binds itself
    to the digest returned by this immediately preceding contract read.
    """
    from cafe.driver import EventCallbackRequest, event_callback_projection

    projection = event_callback_projection(
        EventCallbackRequest(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
    )
    if projection.event is None:
        return None
    entries = projection.event.get("clis")
    if not isinstance(entries, tuple):
        raise ValueError("event-driven Driver contract projection is invalid")
    normalized = _normalize_cli_entries(
        [(entry.get("cli"), entry.get("model")) for entry in entries if isinstance(entry, Mapping)]
    )
    if len(normalized) != len(entries):
        raise ValueError("event-driven Driver contract projection is invalid")
    config: dict[str, Any] = {
        "schema_version": _CONTRACT_CALLBACK_CONFIG_SCHEMA,
        "mode": "event-driven",
        "contract_sha256": projection.contract_sha256,
        "clis": normalized,
    }
    if normalized[0]["cli"] == AgentCLI.CODEX.value:
        host_session = _current_host_session_binding()
        if host_session is not None:
            config["host_session"] = host_session
    return config


def _valid_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_dispatch_attempt(
    attempt: Any,
    *,
    entries: list[dict[str, Any]],
) -> tuple[int, str, str]:
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
    stage = attempt.get("stage")
    status = attempt.get("status")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index < len(entries)
        or stage not in {"bootstrap", "delivery"}
        or status not in {"pending", "acquired", "accepted", "failed", "ambiguous"}
        or not _valid_nonempty_string(attempt.get("started_at"))
    ):
        raise ValueError("event-driven dispatch attempt is invalid")

    outcome = attempt.get("outcome")
    reason = attempt.get("reason")
    session_id = attempt.get("session_id")
    finished_at = attempt.get("finished_at")
    if status == "pending":
        valid_transition = all(
            value is None for value in (outcome, reason, session_id, finished_at)
        )
    elif status == "acquired":
        valid_transition = (
            stage == "bootstrap"
            and outcome == "session_acquired"
            and reason == "provider_evidence"
            and _valid_nonempty_string(session_id)
            and _valid_nonempty_string(finished_at)
        )
    elif status == "accepted":
        valid_transition = (
            stage == "delivery"
            and outcome == "durable_acceptance"
            and reason == "provider_acknowledgement"
            and _valid_nonempty_string(session_id)
            and _valid_nonempty_string(finished_at)
        )
    else:
        valid_transition = (
            outcome == ("conclusive_nonacceptance" if status == "failed" else "ambiguous")
            and _valid_nonempty_string(reason)
            and _valid_nonempty_string(finished_at)
            and (session_id is None if stage == "bootstrap" else _valid_nonempty_string(session_id))
        )
    if not valid_transition:
        raise ValueError("event-driven dispatch attempt transition is invalid")

    if status in {"acquired", "accepted", "failed", "ambiguous"} and stage == "delivery":
        session = entries[index].get("session")
        if not isinstance(session, dict) or session.get("id") != session_id:
            raise ValueError("event-driven delivery session provenance is invalid")
    if status == "acquired":
        session = entries[index].get("session")
        if not isinstance(session, dict) or session.get("id") != session_id:
            raise ValueError("event-driven bootstrap session provenance is invalid")
    return index, stage, status


def _validate_dispatch_events(state: dict[str, Any]) -> None:
    entries = state["entries"]
    workflow_id = state["workflow_id"]
    accepted_indexes: list[int] = []
    sequences: set[int] = set()
    expected_event_fields = {
        "event",
        "starting_index",
        "status",
        "attempts",
        "accepted_index",
        "takeover",
        "recovery_pending",
    }
    for event_id, event_state in state["events"].items():
        if (
            not _valid_nonempty_string(event_id)
            or not isinstance(event_state, dict)
            or set(event_state) != expected_event_fields
        ):
            raise ValueError("event-driven dispatch event is invalid")
        event = event_state.get("event")
        sequence = event.get("sequence") if isinstance(event, dict) else None
        if (
            not isinstance(event, dict)
            or event.get("workflow_id") != workflow_id
            or event.get("event_id") != event_id
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= 0
            or sequence in sequences
            or not _valid_nonempty_string(event.get("occurred_at"))
        ):
            raise ValueError("event-driven dispatch event identity is invalid")
        sequences.add(sequence)

        starting_index = event_state.get("starting_index")
        attempts = event_state.get("attempts")
        status = event_state.get("status")
        if (
            not isinstance(starting_index, int)
            or isinstance(starting_index, bool)
            or not 0 <= starting_index < len(entries)
            or not isinstance(attempts, list)
            or status not in {"routing", "accepted", "recovery_pending", "exhausted"}
            or not isinstance(event_state.get("recovery_pending"), bool)
        ):
            raise ValueError("event-driven dispatch event is invalid")

        previous: tuple[int, str, str] | None = None
        for position, attempt in enumerate(attempts):
            current = _validate_dispatch_attempt(attempt, entries=entries)
            index, stage, attempt_status = current
            if previous is None:
                valid_order = index == starting_index
            else:
                prior_index, prior_stage, prior_status = previous
                if prior_stage == "bootstrap" and prior_status == "acquired":
                    valid_order = index == prior_index and stage == "delivery"
                elif prior_status == "failed":
                    valid_order = index == prior_index + 1
                else:
                    valid_order = False
            if not valid_order or (
                attempt_status in {"pending", "accepted", "ambiguous"}
                and position != len(attempts) - 1
            ):
                raise ValueError("event-driven dispatch attempt order is invalid")
            previous = current

        accepted_index = event_state.get("accepted_index")
        takeover = event_state.get("takeover")
        recovery_pending = event_state["recovery_pending"]
        last_status = previous[2] if previous is not None else None
        last_index = previous[0] if previous is not None else None
        if status == "accepted":
            if (
                not isinstance(accepted_index, int)
                or isinstance(accepted_index, bool)
                or accepted_index != last_index
                or last_status != "accepted"
                or recovery_pending
            ):
                raise ValueError("event-driven accepted event is inconsistent")
            accepted_indexes.append(accepted_index)
            if accepted_index > starting_index:
                prior_failure = next(
                    (
                        attempt
                        for attempt in reversed(attempts[:-1])
                        if attempt.get("outcome") == "conclusive_nonacceptance"
                    ),
                    None,
                )
                expected_takeover = {
                    "event_id",
                    "sequence",
                    "occurred_at",
                    "from_index",
                    "to_index",
                    "eligible_reason",
                    "accepted_at",
                }
                if (
                    not isinstance(takeover, dict)
                    or set(takeover) != expected_takeover
                    or takeover.get("event_id") != event_id
                    or takeover.get("sequence") != sequence
                    or takeover.get("occurred_at") != event["occurred_at"]
                    or takeover.get("from_index") != starting_index
                    or takeover.get("to_index") != accepted_index
                    or prior_failure is None
                    or takeover.get("eligible_reason") != prior_failure.get("reason")
                    or not _valid_nonempty_string(takeover.get("accepted_at"))
                ):
                    raise ValueError("event-driven takeover record is invalid")
            elif takeover is not None:
                raise ValueError("event-driven primary acceptance cannot record takeover")
        elif accepted_index is not None or takeover is not None:
            raise ValueError("event-driven unaccepted event has acceptance state")
        elif status == "recovery_pending":
            if last_status != "ambiguous" or not recovery_pending:
                raise ValueError("event-driven recovery event is inconsistent")
        elif status == "exhausted":
            if last_status != "failed" or last_index != len(entries) - 1 or not recovery_pending:
                raise ValueError("event-driven exhausted event is inconsistent")
        elif recovery_pending or last_status in {"accepted", "ambiguous"}:
            raise ValueError("event-driven routing event is inconsistent")

    expected_active = max(accepted_indexes, default=0)
    if state["active_index"] != expected_active:
        raise ValueError("event-driven active entry is inconsistent")


def _load_or_initialize_dispatch_state(
    driver_dir: Path,
    *,
    workflow_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Load mutable state without allowing it to become transport authority."""
    contract_managed = config.get("schema_version") == _CONTRACT_CALLBACK_CONFIG_SCHEMA
    if config.get("schema_version") not in {3, _CONTRACT_CALLBACK_CONFIG_SCHEMA}:
        raise ValueError("event-driven dispatch state requires an ordered configuration")
    path = driver_dir / DISPATCH_STATE_FILENAME
    if path.is_file() and not path.is_symlink():
        try:
            state = json.loads(_read_bounded_text(path, label="event-driven dispatch state"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("event-driven dispatch state is unreadable") from exc
        expected_fields = (
            {
                "schema_version",
                "workflow_id",
                "contract_sha256",
                "active_index",
                "entries",
                "events",
                "updated_at",
            }
            if contract_managed
            else {
                "schema_version",
                "workflow_id",
                "policy",
                "active_index",
                "entries",
                "events",
                "updated_at",
            }
        )
        if not isinstance(state, dict) or set(state) != expected_fields:
            raise ValueError("event-driven dispatch state is invalid")
        expected_schema = 2 if contract_managed else 1
        if (
            state.get("schema_version") != expected_schema
            or state.get("workflow_id") != workflow_id
        ):
            raise ValueError("event-driven dispatch state belongs to another workflow")
        if not contract_managed and state.get("policy") != config:
            raise ValueError("event-driven dispatch policy cannot change within a workflow")
        if contract_managed and state.get("contract_sha256") != config.get("contract_sha256"):
            raise ValueError("event-driven dispatch state belongs to a stale Driver contract")
        entries = state.get("entries")
        if not isinstance(entries, list) or len(entries) != len(config["clis"]):
            raise ValueError("event-driven dispatch state is invalid")
        for index, (entry, policy_entry) in enumerate(zip(entries, config["clis"])):
            expected_entry_fields = (
                {"index", "session"} if contract_managed else {"index", "cli", "model", "session"}
            )
            if not isinstance(entry, dict) or set(entry) != expected_entry_fields:
                raise ValueError("event-driven dispatch state is invalid")
            if entry.get("index") != index or (
                not contract_managed
                and (
                    entry.get("cli") != policy_entry["cli"]
                    or entry.get("model") != policy_entry["model"]
                )
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
        if contract_managed:
            state["entries"] = [
                {"index": entry["index"], **policy_entry, "session": entry["session"]}
                for entry, policy_entry in zip(entries, config["clis"])
            ]
        active_index = state.get("active_index")
        if (
            not isinstance(active_index, int)
            or isinstance(active_index, bool)
            or not 0 <= active_index < len(entries)
            or not _valid_nonempty_string(state.get("updated_at"))
        ):
            raise ValueError("event-driven dispatch state is invalid")
        if not isinstance(state.get("events"), dict):
            raise ValueError("event-driven dispatch state is invalid")
        _validate_dispatch_events(state)
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
        "schema_version": 2 if contract_managed else 1,
        "workflow_id": workflow_id,
        "active_index": 0,
        "entries": entries,
        "events": {},
        "updated_at": now,
    }
    if contract_managed:
        state["contract_sha256"] = config["contract_sha256"]
    else:
        state["policy"] = config
    _write_dispatch_state(driver_dir, state)
    return state


def _write_dispatch_state(driver_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    updated["updated_at"] = _now()
    durable = copy.deepcopy(updated)
    if durable.get("schema_version") == 2:
        entries = durable.get("entries")
        if not isinstance(entries, list):
            raise ValueError("event-driven dispatch state is invalid")
        durable["entries"] = [
            {"index": entry.get("index"), "session": entry.get("session")} for entry in entries
        ]
    _atomic_write(
        driver_dir / DISPATCH_STATE_FILENAME,
        json.dumps(durable, sort_keys=True).encode("utf-8"),
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
        raw = json.loads(_read_bounded_text(path, label="event-driven session"))
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
                "attempts": [_project_attempt(attempt, entries=entries) for attempt in attempts],
                "takeover": copy.deepcopy(event_state.get("takeover")),
                "recovery_pending": event_state["recovery_pending"],
            }
        )
    return sorted(projected, key=lambda item: (item["sequence"], item["event_id"]))


def read_status(issue_dir: Path) -> dict[str, Any]:
    """Project exact event-driver state without locks, writes, or output inference."""
    driver_dir = _driver_dir(issue_dir)
    try:
        config = _contract_callback_config(
            issue_dir=issue_dir,
            issue_name=issue_dir.name,
            workflow_id=_prepared_workflow_id(issue_dir),
        )
    except ValueError as exc:
        from cafe.driver import DriverContractMissingError

        if not isinstance(exc, DriverContractMissingError) and (
            "requires a prepared workflow" not in str(exc)
        ):
            raise
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
            raw_state = json.loads(
                _read_bounded_text(state_path, label="event-driven dispatch state")
            )
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
    state_entries = (
        state["entries"]
        if state is not None
        else [
            {"index": index, **entry, "session": None} for index, entry in enumerate(policy_entries)
        ]
    )
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
        "schema_version": config["schema_version"],
        "mode": (
            "contract_ordered_transport_chain"
            if config["schema_version"] == _CONTRACT_CALLBACK_CONFIG_SCHEMA
            else "ordered_transport_chain"
        ),
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
            loaded = json.loads(_read_bounded_text(self.path, label="event-driven session"))
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
    issue_name = _validated_issue_name(event)
    workflow_id = event.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("workflow event callback has an invalid workflow ID")
    issue_dir = repository_root / ".cafe" / "issues" / issue_name
    driver_dir = _driver_dir(issue_dir)
    with _session_lock(driver_dir):
        config = _contract_callback_config(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
        if config is None:
            return
        from cafe.core.blackboard import BlackboardStore

        blackboard = BlackboardStore(issue_dir).load_or_create("spec")
        if blackboard.workflow_id != workflow_id:
            raise StaleWorkflowEventError("workflow event callback is stale")
        if config["schema_version"] in {3, _CONTRACT_CALLBACK_CONFIG_SCHEMA}:
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
        raw = json.loads(_read_bounded_text(path, label="callback failure notifications"))
    except (FileNotFoundError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
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
