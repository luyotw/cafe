"""Plan I3/I4/I5/U2/U8/U10: admitted native host delegation and retention."""

import json
from datetime import datetime, timezone

import pytest

from cafe.manager._schema import freshness_semantic_facts
from cafe.manager._store import load_contract
from cafe.manager.costs import CostStore, inclusive_report
from tests.fixtures.manager_chat import adapter, issue, repository
from tests.integration.test_codex_descendant_accounting import native_journal
from tests.unit.test_codex_subagent_usage import CHILD, ROOT


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


def append_event(path, kind, **payload):
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                dict(
                    timestamp=datetime.now(timezone.utc).isoformat(),
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
