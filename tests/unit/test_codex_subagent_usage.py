"""Plan U1/U2/U5/U6/U7: owned native intervals and contained discovery gaps."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cafe.agents.cli.codex_subagent_usage import NativeInterval

START = datetime(2026, 10, 10, tzinfo=timezone.utc)
ROOT = "00000000-0000-0000-0000-000000000001"
CHILD = "00000000-0000-0000-0000-000000000002"
NESTED = "00000000-0000-0000-0000-000000000003"


def counters(n):
    return dict(
        input_tokens=n,
        cached_input_tokens=n // 2,
        cache_write_input_tokens=0,
        output_tokens=n // 10,
        reasoning_output_tokens=n // 20,
        total_tokens=n + n // 10,
    )


def event(kind, second, **payload):
    return dict(
        timestamp=(START + timedelta(seconds=second)).isoformat(),
        type="event_msg",
        payload=dict(type=kind, **payload),
    )


def journal(home, session, parent=None, second=1, fork=None, version="0.159.3"):
    path = home / "sessions" / f"rollout-test-{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(
        id=session,
        timestamp=(START + timedelta(seconds=second)).isoformat(),
        cli_version=version,
        source="exec",
    )
    if parent:
        metadata["source"] = dict(
            subagent=dict(thread_spawn=dict(parent_thread_id=parent, agent_path="/root/child"))
        )
    if fork:
        metadata["forked_from_id"] = fork
    path.write_text(json.dumps(dict(type="session_meta", payload=metadata)) + "\n")
    return path


def append(path, *rows):
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def turn(path, n=100, second=2, turn_id="t1", complete=True, model="gpt-5.4"):
    append(
        path,
        event("task_started", second, turn_id=turn_id),
        dict(
            timestamp=(START + timedelta(seconds=second)).isoformat(),
            type="turn_context",
            payload=dict(turn_id=turn_id, model=model),
        ),
        event(
            "token_count",
            second + 1,
            info=dict(total_token_usage=counters(n), last_token_usage=counters(n)),
        ),
    )
    if complete:
        append(path, event("task_complete", second + 2, turn_id=turn_id))


def interval(home):
    return NativeInterval(
        home,
        workflow_id="flow",
        caller_id="custom-step",
        attempt_id="attempt",
        root_session_id=ROOT,
        started_at=START,
    )


def collect(scope):
    return scope.collect(cutoff=START + timedelta(seconds=20), final=True)


def test_new_nested_children_exclude_unrelated_and_future_history(tmp_path):
    journal(tmp_path, ROOT, second=-20)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    turn(journal(tmp_path, NESTED, CHILD), 50)
    turn(journal(tmp_path, "00000000-0000-0000-0000-000000000004", second=3), 200)
    append(child, event("token_count", 50, info=dict(total_token_usage=counters(900))))
    records = collect(scope)
    children = [r for r in records if r["native_usage"]["kind"] == "child"]
    assert {r["session_id"] for r in children} == {CHILD, NESTED}
    assert sum(r["usage"]["input_tokens"] for r in children) == 150
    assert children[0]["model"] == "gpt-5.4"
    assert children[0]["native_usage"]["end"]["counters"]["total_tokens"] == 110
    assert all(r["native_usage"]["workflow_id"] == "flow" for r in children)
    assert json.dumps(records).find("private prompt") == -1


def test_existing_child_requires_causal_resubmission_and_uses_entry_delta(tmp_path):
    root = journal(tmp_path, ROOT, second=-30)
    child = journal(tmp_path, CHILD, ROOT, second=-25)
    turn(child, 100, second=-20)
    scope = interval(tmp_path)
    turn(child, 150, second=2, turn_id="t2")
    # An old child's ancestry alone cannot claim its continuation.
    assert not [r for r in collect(scope) if r["native_usage"]["kind"] == "child"]
    append(root, event("collab_resume_end", 1, receiver_thread_id=CHILD))
    children = [r for r in collect(scope) if r["native_usage"]["kind"] == "child"]
    assert children[0]["usage"]["input_tokens"] == 50


@pytest.mark.parametrize("mode", ["missing", "reset", "fork", "unsupported", "partial", "mixed"])
def test_degraded_native_evidence_is_unknown_or_partial(tmp_path, mode):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(
        tmp_path,
        CHILD,
        ROOT,
        fork=ROOT if mode == "fork" else None,
        version="9.0" if mode == "unsupported" else "0.159.3",
    )
    if mode != "missing":
        turn(child, complete=mode != "partial")
    if mode == "reset":
        append(child, event("token_count", 7, info=dict(total_token_usage=counters(10))))
    if mode == "mixed":
        turn(child, 150, second=6, turn_id="t2", model="another-model")
    record = next(r for r in collect(scope) if r["native_usage"]["kind"] == "child")
    assert not record["complete"] or record["provenance"] == "unavailable"
    if mode in {"missing", "reset", "fork", "unsupported"}:
        assert "input_tokens" not in record["usage"]
    if mode == "mixed":
        assert record["model"] is None


def test_read_only_sqlite_discovery_and_fallback_agree(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    fallback = collect(scope)
    db = tmp_path / "state_unstable.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT);"
            "CREATE TABLE threads(id TEXT, rollout_path TEXT);"
        )
        conn.execute("INSERT INTO thread_spawn_edges VALUES (?, ?)", (ROOT, CHILD))
        conn.execute("INSERT INTO threads VALUES (?, ?)", (CHILD, str(child)))
    before = db.read_bytes()
    indexed = collect(scope)
    assert [r["usage"] for r in fallback] == [r["usage"] for r in indexed]
    assert db.read_bytes() == before


def test_unsafe_and_over_bound_sources_stay_contained(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    child.unlink()
    child.symlink_to("/dev/zero")
    assert any(r["native_usage"]["gaps"] for r in collect(scope))


def test_fork_inherited_counters_are_baseline_not_child_spend(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT, fork=ROOT)
    append(child, event("token_count", -20, info=dict(total_token_usage=counters(1000))))
    turn(child, 1050)
    row = next(r for r in collect(scope) if r["native_usage"]["kind"] == "child")
    assert row["usage"]["input_tokens"] == 50


def test_fresh_fork_per_thread_usage_records_do_not_inherit_parent_history(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT, fork=ROOT)
    turn(child, 1050, complete=False)
    append(
        child,
        dict(
            type="token_usage_record",
            timestamp=(START + timedelta(seconds=3)).isoformat(),
            payload=dict(
                thread_id=CHILD, turn_id="t1", session_id=ROOT, thread_token_usage=counters(50)
            ),
        ),
        event("task_complete", 4, turn_id="t1"),
    )
    row = next(r for r in collect(scope) if r["native_usage"]["kind"] == "child")
    assert row["usage"]["input_tokens"] == 50
    assert row["native_usage"]["source"]["counter_source"] == "token_usage_record"


def test_truncated_suffix_keeps_verified_prefix_and_marks_partial(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    with child.open("a") as handle:
        handle.write('{"type":"event_msg"')
    row = next(r for r in collect(scope) if r["native_usage"]["kind"] == "child")
    assert row["usage"]["input_tokens"] == 100
    assert not row["complete"] and row["native_usage"]["gaps"]


def test_actual_child_model_uses_pinned_rates_and_preserves_unavailable_categories(tmp_path):
    from decimal import Decimal

    from tests.unit.test_cost_accounting import state

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    scope.rate = state()
    turn(journal(tmp_path, CHILD, ROOT), model="gpt-test")
    row = next(r for r in collect(scope) if r["native_usage"]["kind"] == "child")
    assert row["provenance"] == "estimated" and Decimal(row["amount_usd"]) > 0
    assert row["billed_tokens"]["input"] == 50
    assert row["billed_tokens"]["cached_input"] == 50
    assert row["billed_tokens"]["output"] == 10
    assert "pricing" in row and row["model"] == "gpt-test"
    # Changing a mutable index or report-time card cannot reprice this record.
    scope.rate["snapshot"]["data"]["rates"]["gpt-test"]["standard"]["usd_per_million_tokens"][
        "short"
    ]["input"] = "999"
    from cafe.core.cost import summarize_cost

    assert summarize_cost([row])["known"] == Decimal(row["amount_usd"])
