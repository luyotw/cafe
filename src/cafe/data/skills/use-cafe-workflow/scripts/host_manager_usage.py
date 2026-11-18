"""Recover Codex App Manager and subagent usage for the closeout report."""

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
from cafe.core.cost import account_cost, combine_cost_summaries, normalized_counters, summarize_cost
from cafe.core.pricing_sources import pricing_store
from cafe.core.types import TokenUsage

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_SUBAGENT_JOURNALS = 32


@dataclass
class Turn:
    started_at: datetime
    root_id: str
    start_offset: int
    end: tuple[datetime, int] | None = None
    models: set[str] = field(default_factory=set)
    callbacks: list[dict] = field(default_factory=list)
    other_messages: int = 0
    requests: list[tuple[dict, dict]] = field(default_factory=list)
    children: set[str] = field(default_factory=set)
    child_activity: set[str] = field(default_factory=set)
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


def _child_activity(payload, session):
    if payload.get("type") == "item_completed" and payload.get("thread_id") == session:
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "SubAgentActivity":
            return item.get("agent_thread_id"), item.get("kind") == "started"
    if payload.get("sender_thread_id", session) == session:
        if payload.get("type") == "collab_agent_spawn_end":
            return payload.get("new_thread_id"), True
        if payload.get("type") in {"collab_resume_end", "collab_agent_interaction_end"}:
            return payload.get("receiver_thread_id"), False
    return None, False


def _read_turns(home, session, cutoff, *, parent=None, root_session=None, inherited=frozenset()):
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
                if parent is not None:
                    spawn = payload["source"]["subagent"]["thread_spawn"]
                    if (spawn["parent_thread_id"] != parent
                            or payload.get("forked_from_id") not in (None, parent)):
                        raise ValueError("host_accounting_subagent_parent_mismatch")
            stamp = _time(row["timestamp"])
            if stamp > cutoff:
                break
            tid = payload.get("turn_id")
            # A fork can copy and restamp its ancestors' turn history.
            if tid in inherited and (
                row["type"] != "token_usage_record" or payload.get("thread_id") != session
            ):
                continue
            if row["type"] == "event_msg":
                kind = payload.get("type")
                if kind == "task_started":
                    active = payload["turn_id"]
                    root_id = payload.get("root_turn_id", active)
                    if parent is not None and not all(
                        isinstance(identity, str) and 0 < len(identity) <= 512
                        for identity in (active, root_id)
                    ):
                        raise ValueError("host_accounting_invalid_subagent_turn_id")
                    if active in turns:
                        raise ValueError("host_accounting_repeated_turn")
                    turns[active] = Turn(stamp, root_id, source.tell() - len(line))
                elif kind == "task_complete" or (kind == "turn_aborted" and parent is None):
                    tid = payload["turn_id"]
                    if tid == active:
                        turns[tid].end = (stamp, source.tell())
                        active = None
                elif kind == "model_rerouted" and payload.get("turn_id") in turns:
                    turns[payload["turn_id"]].models.add("")
                elif (kind == "token_count" and payload.get("info") is not None
                      and (parent is None or active is not None)):
                    info = payload["info"]
                    if not isinstance(info, dict):
                        raise ValueError("host_accounting_invalid_token_count")
                    current = _counters(info["total_token_usage"])
                    if current != last_legacy:
                        previous = last_legacy or dict.fromkeys(current, 0)
                        if active is not None and (
                            current.keys() != previous.keys()
                            or any(
                                not 0 <= current[key] - previous[key] <= native_since_legacy[key]
                                if parent is not None
                                else current[key] - previous[key] != native_since_legacy[key]
                                for key in current
                            )
                        ):
                            turns[active].legacy_mismatch = True
                        native_since_legacy.clear()
                    elif compacted_since_legacy:
                        # The legacy total can omit the native compaction request.
                        # Its unchanged value is the baseline for subsequent deltas.
                        native_since_legacy.clear()
                    compacted_since_legacy = False
                    last_legacy = current
                elif active is not None and tid in (None, active):
                    child, spawned = _child_activity(payload, session)
                    if isinstance(child, str):
                        turn = turns[active]
                        (turn.children if spawned else turn.child_activity).add(child)
            elif row["type"] == "compacted":
                compacted_since_legacy = True
            elif row["type"] == "turn_context" and payload["turn_id"] in turns:
                turns[payload["turn_id"]].models.add(payload["model"])
            elif row["type"] == "response_item" and payload.get("role") == "user":
                if parent is None and active is not None and payload.get("type") == "message":
                    _user_message(turns[active], payload["content"])
            elif row["type"] == "token_usage_record":
                tid = payload["turn_id"]
                root_id = tid if parent is None else turns[tid].root_id
                if (payload["thread_id"] != session
                        or payload["session_id"] not in (session, root_session or session)
                        or payload.get("root_turn_id") != root_id
                        or (parent is not None and root_id == tid)):
                    raise ValueError("host_accounting_request_identity_mismatch")
                response = payload["response_id"]
                if not isinstance(response, str) or not response:
                    raise ValueError("host_accounting_request_identity_missing")
                raw = (payload["usage"], payload["thread_token_usage"], payload["turn_token_usage"])
                if parent is not None and any(normalized_counters(item)[1] for item in raw):
                    raise ValueError("host_accounting_invalid_subagent_counters")
                usage, total, cumulative = map(_counters, raw)
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
    if turn.end is None or turn.legacy_mismatch or not turn.requests or len(turn.models) != 1:
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
    record = usage.cost_records[-1]
    record["usage"]["total_tokens"] = known["input_tokens"] + known["output_tokens"]
    return {**record, "session_id": session, "turn_id": turn_id}


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


