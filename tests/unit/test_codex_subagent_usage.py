"""Plan U1/U2/U5/U6/U7: owned native intervals and contained discovery gaps."""

import json
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


def test_journal_discovery_is_independent_of_optional_index(tmp_path):
    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    fallback = collect(scope)
    db = tmp_path / "state_unstable.sqlite"
    db.write_bytes(b"inaccessible or unsupported index")
    before = db.read_bytes()
    indexed = collect(scope)
    assert [r["usage"] for r in fallback] == [r["usage"] for r in indexed]
    assert indexed[0]["native_usage"]["gaps"] == fallback[0]["native_usage"]["gaps"]
    assert db.read_bytes() == before


def test_large_journal_home_keeps_archived_nested_coverage_within_bounds(tmp_path):
    import time
    import uuid

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    turn(child)
    nested = journal(tmp_path, NESTED, CHILD)
    turn(nested, 50)
    archived = tmp_path / "archived_sessions/2026/10"
    archived.mkdir(parents=True)
    nested.rename(archived / nested.name)
    for number in range(5000):
        journal(tmp_path, str(uuid.UUID(int=number + 100)), second=-100)
    began = time.monotonic()
    records = collect(scope)
    elapsed = time.monotonic() - began
    children = [r for r in records if r["native_usage"]["kind"] == "child"]
    assert {r["session_id"] for r in children} == {CHILD, NESTED}
    assert sum(r["usage"]["input_tokens"] for r in children) == 150
    assert not records[0]["native_usage"]["gaps"]
    print(
        f"5003-journal discovery: {elapsed:.3f}s; header bytes={scope.header_bytes}; journal bytes={scope.read_bytes}"
    )


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


def large_history(home, count=20):
    import uuid

    for number in range(count):
        path = journal(home, str(uuid.UUID(int=number + 100)), second=-100)
        row = json.loads(path.read_text())
        row['payload']['base_instructions'] = 'x' * 8000
        path.write_text(json.dumps(row) + '\n')


def test_large_unrelated_headers_cannot_starve_root_baseline_or_named_child(tmp_path, monkeypatch):
    monkeypatch.setattr('cafe.agents.cli.codex_subagent_usage.MAX_BYTES', 128 * 1024)
    large_history(tmp_path)
    root = journal(tmp_path, ROOT, second=-30)
    child = journal(tmp_path, CHILD, ROOT, second=-25)
    turn(child, 100, second=-20)
    append(root, event('collab_agent_spawn_end', -24, new_thread_id=CHILD))
    scope = interval(tmp_path)
    assert scope.baselines[CHILD]['counters']['input_tokens'] == 100
    assert 'native_discovery_bound' in scope.entry_gaps
    append(root, event('collab_resume_end', 1, receiver_thread_id=CHILD))
    turn(child, 150, second=2, turn_id='t2')
    records = collect(scope)
    row = next(r for r in records if r['native_usage']['kind'] == 'child')
    assert row['usage']['input_tokens'] == 50
    assert row['native_usage']['inclusion'] == 'exclusive'
    assert 'native_discovery_bound' in records[0]['native_usage']['gaps']
    assert 'root_native_source_unavailable' not in records[0]['native_usage']['gaps']
    assert scope.header_bytes + scope.read_bytes <= 128 * 1024


def test_exploration_deadline_does_not_invalidate_verified_reads(tmp_path, monkeypatch):
    import cafe.agents.cli.codex_subagent_usage as native

    large_history(tmp_path, 2)
    journal(tmp_path, ROOT, second=-30)
    clock = [0.0]
    monkeypatch.setattr(native.time, 'monotonic', lambda: clock[0])
    original = native.NativeInterval._header

    def header(self, path, *, discovery=False):
        result = original(self, path, discovery=discovery)
        if discovery:
            clock[0] += native.MAX_SECONDS / 2
        return result

    monkeypatch.setattr(native.NativeInterval, '_header', header)
    scope = interval(tmp_path)
    assert ROOT in scope.baselines
    clock[0] = 0.0
    records = collect(scope)
    assert 'native_discovery_bound' in records[0]['native_usage']['gaps']
    assert scope.root_version == '0.159.3'


def test_duplicate_root_is_not_certified_by_prioritized_lookup(tmp_path):
    path = journal(tmp_path, ROOT, second=-30)
    (path.parent / f'another-{ROOT}.jsonl').write_bytes(path.read_bytes())
    scope = interval(tmp_path)
    assert ROOT not in scope.baselines
    records = collect(scope)
    assert 'duplicate_native_identity' in records[0]['native_usage']['gaps']
    assert 'root_native_source_unavailable' in records[0]['native_usage']['gaps']


def test_incomplete_filename_inventory_cannot_certify_unique_root(tmp_path, monkeypatch):
    large_history(tmp_path, 2)
    journal(tmp_path, ROOT, second=-30)
    monkeypatch.setattr('cafe.agents.cli.codex_subagent_usage.MAX_FILES', 1)
    scope = interval(tmp_path)
    assert ROOT not in scope.baselines
    assert 'native_discovery_bound' in collect(scope)[0]['native_usage']['gaps']


