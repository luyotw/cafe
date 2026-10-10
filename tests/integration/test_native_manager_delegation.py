"""Plan I3/I4/I5/U2/U8/U10: admitted native host delegation and retention."""

import json
from datetime import datetime, timezone

import pytest

from cafe.manager._schema import freshness_semantic_facts
from cafe.manager._store import load_contract
from cafe.manager.costs import CostStore, inclusive_report
from tests.fixtures.manager_chat import adapter, issue, repository
from tests.integration.test_codex_descendant_accounting import native_journal
from tests.unit.test_codex_subagent_usage import CHILD, NESTED, ROOT, counters


def host_fixture(tmp_path, monkeypatch, root=None, name="topic"):
    root = root or repository(tmp_path / "repo")
    directory = issue(root, name)
    state_path = directory / "manager/dispatch_state.json"
    state = json.loads(state_path.read_text())
    state["entries"][0]["session"] = dict(
        id=ROOT, source="host_session", acquired_at=datetime.now(timezone.utc).isoformat()
    )
    state_path.write_text(json.dumps(state))
    contract, _ = load_contract(directory)
    fresh = dict(
        semantic_facts=freshness_semantic_facts(contract),
        runtime_constraints=contract["provenance"]["runtime_constraints"],
    )
    workflow_id = contract["identity"]["workflow_id"]
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_THREAD_ID", ROOT)
    monkeypatch.setenv("CAFE_PRICING_AUTO_UPDATE", "0")
    return dict(
        project_root=root,
        issue_dir=directory,
        issue_name=name,
        workflow_id=workflow_id,
        fresh_facts=fresh,
        home=home,
    )


def append_event(path, kind, *, observed_at=None, **payload):
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                dict(
                    timestamp=observed_at or datetime.now(timezone.utc).isoformat(),
                    type="event_msg",
                    payload=dict(type=kind, **payload),
                )
            )
            + "\n"
        )


def test_native_begin_finalize_requires_binding_and_causal_work(tmp_path, monkeypatch):
    args = host_fixture(tmp_path, monkeypatch)
    root_journal = native_journal(args["home"], ROOT, complete=False)
    helper = adapter("native_delegation_accounting")
    assert helper.account("begin", correlation="bracket", **args)["status"] == "open"
    store = CostStore(args["project_root"], "topic", args["workflow_id"])
    assert store.read()["manager_sources"][0]["records"][0]["native_usage"]["status"] == "open"
    native_journal(args["home"], CHILD, ROOT)
    # Born ancestry alone cannot allocate another task under a persistent host.
    append_event(root_journal, "collab_agent_spawn_end", sender_thread_id=ROOT, new_thread_id=CHILD)
    helper.account("finalize", correlation="bracket", **args)
    report = inclusive_report(
        args["project_root"], "topic", args["workflow_id"], issue_dir=args["issue_dir"]
    )
    view = report["manager"]["native_usage"]
    assert [r["session_id"] for r in view["children"]] == [CHILD]
    assert view["child_tokens"]["input_tokens"] == 100
    before = store.path.read_bytes()
    helper.account("finalize", correlation="bracket", **args)
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        helper.account("finalize", correlation="missing-entry", **args)
    monkeypatch.setenv("CODEX_THREAD_ID", CHILD)
    with pytest.raises(ValueError):
        helper.account("begin", correlation="wrong-host", **args)


def test_host_claims_cannot_overlap_workflows_and_prior_children_are_excluded(
    tmp_path, monkeypatch
):
    args = host_fixture(tmp_path, monkeypatch)
    native_journal(args["home"], ROOT, complete=False)
    native_journal(args["home"], CHILD, ROOT)
    helper = adapter("native_delegation_accounting")
    helper.account("begin", correlation="first", **args)
    other = host_fixture(tmp_path, monkeypatch, root=args["project_root"], name="other")
    with pytest.raises(ValueError):
        helper.account("begin", correlation="overlap", **other)
    helper.account("finalize", correlation="first", **args)
    first = inclusive_report(
        args["project_root"], "topic", args["workflow_id"], issue_dir=args["issue_dir"]
    )
    assert not first["manager"]["native_usage"]["children"]
    helper.account("begin", correlation="second", **other)
    assert CostStore(args["project_root"], "other", other["workflow_id"]).read()["manager_gaps"][
        "second"
    ]


def test_unfinalized_native_entry_remains_a_durable_gap(tmp_path, monkeypatch):
    args = host_fixture(tmp_path, monkeypatch)
    native_journal(args["home"], ROOT, complete=False)
    helper = adapter("native_delegation_accounting")
    helper.account("begin", correlation="interrupted", **args)
    report = inclusive_report(
        args["project_root"], "topic", args["workflow_id"], issue_dir=args["issue_dir"]
    )
    assert report["manager"]["incomplete"]
    assert report["manager"]["native_usage"]["gaps"]


@pytest.mark.parametrize("child_complete", [True, False])
@pytest.mark.parametrize("same_timestamp", [True, False])
def test_host_finalize_excludes_reused_child_work_after_selected_owner_turn(
    tmp_path, monkeypatch, child_complete, same_timestamp
):
    args = host_fixture(tmp_path, monkeypatch)
    root_journal = native_journal(args["home"], ROOT, complete=False)
    helper = adapter("native_delegation_accounting")
    helper.account("begin", correlation="isolated", **args)
    child_journal = native_journal(args["home"], CHILD, ROOT, complete=child_complete)
    append_event(root_journal, "collab_agent_spawn_end", new_thread_id=CHILD)
    nested_journal = native_journal(args["home"], NESTED, CHILD, n=50)
    append_event(child_journal, "collab_agent_spawn_end", new_thread_id=NESTED)
    boundary = datetime.now(timezone.utc).isoformat()
    append_event(root_journal, "task_complete", observed_at=boundary, turn_id="owned-turn")
    foreign_time = boundary if same_timestamp else None
    append_event(
        root_journal, "task_started", observed_at=foreign_time, turn_id="foreign-root-turn"
    )
    append_event(
        root_journal,
        "collab_agent_interaction_end",
        observed_at=foreign_time,
        receiver_thread_id=CHILD,
    )
    for path, turn_id, n in (
        (child_journal, "foreign-child-turn", 500),
        (nested_journal, "foreign-nested-turn", 700),
    ):
        append_event(path, "task_started", observed_at=foreign_time, turn_id=turn_id)
        append_event(
            path, "token_count", observed_at=foreign_time, info=dict(total_token_usage=counters(n))
        )
        append_event(path, "task_complete", observed_at=foreign_time, turn_id=turn_id)
    helper.account("finalize", correlation="isolated", **args)
    report = inclusive_report(
        args["project_root"], "topic", args["workflow_id"], issue_dir=args["issue_dir"]
    )
    children = {r["session_id"]: r for r in report["manager"]["native_usage"]["children"]}
    child = children[CHILD]
    assert children[NESTED]["usage"]["input_tokens"] == 50
    assert children[NESTED]["native_usage"]["turn_ids"] == ["owned-turn"]
    native = child["native_usage"]
    assert child["usage"]["input_tokens"] == 100
    assert native["turn_ids"] == ["owned-turn"]
    assert native["ownership_cutoff"] < native["end"]["at"]
    assert child["complete"] == (child_complete and not same_timestamp)
    if same_timestamp:
        assert "ownership_boundary_ambiguous" in native["gaps"]
    if not child_complete:
        assert "child_active_at_cutoff" in native["gaps"]
