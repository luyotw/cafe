"""Invocation deltas for Codex's resumed, session-cumulative stdout counters."""

import json
import os
import stat
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from cafe.core.types import TokenUsage

_COUNTERS = frozenset(TokenUsage.model_fields) - {
    "duration_ms",
    "duration_api_ms",
    "turn_usages",
    "cost_records",
}
_TAIL_BYTES = 2 * 1024 * 1024
# Native agent messages can exceed 256 KiB before the next usage record.
# Keep a finite line bound while accepting those normal journal entries.
_LINE_BYTES = 512 * 1024
_REQUEST_BYTES = 16 * 1024 * 1024


def _counters(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid native token counters")
    result = {}
    for key, value in raw.items():
        key = "cache_read_input_tokens" if key == "cached_input_tokens" else key
        if key not in _COUNTERS:
            continue
        if type(value) is not int or value < 0:
            raise ValueError("invalid native token counter")
        if key in result and result[key] != value:
            raise ValueError("conflicting native token counters")
        result[key] = value
    if not {"input_tokens", "output_tokens"}.issubset(result):
        raise ValueError("native token baseline missing")
    return result


def _find_journal(home, session):
    """Search filenames only, with bounded traversal and no symlink descent."""
    matches = []
    visited = 0
    for root in (home / "sessions", home / "archived_sessions"):
        pending = [root]
        while pending:
            directory = pending.pop()
            if directory.is_symlink():
                raise ValueError("ambiguous native session directory")
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 65536:
                            raise ValueError("native session search exceeds bound")
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.name.endswith(f"-{session}.jsonl"):
                            matches.append(Path(entry.path))
            except FileNotFoundError:
                continue
    if len(matches) != 1:
        raise ValueError("native session source is missing or ambiguous")
    return matches[0]


def _open_journal(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    handle = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise ValueError("native session source is not a regular file")
    return handle


def _baseline(home, session):
    journal = _find_journal(home, session)
    with _open_journal(journal) as handle:
        info = os.fstat(handle.fileno())
        first = handle.readline(_LINE_BYTES + 1)
        if len(first) > _LINE_BYTES or not first.endswith(b"\n"):
            raise ValueError("invalid native session identity")
        metadata = json.loads(first)
        if (
            not isinstance(metadata, dict)
            or metadata.get("type") != "session_meta"
            or not isinstance(metadata.get("payload"), dict)
            or metadata["payload"].get("id") != session
        ):
            raise ValueError("native session identity mismatch")
        offset = max(len(first), info.st_size - _TAIL_BYTES)
        handle.seek(offset)
        if offset > len(first):
            handle.readline(_LINE_BYTES + 1)  # Discard the first truncated record.
        latest = None
        while handle.tell() < info.st_size:
            line = handle.readline(min(_LINE_BYTES + 1, info.st_size - handle.tell()))
            if len(line) > _LINE_BYTES or not line.endswith(b"\n"):
                raise ValueError("native accounting record exceeds bound")
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("invalid native accounting record")
            payload = record.get("payload")
            if record.get("type") == "event_msg" and isinstance(payload, dict):
                if payload.get("type") == "token_count" and payload.get("info") is not None:
                    latest = _counters(payload["info"].get("total_token_usage"))
        if latest is None:
            raise ValueError("native token baseline missing")
        anchor_size = min(info.st_size, 128)
        handle.seek(info.st_size - anchor_size)
        anchor = sha256(handle.read(anchor_size)).digest()
        return journal, (info.st_dev, info.st_ino), info.st_size, anchor, latest


def prepare_resumed_usage(command, environment, *, selected_session=None):
    """Snapshot the exact resume source before launch, then subtract its totals.

    Without a verified baseline, cumulative tokens cannot be attributed to this
    call. Keep invocation durations, but leave those token/cost fields unknown.
    No model identity or USD price is inferred from requested configuration.
    """
    try:
        index = command.index("exec")
        if command[index + 1] != "resume":
            return None
        # Invocation controls may insert native options between resume and ID.
        # The configured exact ID remains the selected positional argument.
        session = selected_session or command[index + 2]
    except (ValueError, IndexError):
        return None
    baseline = None
    try:
        if session not in command[index + 2 :]:
            raise ValueError("selected resume session is absent")
        if str(uuid.UUID(session)) != session:
            raise ValueError("resume must identify an exact native session")
        home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        baseline = _baseline(home, session)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        pass

    def project(usage, lines):
        known = usage.model_dump(exclude_unset=True)
        durations = {key: value for key, value in known.items() if key.startswith("duration_")}
        try:
            if baseline is None:
                raise ValueError("no verified native baseline")
            journal, identity, size, anchor, prior = baseline
            with _open_journal(journal) as handle:
                info = os.fstat(handle.fileno())
                if (info.st_dev, info.st_ino) != identity or info.st_size < size:
                    raise ValueError("native session source changed")
                anchor_size = min(size, 128)
                handle.seek(size - anchor_size)
                if sha256(handle.read(anchor_size)).digest() != anchor:
                    raise ValueError("native session baseline changed")
            records = [json.loads(line) for line in lines]
            sessions = {
                record.get("thread_id")
                for record in records
                if isinstance(record, dict) and record.get("type") == "thread.started"
            }
            if sessions != {session}:
                raise ValueError("stdout session does not match native baseline")
            totals = {key: value for key, value in known.items() if key in prior}
            if any(value < prior[key] for key, value in totals.items()):
                raise ValueError("session counters decreased")
            delta = {key: value - prior[key] for key, value in totals.items()}
            turns = []
            previous = prior
            for record in records:
                if not isinstance(record, dict) or record.get("type") != "turn.completed":
                    continue
                raw = record.get("usage")
                if not raw:
                    continue
                current = _counters(raw)
                if any(current[key] < previous[key] for key in current.keys() & previous.keys()):
                    raise ValueError("session counters decreased between turns")
                turns.append(
                    {
                        "turn": len(turns) + 1,
                        **{
                            key: value - previous[key]
                            for key, value in current.items()
                            if key in previous
                        },
                    }
                )
                previous = current
            return TokenUsage(**durations, **delta, **({"turn_usages": turns} if delta else {}))
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return TokenUsage(**durations)

    project.native_baseline = baseline
    return project


def reconcile_request_usage(observations, known, *, baseline=None):
    """Verify each native request against its cumulative delta and owned totals.

    The caller supplies only observations from its verified range, with the
    matching counter source and actual model. This also supports forked child
    per-thread counters without assuming stdout or an initial exec session.
    """
    previous = _counters(baseline) if baseline is not None else None
    requests = []
    for model, cumulative, last_usage in observations:
        current = _counters(cumulative)
        if previous == current:
            continue
        last = _counters(last_usage)
        if previous is None:
            previous = dict.fromkeys(current, 0)
        if (current.keys() != previous.keys() or current.keys() != last.keys()
                or any(current[key] - previous[key] != last[key] for key in current)
                or not isinstance(model, str) or not model.strip() or len(model) > 512):
            raise ValueError("native request counters/model do not reconcile")
        requests.append(dict(kind="codex_request", model=model, usage=last,
                             model_source="native_journal"))
        previous = current
    required = {"input_tokens", "output_tokens", "cache_read_input_tokens"}
    keys = known.keys() & _COUNTERS - {"total_cost_usd"}
    if (not requests or not required <= keys
            or any(key not in request["usage"] for key in keys for request in requests)
            or any(sum(request["usage"][key] for request in requests) != known[key]
                   for key in keys)):
        raise ValueError("native requests do not cover invocation usage")
    return requests


def prepare_request_usage(command, environment, *, selected_session=None):
    """Reconcile native request counters with this fresh/resumed invocation.

    A CLI turn can contain many API requests. Only exact, complete native
    request coverage replaces the turn list; unavailable evidence leaves the
    existing aggregate accounting intact. Never retain transcript content.
    """
    resumed = prepare_resumed_usage(command, environment, selected_session=selected_session)
    started = datetime.now(timezone.utc)
    home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")

    def project(usage, lines):
        usage = resumed(usage, lines) if resumed is not None else usage
        try:
            sessions = {
                record.get("thread_id")
                for line in lines
                if isinstance(record := json.loads(line), dict)
                and record.get("type") == "thread.started"
            }
            if len(sessions) != 1:
                raise ValueError("ambiguous native request session")
            session = sessions.pop()
            if str(uuid.UUID(session)) != session:
                raise ValueError("invalid native request session")
            baseline = resumed.native_baseline if resumed is not None else None
            if resumed is not None and baseline is None:
                raise ValueError("unverified resume baseline")
            path = baseline[0] if baseline else _find_journal(home, session)
            with _open_journal(path) as handle:
                size = os.fstat(handle.fileno()).st_size
                first = handle.readline(_LINE_BYTES + 1)
                if len(first) > _LINE_BYTES or not first.endswith(b"\n"):
                    raise ValueError("invalid native request metadata")
                metadata = json.loads(first)
                meta = metadata.get("payload", {})
                if metadata.get("type") != "session_meta" or meta.get("id") != session:
                    raise ValueError("invalid native request identity")
                # CLI versions are diagnostic metadata. Compatibility is established
                # below by reconciling every request against the invocation counters.
                if baseline:
                    offset, previous = baseline[2], baseline[4]
                else:
                    born = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
                    if (born.tzinfo is None or born < started or meta.get("source") != "exec"
                            or meta.get("forked_from_id")):
                        raise ValueError("unverified initial-zero request baseline")
                    offset, previous = len(first), None
                if size - offset > _REQUEST_BYTES:
                    raise ValueError("native request range exceeds bound")
                handle.seek(offset)
                observations, model = [], None
                while handle.tell() < size:
                    line = handle.readline(min(_LINE_BYTES + 1, size - handle.tell()))
                    if len(line) > _LINE_BYTES or not line.endswith(b"\n"):
                        raise ValueError("native request line exceeds bound")
                    row = json.loads(line)
                    payload = row.get("payload", {})
                    if row.get("type") == "event_msg" and payload.get("type") == "task_started":
                        model = None
                    if row.get("type") == "turn_context":
                        model = payload.get("model")
                    if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    if info is None:
                        continue
                    observations.append((model, info["total_token_usage"],
                                         info["last_token_usage"]))
            known = usage.model_dump(exclude_unset=True)
            requests = reconcile_request_usage(observations, known, baseline=previous)
            return usage.model_copy(update={"turn_usages": requests})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return usage

    return project


def prepare_model_reader(environment):
    """Read model identity only from this invocation's exact native journal.

    Stdout often omits the model. Never replace it with requested configuration
    or a rolling alias. Large, missing or mixed-model evidence stays unknown.
    """
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc)
    home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")

    def read(session):
        try:
            if str(uuid.UUID(session)) != session:
                return None
            path = _find_journal(home, session)
            with _open_journal(path) as handle:
                first = handle.readline(_LINE_BYTES + 1)
                metadata = json.loads(first)
                if (metadata.get("type") != "session_meta"
                        or metadata.get("payload", {}).get("id") != session):
                    return None
                size = os.fstat(handle.fileno()).st_size
                offset = max(len(first), size - _TAIL_BYTES)
                handle.seek(offset)
                if offset > len(first):
                    handle.readline(_LINE_BYTES + 1)
                models = set()
                boundary_seen = offset == len(first)
                while handle.tell() < size:
                    line = handle.readline(min(_LINE_BYTES + 1, size - handle.tell()))
                    if len(line) > _LINE_BYTES or not line.endswith(b"\n"):
                        return None
                    record = json.loads(line)
                    timestamp = record.get("timestamp")
                    if not timestamp:
                        continue
                    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if instant.tzinfo is None:
                        return None
                    if instant < started_at:
                        boundary_seen = True
                        continue
                    if record.get("type") == "turn_context":
                        model = record.get("payload", {}).get("model")
                        if not isinstance(model, str) or not model.strip() or len(model) > 512:
                            return None
                        models.add(model)
                return next(iter(models)) if boundary_seen and len(models) == 1 else None
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return None

    return read
