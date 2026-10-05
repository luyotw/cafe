#!/usr/bin/env python3
"""Skill-owned callback runner for event-driven workflow managers."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import select
import stat
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

import yaml

from cafe.agents.executor import AgentExecutionControl, AgentExecutionError, AgentExecutor
from cafe.agents.transport import ConversationTransport
from cafe.constraints import execution_context, numeric_limit
from cafe.core.audit_events import AuditEventStore
from cafe.core.conversation_locale import DEFAULT_CONVERSATION_LOCALE
from cafe.core.human_task_notifications import (
    build_workflow_callback_failure_message,
    load_human_task_notification_settings,
    load_slack_webhook_url,
    post_slack_notification,
)
from cafe.core.session import SessionStore
from cafe.core.task_inbox import TaskInboxError, TaskInboxService
from cafe.core.types import AgentCLI, AgentConfig, SessionData
from cafe.core.workflow_runtime import resolve_human_task_notification_repository_root
from cafe.utils.yaml_utils import SafeLoader

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is handled by the host.
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable outside Windows.
    msvcrt = None  # type: ignore[assignment]


DRIVER_AGENT_NAME = "__cafe_event_driver__"
MANAGER_AGENT_NAME = "__cafe_event_manager__"
CONFIG_FILENAME = "config.yaml"
SESSION_FILENAME = "session.json"
DISPATCH_STATE_FILENAME = "dispatch_state.json"
LOCK_FILENAME = "session.lock"
FAILURE_NOTIFICATIONS_FILENAME = "callback_failure_notifications.json"
MAX_FAILURE_NOTIFICATIONS = 128
MAX_CALLBACK_INPUT_BYTES = 256 * 1024
_HOST_SESSION_KIND = "codex"
_CONTRACT_CALLBACK_CONFIG_SCHEMA = 4


class _ExactSafeLoader(SafeLoader):
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
def _session_lock(manager_dir: Path, *, blocking: bool = True) -> Iterator[None]:
    manager_dir.mkdir(parents=True, exist_ok=True)
    lock_path = manager_dir / LOCK_FILENAME
    if manager_dir.is_symlink() or lock_path.is_symlink():
        raise ValueError("session lock path is unsafe")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("session lock must be a regular file")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        elif msvcrt is not None:  # pragma: no branch - platform-specific.
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
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


def _manager_dir(issue_dir: Path) -> Path:
    manager = issue_dir / "manager"
    driver = issue_dir / "driver"
    manager_contract = manager / "contract.json"
    driver_contract = driver / "contract.json"
    if manager_contract.exists() or driver_contract.exists():
        from cafe.manager._store import select_authority_directory

        return select_authority_directory(issue_dir)
    if driver_contract.exists():
        return driver
    if manager_contract.exists():
        return manager
    if driver.is_dir() and any(driver.iterdir()):
        return driver
    return driver


def _agent_name(manager_dir: Path) -> str:
    return MANAGER_AGENT_NAME if manager_dir.name == "manager" else DRIVER_AGENT_NAME


def _contract_api(issue_dir: Path):
    if _manager_dir(issue_dir).name == "driver":
        from cafe import driver as api
    else:
        from cafe import manager as api
    return api


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
    from cafe.core.audit_events import AuditEventStore

    store = AuditEventStore(issue_dir)
    path = store.binding
    if not path.is_file() or path.is_symlink():
        raise ValueError("event-driven ordered policy requires a prepared workflow")
    try:
        document = json.loads(_read_bounded_text(path, label="event-driven workflow identity"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("event-driven workflow state is unreadable") from exc
    workflow_id = document.get("workflow_id") if isinstance(document, dict) else None
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("event-driven ordered policy requires a prepared workflow")
    store.high_water(workflow_id)
    return workflow_id


def _normalize_cli_entries(
    clis: Sequence[tuple[str, str | None]], *, implicit_primary_model: bool = False
) -> list[dict[str, str]]:
    if not clis:
        raise ValueError("event-driven clis must be non-empty")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, (raw_cli, raw_model) in enumerate(clis):
        model = raw_model.strip() if isinstance(raw_model, str) else None
        if (
            not isinstance(raw_cli, str)
            or (
                implicit_primary_model
                and ((index == 0 and raw_model is not None) or (index > 0 and not model))
            )
            or (not implicit_primary_model and not model)
        ):
            raise ValueError("event-driven CLI entries require exact cli and model values")
        try:
            cli = AgentCLI(raw_cli).value
        except ValueError as exc:
            raise ValueError("event-driven CLI is not supported") from exc
        if cli in seen:
            raise ValueError("event-driven clis must use distinct CLIs")
        seen.add(cli)
        entry = {"cli": cli}
        if model is not None:
            entry["model"] = model
        normalized.append(entry)
    return normalized


def write_config(
    issue_dir: Path,
    *,
    cli: str | None = None,
    model: str | None = None,
    clis: Sequence[tuple[str, str]] | None = None,
) -> None:
    """Create a legacy event binding only when no Manager contract exists."""
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
        api = _contract_api(issue_dir)
        missing_error = (
            getattr(api, "ManagerContractMissingError", None) or api.DriverContractMissingError
        )

        try:
            api.event_callback_projection(
                api.EventCallbackRequest(
                    issue_dir=issue_dir,
                    issue_name=issue_dir.name,
                    workflow_id=_prepared_workflow_id(issue_dir),
                )
            )
        except missing_error:
            pass
        else:
            raise ValueError("contract-managed event managers do not write legacy config")
    manager_dir = _manager_dir(issue_dir)
    with _session_lock(manager_dir):
        existing = _load_config(manager_dir)
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
                or (manager_dir / SESSION_FILENAME).exists()
                or (manager_dir / DISPATCH_STATE_FILENAME).exists()
            ):
                raise ValueError("event-driven binding cannot change within a prepared workflow")
        if proposed["schema_version"] == 3:
            _load_or_initialize_dispatch_state(
                manager_dir,
                workflow_id=_prepared_workflow_id(issue_dir),
                config=proposed,
            )
        _atomic_write(
            manager_dir / CONFIG_FILENAME,
            yaml.safe_dump(proposed, sort_keys=True).encode("utf-8"),
        )


def _load_config(manager_dir: Path) -> dict[str, Any] | None:
    path = manager_dir / CONFIG_FILENAME
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
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
) -> dict[str, Any] | None:
    """Derive one callback-only transport view from the durable contract.

    This value is intentionally never written as ``config.yaml``.  Mutable
    dispatch state retains session identities and event history; the current
    contract remains the authority for callback routing.
    """
    api = _contract_api(issue_dir)

    projection = api.event_callback_projection(
        api.EventCallbackRequest(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
    )
    if projection.event is None:
        return None
    entries = projection.event.get("clis")
    if not isinstance(entries, tuple):
        raise ValueError("event-driven Manager contract projection is invalid")
    raw_entries: list[tuple[str, str | None]] = []
    for index, entry in enumerate(entries):
        expected = {"cli"} if index == 0 else {"cli", "model"}
        if not isinstance(entry, Mapping) or set(entry) != expected:
            raise ValueError("event-driven Manager contract projection is invalid")
        raw_entries.append((entry.get("cli"), entry.get("model")))
    normalized = _normalize_cli_entries(raw_entries, implicit_primary_model=True)
    config: dict[str, Any] = {
        "schema_version": _CONTRACT_CALLBACK_CONFIG_SCHEMA,
        "mode": "event-driven",
        "contract_sha256": projection.contract_sha256,
        "clis": normalized,
    }
    return config


def activate_confirmed_contract_with_host_session(
    *,
    issue_dir: Path,
    issue_name: str,
    workflow_id: str,
    activate_contract: Callable[[], Any],
) -> Any:
    """Activate the contract, then best-effort bind its visible Codex thread.

    Background workers and callback children intentionally discard Codex host
    controls. Initializing dispatch state during confirmed activation is the
    last trusted point where the originating App thread is still available.
    """
    result = activate_contract()
    manager_dir = _manager_dir(issue_dir)
    try:
        with _session_lock(manager_dir):
            config = _contract_callback_config(
                issue_dir=issue_dir,
                issue_name=issue_name,
                workflow_id=workflow_id,
            )
            if config is None or config["clis"][0]["cli"] != AgentCLI.CODEX.value:
                return result
            host_session = _current_host_session_binding()
            if host_session is None:
                return result
            state_path = manager_dir / DISPATCH_STATE_FILENAME
            if state_path.exists():
                state = _load_or_initialize_dispatch_state(
                    manager_dir,
                    workflow_id=workflow_id,
                    config=config,
                )
                first_session = state["entries"][0]["session"]
                if first_session is not None or state["events"]:
                    return result
                updated = copy.deepcopy(state)
                updated["entries"][0]["session"] = {
                    "id": host_session["thread_id"],
                    "source": "host_session",
                    "acquired_at": _now(),
                }
                _write_dispatch_state(manager_dir, updated)
                return result
            _load_or_initialize_dispatch_state(
                manager_dir,
                workflow_id=workflow_id,
                config={**config, "host_session": host_session},
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"Warning: Manager contract activated without Codex host binding: {exc}",
            file=sys.stderr,
        )
    return result


def _valid_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_dispatch_attempt(
    attempt: Any,
    *,
    route_length: int | None,
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
        or index < 0
        or (route_length is not None and index >= route_length)
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
        fields = frozenset(event_state) if isinstance(event_state, dict) else frozenset()
        if (
            not _valid_nonempty_string(event_id)
            or not isinstance(event_state, dict)
            or fields
            not in {
                frozenset(expected_event_fields),
                frozenset(expected_event_fields | {"routing_chain"}),
            }
        ):
            raise ValueError("event-driven dispatch event is invalid")
        routing_chain = event_state.get("routing_chain")
        if "routing_chain" in event_state and (
            state["schema_version"] != 2
            or not isinstance(routing_chain, list)
            or not routing_chain
            or any(
                not isinstance(entry, dict)
                or set(entry) != {"cli", "model"}
                or entry["cli"] not in {cli.value for cli in AgentCLI}
                or (entry["model"] is not None and not _valid_nonempty_string(entry["model"]))
                for entry in routing_chain
            )
            or len({entry["cli"] for entry in routing_chain}) != len(routing_chain)
        ):
            raise ValueError("event-driven dispatch route is invalid")
        route_length = (
            len(routing_chain)
            if routing_chain is not None
            else len(entries)
            if state["schema_version"] == 1
            else None
        )
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
            or starting_index < 0
            or (route_length is not None and starting_index >= route_length)
            or not isinstance(attempts, list)
            or status not in {"routing", "accepted", "recovery_pending", "exhausted"}
            or not isinstance(event_state.get("recovery_pending"), bool)
        ):
            raise ValueError("event-driven dispatch event is invalid")

        previous: tuple[int, str, str] | None = None
        for position, attempt in enumerate(attempts):
            current = _validate_dispatch_attempt(attempt, route_length=route_length)
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
            if (
                last_status != "failed"
                or (route_length is not None and last_index != route_length - 1)
                or not recovery_pending
            ):
                raise ValueError("event-driven exhausted event is inconsistent")
        elif recovery_pending or last_status in {"accepted", "ambiguous"}:
            raise ValueError("event-driven routing event is inconsistent")

    expected_active = max(accepted_indexes, default=0)
    if state["schema_version"] == 1 and state["active_index"] != expected_active:
        raise ValueError("event-driven active entry is inconsistent")


def _load_or_initialize_dispatch_state(
    manager_dir: Path,
    *,
    workflow_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Load mutable state without allowing it to become transport authority."""
    contract_managed = config.get("schema_version") == _CONTRACT_CALLBACK_CONFIG_SCHEMA
    if config.get("schema_version") not in {3, _CONTRACT_CALLBACK_CONFIG_SCHEMA}:
        raise ValueError("event-driven dispatch state requires an ordered configuration")
    path = manager_dir / DISPATCH_STATE_FILENAME
    if path.is_file() and not path.is_symlink():
        try:
            state = json.loads(_read_bounded_text(path, label="event-driven dispatch state"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("event-driven dispatch state is unreadable") from exc
        if contract_managed and isinstance(state, dict):
            # Older callbacks persisted the selected transport here. The
            # confirmed Manager contract now owns that choice, so discard this
            # obsolete snapshot when loading an otherwise current state.
            state.pop("transport_clis", None)
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
        entries = state.get("entries")
        if (
            not isinstance(entries, list)
            or not entries
            or (not contract_managed and len(entries) != len(config["clis"]))
        ):
            raise ValueError("event-driven dispatch state is invalid")
        recorded_route: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError("event-driven dispatch state is invalid")
            fields = set(entry)
            if fields not in (
                (
                    {"index", "session"},
                    {"index", "cli", "session"},
                    {"index", "cli", "model", "session"},
                )
                if contract_managed
                else ({"index", "cli", "model", "session"},)
            ):
                raise ValueError("event-driven dispatch state is invalid")
            if "cli" in entry:
                if entry["cli"] not in {cli.value for cli in AgentCLI} or (
                    entry.get("model") is not None and not _valid_nonempty_string(entry["model"])
                ):
                    raise ValueError("event-driven dispatch session provenance is invalid")
                recorded_route.append({"cli": entry["cli"], "model": entry.get("model")})
            if entry.get("index") != index or (
                not contract_managed
                and (
                    entry.get("cli") != config["clis"][index]["cli"]
                    or entry.get("model") != config["clis"][index].get("model")
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
            if (
                contract_managed
                and isinstance(session, dict)
                and session["source"] == "host_session"
                and (index != 0 or ("cli" in entry and entry["cli"] != AgentCLI.CODEX.value))
            ):
                raise ValueError("event-driven host session provenance is invalid")
            host_session = config.get("host_session")
            if not contract_managed and index == 0 and isinstance(host_session, dict):
                if (
                    session is None
                    or session.get("id") != host_session["thread_id"]
                    or session.get("source") != "host_session"
                ):
                    raise ValueError("event-driven host session conflicts with dispatch state")
            elif (
                not contract_managed
                and index != 0
                and isinstance(session, dict)
                and session.get("source") == "host_session"
            ):
                raise ValueError("event-driven host session cannot bind a fallback")
        if contract_managed:
            if recorded_route and len(recorded_route) != len(entries):
                raise ValueError("event-driven dispatch state is invalid")
            if len({item["cli"] for item in recorded_route}) != len(recorded_route):
                raise ValueError("event-driven dispatch session provenance is invalid")
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
        if contract_managed:
            available = {
                (entry["cli"], entry.get("model")): entry["session"]
                for entry in entries
                if "cli" in entry
                and isinstance(entry["session"], dict)
                and entry["session"]["source"] == "provider"
            }
            host = next(
                (
                    entry["session"]
                    for entry in entries
                    if isinstance(entry["session"], dict)
                    and entry["session"]["source"] == "host_session"
                    and ("cli" not in entry or entry["cli"] == AgentCLI.CODEX.value)
                ),
                None,
            )
            current_host = config.get("host_session")
            state["entries"] = []
            for index, policy_entry in enumerate(config["clis"]):
                session = available.get((policy_entry["cli"], policy_entry.get("model")))
                if index == 0 and policy_entry["cli"] == AgentCLI.CODEX.value:
                    session = session or host
                    if session is None and isinstance(current_host, dict):
                        session = {
                            "id": current_host["thread_id"],
                            "source": "host_session",
                            "acquired_at": _now(),
                        }
                state["entries"].append({"index": index, **policy_entry, "session": session})
            current_route = [
                {"cli": entry["cli"], "model": entry.get("model")} for entry in config["clis"]
            ]
            if recorded_route != current_route:
                state["active_index"] = 0
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
    _write_dispatch_state(manager_dir, state)
    return state


def _write_dispatch_state(manager_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    updated["updated_at"] = _now()
    _atomic_write(
        manager_dir / DISPATCH_STATE_FILENAME,
        json.dumps(updated, sort_keys=True).encode("utf-8"),
    )
    return updated


def _entry_is_conforming(entry: dict[str, str]) -> bool:
    executor = AgentExecutor(
        AgentConfig(
            name=MANAGER_AGENT_NAME,
            cli=AgentCLI(entry["cli"]),
            model=entry.get("model"),
            clis=[],
            backup_clis=[],
        ),
        stream_output=False,
    )
    transport = ConversationTransport(executor)
    return all(transport.capabilities(operation).supported
               for operation in ("acquire_session", "deliver_to_exact_session"))


def _read_legacy_session(
    manager_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Read and validate legacy session provenance without updating it."""
    path = manager_dir / SESSION_FILENAME
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
    routing_chain: list[dict[str, Any]] | None,
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
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or (routing_chain is not None and index >= len(routing_chain))
    ):
        raise ValueError("event-driven dispatch attempt is invalid")
    if attempt.get("stage") not in {"bootstrap", "delivery"}:
        raise ValueError("event-driven dispatch attempt is invalid")
    if attempt.get("status") not in {"pending", "acquired", "accepted", "failed", "ambiguous"}:
        raise ValueError("event-driven dispatch attempt is invalid")
    return {
        "index": index,
        "cli": routing_chain[index]["cli"] if routing_chain is not None else None,
        "model": routing_chain[index].get("model") if routing_chain is not None else None,
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
        if not isinstance(event_state, dict) or set(event_state) not in (
            expected,
            expected | {"routing_chain"},
        ):
            raise ValueError("event-driven dispatch event is invalid")
        routing_chain = event_state.get("routing_chain")
        if state["schema_version"] == 1:
            routing_chain = entries
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
            or starting_index < 0
            or (routing_chain is not None and starting_index >= len(routing_chain))
            or (
                accepted_index is not None
                and (
                    not isinstance(accepted_index, int)
                    or isinstance(accepted_index, bool)
                    or accepted_index < 0
                    or (routing_chain is not None and accepted_index >= len(routing_chain))
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
                    _project_attempt(attempt, routing_chain=routing_chain) for attempt in attempts
                ],
                "takeover": copy.deepcopy(event_state.get("takeover")),
                "recovery_pending": event_state["recovery_pending"],
            }
        )
    return sorted(projected, key=lambda item: (item["sequence"], item["event_id"]))


def read_chat_session(issue_dir: Path, *, workflow_id: str) -> dict[str, Any]:
    """Verify the stored current route without initializing or adapting dispatch."""
    from cafe.agents.transport_types import _validated_evidence_scalar

    config = _contract_callback_config(
        issue_dir=issue_dir, issue_name=issue_dir.name, workflow_id=workflow_id,
    )
    if config is None:
        raise ValueError("unsupported_mode")
    manager_dir = _manager_dir(issue_dir)
    path = manager_dir / DISPATCH_STATE_FILENAME
    if not path.exists():
        raise ValueError("identity_absent")
    from cafe.manager._store import _decode_exact

    state = _decode_exact(_read_bounded_text(path, label="chat dispatch state").encode("utf-8"))
    expected = {"schema_version", "workflow_id", "contract_sha256", "active_index",
                "entries", "events", "updated_at"}
    if (not isinstance(state, dict) or set(state) != expected
            or state["schema_version"] != 2 or state["workflow_id"] != workflow_id
            or state["contract_sha256"] != config["contract_sha256"]
            or not _valid_nonempty_string(state["updated_at"])):
        raise ValueError("identity_conflict")
    entries = state["entries"]
    active = state["active_index"]
    if (not isinstance(entries, list) or len(entries) != len(config["clis"])
            or type(active) is not int or not 0 <= active < len(entries)
            or not isinstance(state["events"], dict)):
        raise ValueError("identity_conflict")
    for index, (entry, policy) in enumerate(zip(entries, config["clis"])):
        if (not isinstance(entry, dict)
                or set(entry) not in ({"index", "cli", "session"}, {"index", "cli", "model", "session"})
                or type(entry["index"]) is not int or entry["index"] != index
                or entry["cli"] != policy["cli"] or entry.get("model") != policy.get("model")):
            raise ValueError("identity_conflict")
        session = entry["session"]
        if session is not None:
            if (not isinstance(session, dict) or set(session) != {"id", "source", "acquired_at"}
                    or session["source"] not in {"provider", "host_session"}
                    or not _valid_nonempty_string(session["acquired_at"])
                    or (session["source"] == "host_session" and (index != 0 or entry["cli"] != "codex"))):
                raise ValueError("identity_conflict")
            _validated_evidence_scalar(session["id"])
    _validate_dispatch_events(state)
    if any(event["recovery_pending"] or event["status"] == "routing"
           or any(attempt["status"] in {"pending", "ambiguous"} for attempt in event["attempts"])
           for event in state["events"].values()):
        raise ValueError("recovery_pending")
    entry = entries[active]
    if entry["session"] is None:
        raise ValueError("identity_absent")
    if entry["session"]["source"] == "host_session":
        raise ValueError("host_bound")
    if entry["cli"] != "codex":
        raise ValueError("unsupported_provider")
    return {"session_id": entry["session"]["id"], "cli": entry["cli"],
            "model": entry.get("model"), "contract_sha256": config["contract_sha256"]}


def read_status(issue_dir: Path) -> dict[str, Any]:
    """Project exact event-manager state without locks, writes, or output inference."""
    manager_dir = _manager_dir(issue_dir)
    try:
        config = _contract_callback_config(
            issue_dir=issue_dir,
            issue_name=issue_dir.name,
            workflow_id=_prepared_workflow_id(issue_dir),
        )
    except ValueError as exc:
        api = _contract_api(issue_dir)
        missing_error = (
            getattr(api, "ManagerContractMissingError", None) or api.DriverContractMissingError
        )

        if not isinstance(exc, missing_error) and (
            "requires a prepared workflow" not in str(exc)
        ):
            raise
        config = _load_config(manager_dir)
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
        session = _read_legacy_session(manager_dir, config)
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

    state_path = manager_dir / DISPATCH_STATE_FILENAME
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
            manager_dir,
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
                if attempt["index"] == index
                and attempt["cli"] == policy["cli"]
                and attempt["model"] == policy.get("model")
                and attempt["stage"] == "bootstrap"
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
        "route_status",
        "resolution_owner",
        "evidence_reason",
    }
    return {key: value for key, value in event.items() if key in allowed}


def _ensure_dispatch_event(
    manager_dir: Path,
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
    if state["schema_version"] == 2:
        updated["events"][event_id]["routing_chain"] = [
            {"cli": entry["cli"], "model": entry.get("model")} for entry in state["entries"]
        ]
    return _write_dispatch_state(manager_dir, updated)


def _classify_provider_failure(error: BaseException) -> str:
    if isinstance(error, _HostTransportUnavailable):
        # Checked before starting the proxy or sending any RPC; no delivery
        # could have occurred. Do not label this as a lost acknowledgement.
        return "conclusive_nonacceptance"
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
    manager_dir: Path,
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
    return _write_dispatch_state(manager_dir, updated)


def _finish_pending_attempt(
    manager_dir: Path,
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
    return _write_dispatch_state(manager_dir, updated)


def _callback_usage_sink(manager_dir: Path, event: dict[str, Any], repository_root: Path):
    """Pin the existing event-time iteration; callback attempt is not an iteration."""
    from cafe.core.usage import iteration_usage_sink

    step = event.get("step")
    occurred_at = event.get("occurred_at")
    if not isinstance(step, str) or Path(step).name != step or not isinstance(occurred_at, str):
        return None
    try:
        cutoff = datetime.fromisoformat(occurred_at)
        candidates = []
        for directory in sorted((manager_dir.parent / step).glob("iteration_[0-9]*")):
            target = directory / "iteration.json"
            if not target.exists():
                target = directory / "context.json"
            if not target.is_file():
                continue
            data = json.loads(target.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(data["timestamp"])
            if started <= cutoff and data.get("iteration") == int(directory.name.removeprefix("iteration_")):
                candidates.append(target)
        return iteration_usage_sink(repository_root, candidates[-1]) if candidates else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _acquire_v3_session(
    manager_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
    index: int,
    repository_root: Path,
    executor_factory=None,
    on_usage=None,
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
        manager_dir,
        state,
        event_id=event_id,
        index=index,
        stage="bootstrap",
    )
    executor = executor_factory(
        AgentConfig(
            name=_agent_name(manager_dir),
            cli=AgentCLI(entry["cli"]),
            model=entry.get("model"),
            clis=[],
            backup_clis=[],
        ),
        stream_output=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="cafe-event-bootstrap-") as temporary:
            result = ConversationTransport(executor).acquire_session(
                'say "HI"',
                on_usage=on_usage,
                allowed_tools=[],
                allowed_directories=[],
                execution_control=AgentExecutionControl(
                    working_directory=Path(temporary),
                    max_duration_seconds=numeric_limit("callback.attempt-budget", "duration", execution_context(consumers=["callback"]), expected_unit="seconds"),
                    max_output_bytes=numeric_limit("callback.attempt-budget", "output-bytes", execution_context(consumers=["callback"]), expected_unit="bytes"),
                    max_output_lines=numeric_limit("callback.attempt-budget", "output-lines", execution_context(consumers=["callback"]), expected_unit="lines"),
                ),
            )
    except Exception as exc:
        classification = _classify_provider_failure(exc)
        # A completed bootstrap with no identity never attempted event delivery.
        if getattr(exc, "error_type", None) == "missing_evidence":
            classification = "conclusive_nonacceptance"
        state = _finish_pending_attempt(
            manager_dir,
            state,
            event_id=event_id,
            status="failed" if classification == "conclusive_nonacceptance" else "ambiguous",
            outcome=classification,
            reason=getattr(exc, "error_type", None) or type(exc).__name__,
            recovery_pending=classification == "ambiguous",
        )
        return state, classification

    session_id = result.observed_session_id
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
    return _write_dispatch_state(manager_dir, updated), "acquired"


class EventManagerSessionStore(SessionStore):
    """Persist exactly one callback target session below its issue skill state."""

    def __init__(self, manager_dir: Path, *, workflow_id: str, cli: AgentCLI, model: str) -> None:
        self.manager_dir = manager_dir
        self.agent_name = _agent_name(manager_dir)
        self.workflow_id = workflow_id
        self.cli = cli
        self.model = model
        self._pending_session_id: str | None = None

    @property
    def path(self) -> Path:
        return self.manager_dir / SESSION_FILENAME

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
        if agent_name != self.agent_name or issue_name is not None or phase_name is not None:
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
            agent_name=self.agent_name,
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
            agent_name != self.agent_name
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


EventDriverSessionStore = EventManagerSessionStore


def _callback_to_step(event: dict[str, Any], *, repository_root: Path) -> str | None:
    """Project the event-time baton without changing callback or workflow authority."""
    issue = event.get("issue")
    if not isinstance(issue, str) or issue in {"", ".", ".."} or Path(issue).name != issue:
        return None
    audit = AuditEventStore(repository_root / ".cafe" / "issues" / issue)
    try:
        record = audit.read(event.get("workflow_id"), event.get("sequence"), bounded=True)
        if (record is None or record["event_id"] != event.get("event_id")
                or record["timestamp"] != event.get("occurred_at")
                or record["event_type"] != "workflow_event_callback_enqueued"):
            return None
        for sequence in range(record["sequence"] - 1, 0, -1):
            prior = audit.read(event["workflow_id"], sequence, bounded=True)
            if prior is None:
                continue
            change = prior["patch"].get("handoff_contract")
            if change is not None:
                handoff = change.get("after") if isinstance(change, dict) else None
            elif "baton" in prior:
                handoff = prior["baton"]
            elif prior["event_type"] == "workflow_paused":
                return "user"
            elif prior["event_type"] == "workflow_completed":
                return "done"
            elif prior["event_type"] == "transition":
                handoff = {"to_step": prior["data"].get("to")}
            else:
                continue
            target = handoff.get("to_step") if isinstance(handoff, dict) else None
            return target if isinstance(target, str) and target else None
    except (OSError, ValueError, TypeError):
        pass
    return None


def _callback_prompt(
    event: dict[str, Any], *, repository_root: Path, include_instructions: bool = False
) -> str:
    notice = json.dumps(
        {**event, "to_step": _callback_to_step(event, repository_root=repository_root)},
        ensure_ascii=False, sort_keys=True,
    )
    instructions = (
        "You are the event-driven CAFE workflow manager.",
        "This is an asynchronous wake notification, not a workflow advancement gate.",
        "Read the builtin use-cafe-workflow skill and follow its current confirmed contract.",
        "First inspect current durable state with cafe status/show before acting; "
        "the event may be stale.",
        "For the current task, run cafe task inspect <task-id> --json, then "
        "inspect_task_authority.py --issue-dir <issue-dir> --task-id <task-id> --json. "
        "Treat route_status, resolution_owner, and evidence_reason independently.",
        "Do not answer mandatory, user-required, permission, or capability tasks; only "
        "a user-facing manager turn may relay an explicit user-owned answer.",
        "You may complete a manager_confirmable task authorized by its explicit declaration "
        "or the confirmed overall need_clarification policy, only after verifying its "
        "confirmed contract and evidence. "
        "Explicit task ownership overrides the overall policy. "
        "Use complete_manager_task.py with the same assessment and inspected digests "
        "so authority is rechecked at durable completion. "
        "A clarification answer must stay within confirmed scope, constraints and authority "
        "and trigger no contract deviation; otherwise leave it for the user. Do not grant "
        "permissions/capabilities or wait for this callback.",
        "Do not assume you own a running background process. Only use an already "
        "reliable, authorized control path.",
    ) if include_instructions else ()
    return "\n".join(
        ("CAFE callback", *instructions, f"Repository: {repository_root}", f"Wake notice: {notice}")
    )


def _fallback_needs_instructions(state: dict[str, Any], index: int) -> bool:
    """Reuse verified delivery history across events and reordered routing chains."""
    if index == 0:
        return False
    entry = state["entries"][index]
    session_id = entry["session"]["id"]
    return not any(
        attempt.get("stage") == "delivery" and attempt.get("status") == "accepted"
        and attempt.get("cli") == entry["cli"] and attempt.get("model") == entry.get("model")
        and attempt.get("session_id") == session_id
        for event_state in _project_v3_events(state)
        for attempt in event_state["attempts"]
    )


def inspect_task_authority(issue_dir: Path, task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Inspect through the selected Manager or legacy Driver contract API."""
    if _manager_dir(issue_dir).name == "driver":
        from cafe.driver.task_inspection import inspect_task_authority as inspect
    else:
        from cafe.manager.task_inspection import inspect_task_authority as inspect
    return inspect(issue_dir, task_id, **kwargs)


def _with_current_task_authority(
    event: dict[str, Any], *, issue_dir: Path, repository_root: Path
) -> dict[str, Any]:
    task_id = event.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return event
    try:
        detail = TaskInboxService(repository_root / ".cafe").inspect_read_only(task_id)
        if detail.issue != issue_dir.name or detail.status != "pending":
            return event
        facts = inspect_task_authority(issue_dir, task_id)
    except (TaskInboxError, OSError, ValueError):
        return {
            **event,
            "route_status": event.get("trigger") or "unknown",
            "resolution_owner": "user_required",
            "evidence_reason": "authority_inspection_unavailable",
        }
    return {
        **event,
        **{key: facts[key] for key in ("route_status", "resolution_owner", "evidence_reason")},
    }


class _HostRPCError(RuntimeError):
    """A daemon rejection; never include provider output in durable errors."""


class _HostTransportUnavailable(ValueError):
    """The existing host endpoint is absent before any delivery is attempted."""

    error_type = "host_control_socket_unavailable"


def _require_host_control_socket() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    endpoint = home / "app-server-control" / "app-server-control.sock"
    try:
        endpoint_metadata = endpoint.lstat()
        # Managed Codex daemons publish the control path as an owned symlink
        # to their Unix socket. Validate the resolved socket, not just the link.
        metadata = endpoint.stat()
        available = (
            stat.S_ISSOCK(metadata.st_mode)
            and hasattr(os, "getuid")
            and metadata.st_uid == os.getuid()
            and endpoint_metadata.st_uid == os.getuid()
        )
    except OSError:
        available = False
    if not available:
        raise _HostTransportUnavailable(
            "The bound Codex App has no usable daemon control socket. "
            "Local App stdio sessions cannot receive this callback. "
            "Use an explicitly confirmed attached Manager mode, or connect the App "
            "to a supported existing daemon before restoring event-driven mode."
        )
    return endpoint


def validate_bound_host_transport(issue_dir: Path) -> None:
    """Check a bound host's endpoint without creating a session or sending input."""
    status = read_status(issue_dir)
    entries = status.get("entries", [])
    # Later confirmed providers remain usable without the primary host socket.
    # Runtime routing handles this conclusive failure through the same chain.
    if len(entries) != 1 or status.get("active_index", 0) != 0:
        return
    first = entries[0]
    session = first.get("acquisition", {}).get("session")
    if first.get("cli") == "codex" and isinstance(session, dict):
        if session.get("source") == "host_session":
            _require_host_control_socket()


class _HostConnection:
    """Bounded JSON-RPC over a proxy to the existing daemon, without approvals."""

    def __init__(self, process: Any, *, timeout: float = 30) -> None:
        self.process = process
        self.deadline = time.monotonic() + timeout
        self.buffer = bytearray()
        self.received = 0
        self.sequence = 0
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        # The control socket (and its byte proxy) uses RFC 6455, not the
        # newline-delimited JSON transport used by standalone app-server.
        key = base64.b64encode(os.urandom(16)).decode()
        self._write(
            (
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {key}\r\n\r\n"
            ).encode()
        )
        while b"\r\n\r\n" not in self.buffer:
            self._receive()
            if len(self.buffer) > 16 * 1024:
                raise ValueError("Codex host handshake limit exceeded")
        headers, _, rest = self.buffer.partition(b"\r\n\r\n")
        self.buffer = bytearray(rest)
        lines = headers.decode("ascii").split("\r\n")
        fields = {name.lower(): value for name, value in (line.split(":", 1) for line in lines[1:])}
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if (
            lines[0].split()[1] != "101"
            or fields.get("sec-websocket-accept", "").strip() != accept
            or fields.get("upgrade", "").strip().lower() != "websocket"
            or "upgrade" not in fields.get("connection", "").lower()
        ):
            raise ConnectionError("Invalid Codex host WebSocket handshake")

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex host transport timed out")
        return remaining

    def _write(self, payload: bytes) -> None:
        data = memoryview(payload)
        while data:
            if not select.select([], [self.process.stdin], [], self._remaining())[1]:
                raise TimeoutError("Codex host transport timed out")
            try:
                written = os.write(self.process.stdin.fileno(), data)
            except BlockingIOError:
                continue
            if not written:
                raise ConnectionError("Codex host proxy closed")
            data = data[written:]

    def _receive(self) -> None:
        if not select.select([self.process.stdout], [], [], self._remaining())[0]:
            raise TimeoutError("Codex host transport timed out")
        try:
            chunk = os.read(self.process.stdout.fileno(), 64 * 1024)
        except BlockingIOError:
            return
        if not chunk:
            raise ConnectionError("Codex host proxy closed")
        self.received += len(chunk)
        if self.received > 2 * 1024 * 1024:
            raise ValueError("Codex host transport output limit exceeded")
        self.buffer.extend(chunk)

    def _read(self, count: int) -> bytes:
        if count > 2 * 1024 * 1024:
            raise ValueError("Codex host frame limit exceeded")
        while len(self.buffer) < count:
            self._receive()
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def _frame(self, payload: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        length = len(payload)
        prefix = bytes([0x80 | opcode])
        if length < 126:
            prefix += bytes([0x80 | length])
        elif length < 65536:
            prefix += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            prefix += bytes([0x80 | 127]) + struct.pack("!Q", length)
        self._write(
            prefix + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        )

    def send(self, message: dict[str, Any]) -> None:
        self._frame(json.dumps(message).encode())

    def _message(self) -> dict[str, Any]:
        payload = bytearray()
        fragmented = False
        while True:
            self._remaining()
            first, second = self._read(2)
            opcode = first & 15
            if first & 0x70 or second & 0x80:
                raise ValueError("Invalid Codex host WebSocket frame")
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            if opcode >= 8 and (length > 125 or not first & 0x80):
                raise ValueError("Invalid Codex host control frame")
            chunk = self._read(length)
            if opcode == 8:
                raise ConnectionError("Codex host proxy closed")
            if opcode == 9:
                self._frame(chunk, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode != (0 if fragmented else 1):
                raise ValueError("Invalid Codex host message frame")
            payload.extend(chunk)
            if first & 0x80:
                message = json.loads(payload)
                if not isinstance(message, dict):
                    raise ValueError("Invalid Codex host response")
                return message
            fragmented = True

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._message()
            # Notifications and server requests belong to the original UI. This
            # connection never answers permission, capability or user questions.
            if "method" in message:
                continue
            if message.get("id") != request_id:
                raise ValueError("Unexpected Codex host response identity")
            if "error" in message:
                raise _HostRPCError(f"Codex host rejected {method}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ValueError("Invalid Codex host response result")
            return result


def _host_thread(connection: _HostConnection, thread_id: str) -> dict[str, Any]:
    result = connection.request("thread/read", {"threadId": thread_id})
    thread = result.get("thread")
    if not isinstance(thread, dict) or thread.get("id") != thread_id:
        raise ValueError("Codex host returned a different thread")
    status = thread.get("status")
    if not isinstance(status, dict) or status.get("type") not in {
        "notLoaded",
        "idle",
        "active",
        "systemError",
    }:
        raise ValueError("Unknown Codex host thread status")
    return thread


def _queue_host_callback(
    prompt: str,
    *,
    thread_id: str,
    model: str | None,
    repository_root: Path,
) -> None:
    """Load and wake the bound thread through the already running host daemon."""
    _require_host_control_socket()
    # No new daemon, session, config, cwd, model or permission override. The
    # repository root is already in the event prompt; the host owns its cwd.
    process = subprocess.Popen(
        ["codex", "app-server", "proxy"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        connection = _HostConnection(process)
        connection.request(
            "initialize",
            {
                "clientInfo": {"name": "cafe_callback", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        connection.send({"method": "initialized", "params": {}})
        thread = _host_thread(connection, thread_id)
        if thread.get("ephemeral") or thread.get("canAcceptDirectInput") is False:
            raise _HostRPCError("Codex host thread cannot accept queued input")
        if model is not None and thread.get("model") != model:
            raise _HostRPCError("Codex host model differs from confirmed binding")
        latest = connection.request(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            },
        ).get("data")
        if not isinstance(latest, list) or any(not isinstance(t, dict) for t in latest):
            raise ValueError("Invalid Codex host turn history")
        if thread["status"]["type"] == "systemError" or (
            thread["status"]["type"] != "active"
            and latest
            and latest[0].get("status") == "interrupted"
        ):
            raise _HostRPCError("Codex host thread requires explicit user recovery")
        if thread["status"]["type"] == "notLoaded":
            resumed = connection.request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "excludeTurns": True,
                },
            ).get("thread")
            if not isinstance(resumed, dict) or resumed.get("id") != thread_id:
                raise ValueError("Codex host resumed a different thread")
            thread = _host_thread(connection, thread_id)
            if thread["status"]["type"] not in {"idle", "active"}:
                raise _HostRPCError("Codex host thread did not become available")
        client_id = "cafe-" + hashlib.sha256(prompt.encode()).hexdigest()
        queued = connection.request(
            "thread/queue/add",
            {
                "threadId": thread_id,
                "clientUserMessageId": client_id,
                "input": [{"type": "text", "text": prompt}],
            },
        ).get("queuedSubmission")
        if (
            not isinstance(queued, dict)
            or not isinstance(queued.get("id"), str)
            or not queued["id"]
            or queued.get("clientUserMessageId") != client_id
            or queued.get("input") != [{"type": "text", "text": prompt, "text_elements": []}]
            and queued.get("input") != [{"type": "text", "text": prompt}]
        ):
            raise ValueError("Invalid Codex host queue acknowledgement")
        # queue/add wakes the loaded thread through the daemon's dispatcher,
        # which preserves FIFO and excludes user-interrupted threads. Never use
        # queue/start here: its explicit selection can overtake a concurrently
        # reordered user message or override a stop after our status snapshot.
        thread = _host_thread(connection, thread_id)
        if thread["status"]["type"] not in {"idle", "active"}:
            raise RuntimeError("Codex host unloaded or failed after queue acceptance")
    finally:
        # Only terminate our stdio proxy. Never unload/interrupt the thread or
        # stop the shared app-server when this short-lived connection closes.
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def _accept_delivery(
    manager_dir: Path,
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
    return _write_dispatch_state(manager_dir, updated)


def _deliver_v3_callback(
    manager_dir: Path,
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    index: int,
    repository_root: Path,
    executor_factory=None,
    on_usage=None,
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
        manager_dir,
        state,
        event_id=event_id,
        index=index,
        stage="delivery",
    )
    session_id = session["id"]
    acceptance_persisted = False
    acceptance_write_failed = False
    usage_write_error = None

    def persist_usage(usage) -> None:
        nonlocal usage_write_error
        try:
            on_usage(usage)
        except Exception as exc:
            usage_write_error = exc
            raise

    def persist_acceptance() -> None:
        nonlocal state, acceptance_persisted, acceptance_write_failed
        if acceptance_persisted:
            return
        try:
            state = _accept_delivery(
                manager_dir,
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
        if (
            index == 0
            and entry["cli"] == AgentCLI.CODEX.value
            and session.get("source") == "host_session"
        ):
            _queue_host_callback(
                _callback_prompt(event, repository_root=repository_root),
                thread_id=session_id,
                model=entry.get("model"),
                repository_root=repository_root,
            )
            accepted = True
            reported_session_id = session_id
        else:
            executor = executor_factory(
                AgentConfig(
                    name=_agent_name(manager_dir),
                    cli=AgentCLI(entry["cli"]),
                    model=entry.get("model"),
                    session_id=session_id,
                    clis=[],
                    backup_clis=[],
                ),
                stream_output=False,
            )
            result = ConversationTransport(executor).deliver_to_exact_session(
                _callback_prompt(
                    event, repository_root=repository_root,
                    include_instructions=_fallback_needs_instructions(state, index),
                ),
                session_id=session_id,
                delivery_id=event_id,
                on_usage=persist_usage if on_usage is not None else None,
                on_acceptance=persist_acceptance,
                allowed_tools=["Read", "Grep", "Glob", "Bash"],
                allowed_directories=[str(repository_root)],
                execution_control=AgentExecutionControl(
                    max_duration_seconds=numeric_limit("callback.attempt-budget", "duration", execution_context(consumers=["callback"]), expected_unit="seconds"),
                    max_output_bytes=numeric_limit("callback.attempt-budget", "output-bytes", execution_context(consumers=["callback"]), expected_unit="bytes"),
                    max_output_lines=numeric_limit("callback.attempt-budget", "output-lines", execution_context(consumers=["callback"]), expected_unit="lines"),
                ),
            )
            accepted = result.accepted is True
            reported_session_id = result.observed_session_id
    except Exception as exc:
        if acceptance_write_failed or exc is usage_write_error:
            raise
        if acceptance_persisted:
            return state, "accepted"
        classification = _classify_provider_failure(exc)
        state = _finish_pending_attempt(
            manager_dir,
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
                manager_dir,
                state,
                event_id=event_id,
                index=index,
                session_id=session_id,
            ),
            "accepted",
        )

    conflicting = reported_session_id is not None and reported_session_id != session_id
    classification = "ambiguous" if accepted or conflicting else "conclusive_nonacceptance"
    state = _finish_pending_attempt(
        manager_dir,
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
    manager_dir: Path,
    state: dict[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    event_state = updated["events"][event_id]
    event_state["status"] = "exhausted"
    event_state["recovery_pending"] = True
    return _write_dispatch_state(manager_dir, updated)


def _run_v3_callback(
    manager_dir: Path,
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    repository_root: Path,
    executor_factory=None,
) -> dict[str, Any]:
    """Run serial acquisition/delivery attempts until first acceptance or recovery."""
    if executor_factory is None:
        executor_factory = AgentExecutor
    on_usage = _callback_usage_sink(manager_dir, event, repository_root)
    event_id = event["event_id"]
    event_state = state["events"][event_id]
    if event_state["status"] in {"accepted", "exhausted", "recovery_pending"}:
        return state
    attempts = event_state["attempts"]
    if attempts and attempts[-1].get("status") == "pending":
        return state
    if state["schema_version"] == 2:
        current_route = [
            {"cli": entry["cli"], "model": entry.get("model")} for entry in state["entries"]
        ]
        if event_state.get("routing_chain") != current_route:
            if attempts:
                return state
            updated = copy.deepcopy(state)
            updated["events"][event_id]["routing_chain"] = current_route
            updated["events"][event_id]["starting_index"] = state["active_index"]
            state = _write_dispatch_state(manager_dir, updated)
            event_state = state["events"][event_id]
    if attempts and attempts[-1].get("status") == "acquired":
        acquired = attempts[-1]
        session = state["entries"][acquired["index"]]["session"]
        if not isinstance(session, dict) or session["id"] != acquired["session_id"]:
            return state
    index = event_state["starting_index"]
    if attempts:
        last = attempts[-1]
        index = last["index"]
        if last.get("outcome") == "conclusive_nonacceptance":
            index += 1

    while index < len(state["entries"]):
        state, acquisition = _acquire_v3_session(
            manager_dir,
            state,
            event_id=event_id,
            index=index,
            repository_root=repository_root,
            executor_factory=executor_factory,
            on_usage=on_usage,
        )
        if acquisition == "ambiguous":
            return state
        if acquisition == "conclusive_nonacceptance":
            index += 1
            continue

        state, delivery = _deliver_v3_callback(
            manager_dir,
            state,
            event,
            index=index,
            repository_root=repository_root,
            executor_factory=executor_factory,
            on_usage=on_usage,
        )
        if delivery in {"accepted", "ambiguous"}:
            return state
        index += 1

    return _exhaust_event(manager_dir, state, event_id=event_id)


def run_callback(event: dict[str, Any], *, repository_root: Path) -> None:
    issue_name = _validated_issue_name(event)
    workflow_id = event.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("workflow event callback has an invalid workflow ID")
    issue_dir = repository_root / ".cafe" / "issues" / issue_name
    manager_dir = _manager_dir(issue_dir)
    with _session_lock(manager_dir):
        config = _contract_callback_config(
            issue_dir=issue_dir,
            issue_name=issue_name,
            workflow_id=workflow_id,
        )
        if config is None:
            return
        from cafe.core.blackboard import BlackboardStore

        store = BlackboardStore(issue_dir)
        if _prepared_workflow_id(issue_dir) != workflow_id:
            raise StaleWorkflowEventError("workflow event callback is stale")
        if config["schema_version"] in {3, _CONTRACT_CALLBACK_CONFIG_SCHEMA}:
            store.validate_workflow_callback_event(workflow_id, event)
            event = _with_current_task_authority(
                event, issue_dir=issue_dir, repository_root=repository_root
            )
            state = _load_or_initialize_dispatch_state(
                manager_dir,
                workflow_id=workflow_id,
                config=config,
            )
            state = _ensure_dispatch_event(manager_dir, state, event)
            result = _run_v3_callback(
                manager_dir,
                state,
                event,
                repository_root=repository_root,
            )
            event_state = result["events"][event["event_id"]]
            if event_state["status"] in {"accepted", "exhausted", "recovery_pending"} or (
                event_state["attempts"] and event_state["attempts"][-1].get("status") == "pending"
            ):
                store.audit.close(workflow_id, event["sequence"])
            return
        event = _with_current_task_authority(
            event, issue_dir=issue_dir, repository_root=repository_root
        )
        cli = AgentCLI(config["cli"])
        store = EventManagerSessionStore(
            manager_dir,
            workflow_id=workflow_id,
            cli=cli,
            model=config["model"],
        )
        existing = store.load_session(store.agent_name, cli)
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
                store.save_session(store.agent_name, cli, host_thread_id)
            store.commit()
            return
        executor = AgentExecutor(
            AgentConfig(name=store.agent_name, cli=cli, model=config["model"],
                        session_id=existing.session_id if existing is not None else None,
                        clis=[], backup_clis=[]),
            stream_output=False,
        )
        responses = []
        ConversationTransport(executor).run_one_shot(
            _callback_prompt(event, repository_root=repository_root),
            allowed_tools=["Read", "Grep", "Glob", "Bash"],
            allowed_directories=[str(repository_root)],
            on_response=responses.append,
            on_usage=_callback_usage_sink(manager_dir, event, repository_root),
        )
        response = responses[-1]
        # Legacy caller persistence remains compatible with ordinary CLI session discovery.
        # It is not promoted into verified exact-delivery evidence.
        if (
            response.cli != cli
            or (response.model is not None and response.model != config["model"])
            or not response.session_id
            or (existing is not None and response.session_id != existing.session_id)
        ):
            raise ValueError("event-driven manager identity mismatch")
        store.save_session(store.agent_name, cli, response.session_id)

        store.commit()


def _stored_conversation_locale(issue_dir: Path) -> str:
    """Read the workflow's stored conversation locale for one background message.

    A background notification must not re-resolve the language. An absent or
    unreadable record falls back to the documented English default for this
    message only and never writes anything back.
    """
    try:
        raw = json.loads(
            _read_bounded_text(issue_dir / "blackboard.json", label="blackboard.json")
        )
    except (OSError, ValueError):
        return DEFAULT_CONVERSATION_LOCALE
    if not isinstance(raw, dict):
        return DEFAULT_CONVERSATION_LOCALE
    stored = raw.get("conversation_locale")
    return stored if isinstance(stored, str) and stored.strip() else DEFAULT_CONVERSATION_LOCALE


def _notify_callback_failure(
    event: dict[str, Any], *, repository_root: Path, error: Exception
) -> None:
    """Best-effort out-of-band notice when the primary callback path fails."""
    issue = _validated_issue_name(event)
    step = event.get("step")
    event_type = event.get("event_type")
    issue_dir = repository_root / ".cafe" / "issues" / issue
    manager_dir = _manager_dir(issue_dir)
    notification_root = resolve_human_task_notification_repository_root(issue_dir)
    error_code = _callback_error_code(error)
    notification_key = _callback_failure_key(event, error_code=error_code)
    with _session_lock(manager_dir):
        records = _load_callback_failure_notifications(manager_dir)
        existing = records.get(notification_key)
        if isinstance(existing, dict) and existing.get("outcome") in {
            "sent", "disabled", "pending"
        }:
            return
        records[notification_key] = {
            "occurred_at": _now(),
            "outcome": "pending",
            "error_code": error_code,
            "notification_code": "notification_pending",
        }
        _write_callback_failure_notifications(manager_dir, records)
        settings = load_human_task_notification_settings()
        if not settings.enabled:
            records[notification_key] = {
                "occurred_at": _now(),
                "outcome": "disabled",
                "error_code": error_code,
                "notification_code": settings.code,
            }
            _write_callback_failure_notifications(manager_dir, records)
            return
        locale = _stored_conversation_locale(issue_dir)
        presentation = None
        try:
            from cafe.playbooks.loader import PlaybookLoader
            from cafe.skills.loader import SkillLoader
            from cafe.skills.notification_copy import resolve_step_notification_presentation

            state = json.loads(
                _read_bounded_text(issue_dir / "blackboard.json", label="blackboard.json")
            )
            playbook_id = state.get("playbook_id") if isinstance(state, dict) else None
            if isinstance(playbook_id, str) and playbook_id:
                playbook = PlaybookLoader(
                    project_root=repository_root, resolve_presentation=False
                ).load(playbook_id)
                iteration = 1
                if isinstance(step, str) and step in playbook["steps"] and Path(step).name == step:
                    directories = sorted(
                        path for path in (issue_dir / step).glob("iteration_*") if path.is_dir()
                    )
                    if directories:
                        iteration = int(directories[-1].name.removeprefix("iteration_"))
                presentation = resolve_step_notification_presentation(
                    playbook_data=playbook,
                    step_name=step if isinstance(step, str) else "",
                    locale=locale,
                    iteration=iteration,
                    skill_loader=SkillLoader(
                        project_root=repository_root, resolve_presentation=False
                    ),
                )
        except (OSError, TypeError, ValueError, LookupError):
            # A failure notice must remain available when the original declaration
            # or its presentation files caused the callback failure.
            presentation = None
        message = build_workflow_callback_failure_message(
            repository=notification_root.name,
            issue=issue,
            step=step if isinstance(step, str) else "",
            event_type=event_type if isinstance(event_type, str) else "",
            error_code=error_code,
            locale=locale,
            presentation=presentation,
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
            _write_callback_failure_notifications(manager_dir, records)
            raise
        records[notification_key] = {
            "occurred_at": _now(),
            "outcome": "sent",
            "error_code": error_code,
            "notification_code": "slack_notification_sent",
        }
        _write_callback_failure_notifications(manager_dir, records)


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


def _load_callback_failure_notifications(manager_dir: Path) -> dict[str, dict[str, str]]:
    """Load the bounded, workflow-bound callback failure authority."""
    path = manager_dir / FAILURE_NOTIFICATIONS_FILENAME
    if not path.exists():
        return {}
    try:
        raw = json.loads(_read_bounded_text(path, label="callback failure notifications"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("callback failure authority is unreadable") from exc
    if (not isinstance(raw, dict) or raw.get("schema_version") != 1
            or raw.get("workflow_id") != _prepared_workflow_id(manager_dir.parent)):
        raise ValueError("callback failure authority identity is invalid")
    records = raw.get("records")
    if not isinstance(records, dict):
        raise ValueError("callback failure authority records are invalid")
    return {
        key: value
        for key, value in records.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _write_callback_failure_notifications(
    manager_dir: Path, records: dict[str, dict[str, str]]
) -> None:
    """Persist secret-free callback notification outcomes for diagnosis."""
    # JSON persistence sorts hash keys; retain by occurrence time instead.
    bounded_records = dict(sorted(
        records.items(),
        key=lambda item: datetime.fromisoformat(item[1]["occurred_at"]),
    )[-MAX_FAILURE_NOTIFICATIONS:])
    payload = {"schema_version": 1, "workflow_id": _prepared_workflow_id(manager_dir.parent),
               "records": bounded_records}
    _atomic_write(
        manager_dir / FAILURE_NOTIFICATIONS_FILENAME,
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