def request_child(home, scope, *, per_thread=False, fork=False, sizes=(180_000, 180_000),
                  duplicate=False, damage=None):
    child = journal(home, CHILD, ROOT, fork=ROOT if fork else None)
    inherited = counters(1_000_000) if fork and not per_thread else dict.fromkeys(counters(0), 0)
    if fork:
        append(child, event('token_count', -10,
                            info=dict(total_token_usage=counters(1_000_000))))
    append(child, event('task_started', 2, turn_id='t1'))
    total = dict(inherited)
    for index, n in enumerate(sizes):
        when = 3 + index
        last = counters(n)
        total = {key: total[key] + last[key] for key in total}
        append(child, dict(type='turn_context', timestamp=(START + timedelta(seconds=when)).isoformat(),
                           payload=dict(model='gpt-test')))
        if per_thread:
            row = dict(type='token_usage_record',
                       timestamp=(START + timedelta(seconds=when)).isoformat(),
                       payload=dict(thread_id=CHILD, turn_id='t1', session_id=ROOT,
                                    thread_token_usage=dict(total), usage=dict(last)))
        else:
            row = event('token_count', when,
                        info=dict(total_token_usage=dict(total), last_token_usage=dict(last)))
        if damage == 'missing' and index == 0:
            continue
        if damage == 'wrong_last' and index == 1:
            (row['payload']['usage'] if per_thread else
             row['payload']['info']['last_token_usage'])['input_tokens'] -= 1
        append(child, row)
        if duplicate:
            append(child, row)
    append(child, event('task_complete', 6, turn_id='t1'))
    return child


@pytest.mark.parametrize('per_thread', [False, True])
@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('duplicate', [False, True])
def test_child_short_requests_crossing_aggregate_threshold_are_priced(
    tmp_path, per_thread, fork, duplicate
):
    from decimal import Decimal
    from tests.unit.test_cost_accounting import state

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    scope.rate = state()
    request_child(tmp_path, scope, per_thread=per_thread, fork=fork, duplicate=duplicate)
    row = next(r for r in collect(scope) if r['native_usage']['kind'] == 'child')
    assert row['usage']['input_tokens'] == 360_000
    assert row['complete']
    assert len(row['request_estimates']) == 2
    assert row['context_band'] == 'short'
    assert Decimal(row['amount_usd']) == Decimal('0.756')


@pytest.mark.parametrize('per_thread', [False, True])
@pytest.mark.parametrize('damage', ['missing', 'wrong_last'])
def test_child_unreconciled_requests_preserve_tokens_without_guessing_price(
    tmp_path, per_thread, damage
):
    from tests.unit.test_cost_accounting import state

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    scope.rate = state()
    request_child(tmp_path, scope, per_thread=per_thread, damage=damage)
    row = next(r for r in collect(scope) if r['native_usage']['kind'] == 'child')
    assert row['usage']['input_tokens'] == 360_000
    assert row['amount_usd'] is None
    assert row['reason'] == 'context_tier_unavailable'


def test_child_mixed_context_requests_and_cutoff_use_only_owned_costs(tmp_path):
    from tests.unit.test_cost_accounting import state

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    scope.rate = state()
    child = request_child(tmp_path, scope, sizes=(180_000, 300_000))
    append(child, event('token_count', 30,
                        info=dict(total_token_usage=counters(900_000), last_token_usage=counters(420_000))))
    row = next(r for r in collect(scope) if r['native_usage']['kind'] == 'child')
    assert row['usage']['input_tokens'] == 480_000
    assert row['context_band'] == 'mixed'
    assert [r['context_band'] for r in row['request_estimates']] == ['short', 'long']


def test_cross_generation_event_prioritizes_candidate_without_rewriting_ancestry(tmp_path):
    root = journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    child = journal(tmp_path, CHILD, ROOT)
    nested = journal(tmp_path, NESTED, CHILD)
    unrelated = '00000000-0000-0000-0000-000000000004'
    turn(journal(tmp_path, unrelated), 900)
    # Root can address a grandchild directly; the sender is not its parent.
    append(root, event('collab_agent_interaction_end', 1, receiver_thread_id=NESTED),
           event('collab_agent_interaction_end', 1, receiver_thread_id=unrelated),
           event('collab_agent_spawn_end', 1, new_thread_id=CHILD))
    turn(child, 100)
    append(child, event('collab_agent_spawn_end', 3, new_thread_id=NESTED))
    turn(nested, 50)
    children = [r for r in collect(scope) if r['native_usage']['kind'] == 'child']
    assert {r['session_id'] for r in children} == {CHILD, NESTED}
    assert sum(r['usage']['input_tokens'] for r in children) == 150
    assert not collect(scope)[0]['native_usage']['gaps']


@pytest.mark.parametrize('per_thread', [False, True])
@pytest.mark.parametrize('task_started', [False, True])
def test_child_new_turn_requires_its_own_model_context(tmp_path, per_thread, task_started):
    from tests.unit.test_cost_accounting import state

    journal(tmp_path, ROOT, second=-30)
    scope = interval(tmp_path)
    scope.rate = state()
    child = request_child(tmp_path, scope, per_thread=per_thread)
    rows = [json.loads(line) for line in child.read_text().splitlines()]
    # Split the two requests into separate turns, omit the second model context.
    index = next(i for i, row in enumerate(rows) if row.get('timestamp') ==
                 (START + timedelta(seconds=4)).isoformat())
    rows.pop(index)
    rows[index]['payload']['turn_id'] = 't2'
    if task_started:
        rows.insert(index, event('task_started', 4, turn_id='t2'))
    rows.insert(index, event('task_complete', 3, turn_id='t1'))
    rows[-1]['payload']['turn_id'] = 't2'
    if not task_started and not per_thread:
        # token_count has no turn ID: native task-start is its ownership boundary.
        rows.insert(index + 1, event('task_started', 4, turn_id='t2'))
    child.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    row = next(r for r in collect(scope) if r['native_usage']['kind'] == 'child')
    assert row['usage']['input_tokens'] == 360_000
    assert row['amount_usd'] is None
    assert 'request_estimates' not in row
    assert 'actual_model_unavailable' in row['native_usage']['gaps']