def _subagent_records(report, home, session, turns, attributed, cutoff, rates, workflow_id):
    related, other = [], []

    def queue_children(parent, parent_turns, inherited):
        ancestors = inherited.union(parent_turns)
        spawn_roots = defaultdict(set)
        active_children = set()
        for turn in parent_turns.values():
            for child in turn.children:
                roots = spawn_roots[child]
                if turn.root_id in attributed:
                    roots.add(turn.root_id)
            if turn.root_id in attributed:
                active_children.update(turn.child_activity)
        for child, roots in sorted(spawn_roots.items()):
            queue = related if roots or child in active_children else other
            queue.append((parent, child, ancestors, roots))

    records = []
    unavailable = set()
    visited = {session}
    queue_children(session, turns, frozenset())
    while related or other:
        relevant = bool(related)
        parent, child, ancestors, spawned_by = (related or other).pop()
        if child in visited:
            continue
        if len(visited) > MAX_SUBAGENT_JOURNALS:
            if relevant:
                unavailable.add(child)
                unavailable.update(edge[1] for edge in related if edge[1] not in visited)
            break
        visited.add(child)
        try:
            child_turns = _read_turns(
                home, child, cutoff, parent=parent, root_session=session, inherited=ancestors,
            )
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
            if relevant:
                unavailable.add(child)
            continue
        queue_children(child, child_turns, ancestors)
        owned = {tid: turn for tid, turn in child_turns.items() if turn.root_id in attributed}
        if spawned_by - {turn.root_id for turn in owned.values()}:
            unavailable.add(child)
        if not owned:
            continue
        if _may_overlap(report, child):
            unavailable.add(child)
            continue
        for tid, turn in owned.items():
            try:
                if turn.started_at < turns[turn.root_id].started_at:
                    raise ValueError("host_accounting_subagent_before_root_turn")
                record = _turn_record(turn, child, tid, rates)
            except (ValueError, KeyError, TypeError):
                unavailable.add(child)
                continue
            counters = record["usage"]
            ended_at, end_offset = turn.end
            segment = record["invocation_id"].removeprefix("codex-host-turn:")
            record["invocation_id"] = "native:" + segment
            record["native_usage"] = dict(
                version=1, kind="child", status="final", inclusion="exclusive", gaps=[],
                segment_id=segment, caller_id=turn.root_id, attempt_id=tid,
                workflow_id=workflow_id, session_id=child, root_session_id=session,
                parent_session_id=parent, turn_ids=[tid], known_fields=sorted(counters),
                start=dict(at=turn.started_at.isoformat(), offset=turn.start_offset,
                           counters=dict.fromkeys(counters, 0)),
                end=dict(at=ended_at.isoformat(), offset=end_offset, counters=dict(counters)),
                source=dict(kind="codex_jsonl", counter_source="token_usage_record",
                            semantics="codex_turn_token_usage"),
            )
            records.append(record)
    return records, len(unavailable)


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
        cutoff = _time(report["captured_at"])
        turns = _read_turns(home, session, cutoff)
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

    rates = None
    if selected:
        store = pricing_store("openai", os.environ.get("CAFE_PRICING_CACHE_DIR"))
        rates = store.get(auto_update=os.environ.get("CAFE_PRICING_AUTO_UPDATE", "1") != "0")

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
    if rates is not None:
        report["host_manager_usage"]["pricing"] = {
            "stale": rates["stale"],
            "error": rates.get("error"),
        }
    if records:
        attributed = {record["turn_id"] for record in records}
        children, unavailable = _subagent_records(
            report, home, session, turns, attributed, cutoff, rates, workflow_id,
        )
        if children or unavailable:
            child_cost = summarize_cost(children)
            child_cost["incomplete"] |= bool(unavailable)
            report["host_manager_usage"]["subagents"] = {
                "threads": len({record["session_id"] for record in children}),
                "priced_turns": child_cost["counts"]["estimated"] + child_cost["counts"]["reported"],
                "unavailable_threads": unavailable,
                "cost": {key: value for key, value in child_cost.items()
                         if key not in ("_cost_records", "native_usage")},
            }
        supplemental = summarize_cost(records + children)
        supplemental["incomplete"] |= bool(unavailable)
        report["manager"] = combine_cost_summaries([report["manager"], supplemental])
        report["combined"] = combine_cost_summaries([report["worker"], report["manager"]])
