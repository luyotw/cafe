"""The closeout helper attributes archived Codex App usage without persisting it."""

import json
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from uuid import uuid4

import pytest

from tests.unit._kickoff_test_support import SCRIPT_ROOT  # noqa: F401

import host_manager_usage
import workflow_event_callback
from cafe.core.pricing import OpenAIPricingStore
from tests.unit.test_pricing import DOCUMENT, Response


def counters(input_tokens, cached=0, output=0):
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": input_tokens + output,
    }


@pytest.fixture
def usage_fixture(tmp_path, monkeypatch):
    thread = str(uuid4())
    timestamp = "2026-11-17T12:00:00Z"
    workflow = "workflow-example"
    issue = "topic"
    events = [
        {"event_id": f"event-{number}", "workflow_id": workflow, "issue": issue,
         "sequence": number, "event_type": "phase_terminal", "occurred_at": timestamp}
        for number in range(4)
    ]
    home = tmp_path / ".codex"
    journal = home / "sessions" / f"rollout-{thread}.jsonl"
    journal.parent.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CAFE_PRICING_CACHE_DIR", str(tmp_path / "empty-rates"))
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")

    def row(kind, payload, *, at=timestamp):
        return {"type": kind, "payload": payload, "timestamp": at}

    def event(name, turn_id):
        return row("event_msg", {"type": name, "turn_id": turn_id})

    def callback(notice):
        return "CAFE callback\nRepository: /repo\nWake notice: " + json.dumps(notice)

    total = counters(0)

    def turn(turn_id, messages, usage, *, terminal=True):
        nonlocal total
        total = {key: total[key] + usage[key] for key in total}
        rows = [
            event("task_started", turn_id),
            row("turn_context", {"turn_id": turn_id, "model": "gpt-6.1-sol"}),
            *[row("response_item", {"type": "message", "role": "user",
                                  "content": [{"text": text}]}) for text in messages],
            row("token_usage_record", {
                "thread_id": thread, "session_id": thread, "turn_id": turn_id,
                "root_turn_id": turn_id, "response_id": turn_id + "-response",
                "usage": usage, "turn_token_usage": usage, "thread_token_usage": total,
            }),
        ]
        if terminal:
            rows.append(event("task_complete", turn_id))
        return rows

    rows = [
        row("session_meta", {"id": thread}),
        *turn("callback", [callback(events[0])], counters(272_001, 270_000, 10)),
        *turn("direct", ["Confirmed"], counters(1_000, 500, 10)),
        *turn("mixed", [callback(events[1]), "Also start the next issue"], counters(100, 0, 10)),
        *turn("duplicate-first", [callback(events[2])], counters(100, 0, 10)),
        *turn("duplicate-second", [callback(events[2])], counters(100, 0, 10)),
        *turn("other-workflow", [callback({**events[3], "workflow_id": "different"})],
              counters(100, 0, 10)),
        *turn("active", ["Still running"], counters(100, 0, 10), terminal=False),
    ]
    journal.write_text("".join(json.dumps(record) + "\n" for record in rows))
    status = {
        "workflow_id": workflow,
        "entries": [{"cli": "codex", "acquisition": {"session": {
            "source": "host_session", "id": thread,
        }}}],
        "events": [{**notice, "status": "accepted", "accepted_index": 0, "attempts": [{
            "index": 0, "stage": "delivery", "status": "accepted", "cli": "codex",
            "session_id": thread, "started_at": timestamp,
        }]} for notice in events],
    }
    monkeypatch.setattr(workflow_event_callback, "read_status", lambda _archive: status)
    return journal, workflow, issue, thread


def empty_report():
    unknown = host_manager_usage.summarize_cost([])
    return {"captured_at": "2026-11-17T12:01:00Z", "worker": unknown,
            "manager": unknown, "combined": unknown}


def test_recover_only_attributed_turns_and_leave_journal_unchanged(usage_fixture):
    journal, workflow, issue, _ = usage_fixture
    before = journal.read_bytes()
    report = empty_report()
    host_manager_usage.add_host_manager_usage(
        report, Path("archive"), issue, workflow,
        ["callback", "direct", "direct", "mixed", "other-workflow", "active", "missing"],
    )
    assert report["host_manager_usage"] == {
        "status": "supplemented", "accepted_callbacks": 4, "priced_callbacks": 1,
        "priced_explicit_turns": 1,
        "rejected_turn_ids": ["mixed", "other-workflow", "missing", "active"],
        "pricing": {"stale": True, "error": None},
    }
    assert report["manager"]["known"] == Decimal("0.063304")
    assert report["combined"]["known"] == report["manager"]["known"]
    assert report["combined"]["incomplete"]
    assert journal.read_bytes() == before


