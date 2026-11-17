"""Recover Codex App Manager usage for the closeout report, in memory."""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from cafe.agents.cli.codex_usage import (
    _counters,
    _find_journal,
    _open_journal,
    reconcile_request_usage,
)
from cafe.core.cost import account_cost, combine_cost_summaries, summarize_cost
from cafe.core.pricing_sources import pricing_store
from cafe.core.types import TokenUsage

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024


@dataclass
class Turn:
    started_at: datetime
    ended: bool = False
    models: set[str] = field(default_factory=set)
    callbacks: list[dict] = field(default_factory=list)
    other_messages: int = 0
    requests: list[tuple[dict, dict]] = field(default_factory=list)
    legacy_mismatch: bool = False


def _time(value):
    if not isinstance(value, str):
        raise ValueError("host_accounting_invalid_timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("host_accounting_timestamp_missing_timezone")
    return stamp


def _user_message(turn, content):
    if not isinstance(content, list) or any(not isinstance(item, dict) for item in content):
        raise ValueError("host_accounting_invalid_message")
    if len(content) != 1 or not isinstance(content[0].get("text"), str):
        turn.other_messages += 1
        return
    message = content[0]["text"]
    if message.startswith(("CAFE callback\n", "CAFE callback\r\n")):
        _, separator, notice = message.rpartition("\nWake notice: ")
        try:
            event = json.loads(notice) if separator else None
        except ValueError:
            event = None
        turn.callbacks.append(event if isinstance(event, dict) else {})
    elif not message.startswith(("<environment_context>", "<skill>")):
        turn.other_messages += 1


def _read_turns(home, session, cutoff):
    if str(uuid.UUID(session)) != session:
        raise ValueError("host_accounting_invalid_thread")
    with _open_journal(_find_journal(home, session)) as source:
        end = os.fstat(source.fileno()).st_size
        if end > MAX_JOURNAL_BYTES:
            raise ValueError("host_accounting_journal_too_large")
        turns = {}
        active = None
        thread_total = Counter()
        last_legacy = None
        native_since_legacy = Counter()
        compacted_since_legacy = False
        seen = {}
        while source.tell() < end:
            line = source.readline(min(end - source.tell(), MAX_LINE_BYTES + 1))
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("host_accounting_line_too_large")
            if not line.endswith(b"\n"):
                break
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                raise ValueError("host_accounting_invalid_journal_record")
            payload = row["payload"]
            if source.tell() == len(line):
                if row["type"] != "session_meta" or payload["id"] != session:
                    raise ValueError("host_accounting_thread_mismatch")
            stamp = _time(row["timestamp"])
            if stamp > cutoff:
                break
            if row["type"] == "event_msg":
                kind = payload.get("type")
                if kind == "task_started":
                    active = payload["turn_id"]
                    if active in turns:
                        raise ValueError("host_accounting_repeated_turn")
                    turns[active] = Turn(stamp)
                elif kind in {"task_complete", "turn_aborted"}:
                    tid = payload["turn_id"]
                    if tid == active:
                        turns[tid].ended = True
                        active = None
                elif kind == "model_rerouted" and payload.get("turn_id") in turns:
                    turns[payload["turn_id"]].models.add("")
                elif kind == "token_count" and payload.get("info") is not None:
                    info = payload["info"]
                    if not isinstance(info, dict):
                        raise ValueError("host_accounting_invalid_token_count")
                    current = _counters(info["total_token_usage"])
                    if current != last_legacy:
                        previous = last_legacy or dict.fromkeys(current, 0)
                        if active is not None and (
                            current.keys() != previous.keys()
                            or any(current[key] - previous[key] != native_since_legacy[key]
                                   for key in current)
                        ):
                            turns[active].legacy_mismatch = True
                        native_since_legacy.clear()
                    elif compacted_since_legacy:
                        # The legacy total can omit the native compaction request.
                        # Its unchanged value is the baseline for subsequent deltas.
                        native_since_legacy.clear()
                    compacted_since_legacy = False
                    last_legacy = current
            elif row["type"] == "compacted":
                compacted_since_legacy = True
            elif row["type"] == "turn_context" and payload["turn_id"] in turns:
                turns[payload["turn_id"]].models.add(payload["model"])
            elif row["type"] == "response_item" and payload.get("role") == "user":
                if active is not None and payload.get("type") == "message":
                    _user_message(turns[active], payload["content"])
            elif row["type"] == "token_usage_record":
                tid = payload["turn_id"]
                if (payload["thread_id"] != session or payload["session_id"] != session
                        or payload.get("root_turn_id") != tid):
                    raise ValueError("host_accounting_request_identity_mismatch")
                response = payload["response_id"]
                if not isinstance(response, str) or not response:
                    raise ValueError("host_accounting_request_identity_missing")
                usage = _counters(payload["usage"])
                total = _counters(payload["thread_token_usage"])
                cumulative = _counters(payload["turn_token_usage"])
                evidence = (tid, usage, total, cumulative)
                if response in seen:
                    if seen[response] != evidence:
                        raise ValueError("host_accounting_conflicting_request")
                    continue
                if tid != active:
                    raise ValueError("host_accounting_request_outside_turn")
                seen[response] = evidence
                thread_total.update(usage)
                native_since_legacy.update(usage)
                if dict(thread_total) != total:
                    raise ValueError("host_accounting_thread_counters_do_not_reconcile")
                turns[tid].requests.append((cumulative, usage))
    return turns


def _accepted_host_events(status, session):
    return {
        event["event_id"]: (event, _time(attempt["started_at"]))
        for event in status["events"] if event["status"] == "accepted"
        for attempt in event["attempts"]
        if (attempt["index"] == event["accepted_index"] and attempt["stage"] == "delivery"
            and attempt["status"] == "accepted" and attempt["cli"] == "codex"
            and attempt["session_id"] == session)
    }


def _turn_record(turn, session, turn_id, rates):
    if not turn.ended or turn.legacy_mismatch or not turn.requests or len(turn.models) != 1:
        raise ValueError("host_accounting_turn_usage_unavailable")
    model = next(iter(turn.models))
    known = turn.requests[-1][0]
    requests = reconcile_request_usage(
        [(model, cumulative, usage) for cumulative, usage in turn.requests], known,
    )
    identity = sha256(f"{session}:{turn_id}".encode()).hexdigest()
    usage = account_cost(
        TokenUsage(**known, turn_usages=requests), cli="codex", model=model,
        state=rates, invocation_id=f"codex-host-turn:{identity}",
        valuation_date=turn.started_at.date().isoformat(),
    )
    return {**usage.cost_records[-1], "session_id": session, "turn_id": turn_id}


def _may_overlap(report, session):
    for key in ("worker", "manager"):
        summary = report[key]
        if summary.get("legacy"):
            return True
        for row in summary.get("_cost_records", []):
            owner = row.get("session_id") or row.get("native_usage", {}).get("session_id")
            if row.get("cli") == "codex" and (owner == session or (
                owner is None and row.get("amount_usd") is not None
            )):
                return True
    return False


def add_host_manager_usage(report, archive, issue_name, workflow_id, explicit_turn_ids=()):
    """Augment this as-of report from one exact journal and its dispatch receipts."""
    if archive is None:
        return
    from workflow_event_callback import read_status

    try:
        status = read_status(archive)
        entries = status["entries"]
        if not entries:
            return
        primary = entries[0]
        binding = primary["acquisition"]["session"]
        if primary["cli"] != "codex" or not binding or binding["source"] != "host_session":
            return
        if status["workflow_id"] != workflow_id:
            raise ValueError("host_accounting_workflow_mismatch")
        session = binding["id"]
        if _may_overlap(report, session):
            raise ValueError("host_accounting_possible_overlap")
        accepted = _accepted_host_events(status, session)
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        turns = _read_turns(home, session, _time(report["captured_at"]))
        rates = pricing_store("openai", os.environ.get("CAFE_PRICING_CACHE_DIR")).status()
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
        # Keep the existing report usable when optional archived evidence is unavailable.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(
            "host_accounting_"
        ) else "host_accounting_evidence_unavailable"
        report["host_manager_usage"] = {"status": "unavailable", "reason": reason}
        return

    matches = defaultdict(list)
    for turn_id, turn in turns.items():
        for callback in turn.callbacks:
            event_id = callback.get("event_id")
            if isinstance(event_id, str) and event_id in accepted:
                matches[event_id].append(turn_id)

    selected = {}
    for event_id, turn_ids in matches.items():
        if len(turn_ids) != 1:
            continue
        turn_id = turn_ids[0]
        turn = turns[turn_id]
        if len(turn.callbacks) != 1 or turn.other_messages:
            continue
        event = turn.callbacks[0]
        receipt, attempted_at = accepted[event_id]
        if (turn.started_at >= attempted_at and event.get("workflow_id") == workflow_id
                and event.get("issue") == issue_name and all(
                    event.get(key) == receipt[key]
                    for key in ("sequence", "occurred_at", "event_type")
                )):
            selected[turn_id] = "callback"

    rejected = []
    for turn_id in dict.fromkeys(explicit_turn_ids):
        if turn_id in selected:
            continue
        if turn_id not in turns or turns[turn_id].callbacks:
            rejected.append(turn_id)
        else:
            selected[turn_id] = "explicit"

    records = []
    recovered = Counter()
    for turn_id, basis in selected.items():
        try:
            record = _turn_record(turns[turn_id], session, turn_id, rates)
        except (ValueError, KeyError, TypeError):
            rejected.append(turn_id)
            continue
        records.append(record)
        if record["amount_usd"] is not None:
            recovered[basis] += 1

    report["host_manager_usage"] = {
        "status": "supplemented" if records else "unavailable",
        "accepted_callbacks": len(accepted),
        "priced_callbacks": recovered["callback"],
        "priced_explicit_turns": recovered["explicit"],
        "rejected_turn_ids": rejected,
    }
    if records:
        report["manager"] = combine_cost_summaries([report["manager"], summarize_cost(records)])
        report["combined"] = combine_cost_summaries([report["worker"], report["manager"]])