def test_missing_native_request_preserves_original_report(usage_fixture):
    journal, workflow, issue, _ = usage_fixture
    rows = journal.read_text().splitlines(keepends=True)
    first = next(i for i, line in enumerate(rows) if json.loads(line)["type"] == "token_usage_record")
    journal.write_text("".join(rows[:first] + rows[first + 1:]))
    report = empty_report()
    host_manager_usage.add_host_manager_usage(report, Path("archive"), issue, workflow)
    assert report["host_manager_usage"]["reason"] == "host_accounting_thread_counters_do_not_reconcile"
    assert report["manager"]["known"] == 0
    assert report["manager"]["incomplete"]


def test_retained_usage_for_same_session_may_overlap(usage_fixture):
    _, workflow, issue, thread = usage_fixture
    report = empty_report()
    report["manager"] = host_manager_usage.summarize_cost([
        {"invocation_id": "retained", "cli": "codex", "session_id": thread,
         "provenance": "estimated", "amount_usd": "1.2"}
    ])
    host_manager_usage.add_host_manager_usage(report, Path("archive"), issue, workflow)
    assert report["host_manager_usage"]["reason"] == "host_accounting_possible_overlap"
    assert report["manager"]["known"] == Decimal("1.2")


def test_invalid_native_payload_preserves_original_report(usage_fixture):
    journal, workflow, issue, _ = usage_fixture
    lines = journal.read_text().splitlines(keepends=True)
    lines[1] = json.dumps({
        "type": "event_msg", "payload": None, "timestamp": "2026-11-17T12:00:00Z",
    }) + "\n"
    journal.write_text("".join(lines))
    report = empty_report()
    host_manager_usage.add_host_manager_usage(report, Path("archive"), issue, workflow)
    assert report["host_manager_usage"]["reason"] == "host_accounting_invalid_journal_record"
    assert report["manager"]["known"] == 0
    assert report["manager"]["incomplete"]


def test_missing_last_request_with_identical_usage_is_excluded(usage_fixture):
    journal, workflow, issue, _ = usage_fixture
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    end = next(index for index, row in enumerate(rows)
               if row["payload"].get("type") == "task_complete")
    first_turn = rows[:end]
    terminal = rows[end]
    original_total = first_turn[-1]["payload"]["thread_token_usage"]

    def legacy(total):
        return {"type": "event_msg", "timestamp": terminal["timestamp"],
                "payload": {"type": "token_count", "info": {
                    "total_token_usage": total, "last_token_usage": original_total,
                }}}

    rows = [*first_turn, legacy(original_total),
            legacy({key: 2 * value for key, value in original_total.items()}), terminal]
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = empty_report()
    host_manager_usage.add_host_manager_usage(report, Path("archive"), issue, workflow)
    assert report["host_manager_usage"]["priced_callbacks"] == 0
    assert report["host_manager_usage"]["rejected_turn_ids"] == ["callback"]
    assert report["manager"]["known"] == 0


@pytest.mark.parametrize("refresh", ["success", "failure", "disabled"])
def test_host_cost_refreshes_due_rates_and_falls_back(usage_fixture, tmp_path, monkeypatch, refresh):
    _, workflow, issue, _ = usage_fixture
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        if refresh == "failure":
            raise URLError("offline")
        return Response(DOCUMENT.replace("gpt-test", "gpt-6.1-sol"))

    store = OpenAIPricingStore(tmp_path / "rates", opener=fetch)
    monkeypatch.setattr(host_manager_usage, "pricing_store", lambda *_: store)
    if refresh != "disabled":
        monkeypatch.delenv("CAFE_PRICING_AUTO_UPDATE")
    report = empty_report()
    host_manager_usage.add_host_manager_usage(report, Path("archive"), issue, workflow)
    pricing = report["host_manager_usage"]["pricing"]
    assert len(calls) == (0 if refresh == "disabled" else 1)
    assert pricing["stale"] == (refresh != "success")
    assert pricing["error"] == ("URLError" if refresh == "failure" else None)
    assert report["manager"]["known"] == Decimal(
        "0.116154" if refresh == "success" else "0.062154"
    )
