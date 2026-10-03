"""The final preference offer is project-scoped, visible, and saved only by selection."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


@pytest.fixture
def case(tmp_path):
    offer = load_kickoff_module("kickoff_preference_offer")
    prefs = load_kickoff_module("kickoff_preferences")
    project = tmp_path / "project"
    project.mkdir()
    store = prefs.PreferenceStore(tmp_path / "config", repository_root=project)
    args = SimpleNamespace(project_root=project, issue_name="ticket-example", worktree=None,
                           deliver_description=[], cleanup_description=[])
    model = SimpleNamespace(steps={
        "outline": SimpleNamespace(role="writer"), "compose": SimpleNamespace(role="writer"),
        "publish": SimpleNamespace(role="publisher")})
    proposal = {
        "locales": {"conversation": {"value": "zh-TW"}},
        "manager": {"mode": "event-driven", "clis": [{"cli": "codex"}]},
        "phases": [{"name": name, "chain": [{"cli": "codex", "model": "primary"},
                                               {"cli": "claude", "model": "backup"}]}
                   for name in model.steps],
        "confirmation_contract": {"user_required": [], "manager_confirmable": [],
                                  "mandatory_human_stops": ["publish"]},
        "proactive_review": {"phase_decisions": [{"phase": name, "decision": "not_required"}
                                                   for name in model.steps]},
        "delivery_contract": {"closeout_plan": {"deliver": [], "cleanup": []},
                              "permissions": ["one-off permission"], "outcome": "only this issue"},
    }
    return offer, store, args, proposal, model


def build(case, **kwargs):
    module, store, args, proposal, model = case
    return module.build_offer(args, proposal, model, store=store, **kwargs)


def test_offer_groups_identical_chains_and_never_contains_issue_authority(case):
    module, store, args, proposal, _ = case
    before = copy.deepcopy(proposal)
    result = build(case)
    formatter = load_kickoff_module("format_kickoff_contract")
    for zh, plain, remember in ((True, "**確認**", "**確認並記住**"),
                                 (False, "**Confirm**", "**Confirm and remember**")):
        section, prompt = module.render_offer(result, zh=zh, table=formatter._table)
        assert "outline, compose, publish" in section
        assert "codex:primary → claude:backup" in section
        assert section.count("codex:primary") == 1
        assert plain in prompt and remember in prompt
    assert all(e["key"] not in {"permissions", "playbook_id", "pr.auto_create"} for e in result["entries"])
    assert "one-off permission" not in json.dumps(result)
    assert not store._store("repository").path.exists()
    assert before == proposal


def test_role_coverage_and_step_override_keep_ordered_fallbacks(case):
    module, store, _, _, _ = case
    store.set("phase.chains", {"roles": {"writer": ["codex:primary", "claude:backup"]},
                               "steps": {"publish": ["codex:old"]}}, scope="repository", origin="explicit")
    result = build(case)
    phases = [e for e in result["entries"] if e["key"] == "phase.chains"]
    assert [e["selector"] for e in phases] == ["publish"]
    assert phases[0]["previous"]["value"] == ["codex:old"]
    assert phases[0]["value"] == ["codex:primary", "claude:backup"]
    module.remember_offer(store, result, selections=["phase.chains/publish"], reuse=True)
    saved = store.inspect(scope="repository")["phase.chains"]["value"]
    assert saved["roles"] == {"writer": ["codex:primary", "claude:backup"]}
    assert saved["steps"]["publish"] == phases[0]["value"]


def test_all_same_omits_section_and_bare_confirmation_does_not_save(case):
    module, store, _, _, _ = case
    result = build(case)
    assert module.remember_offer(store, result, selections=["*"], reuse=False)["stored"] is False
    assert not store._store("repository").path.exists()
    module.remember_offer(store, result, selections=["*"], reuse=True)
    same = build(case)
    assert same["entries"] == []
    assert module.render_offer(same, zh=True, table=lambda *_: "unused") == ("", None)


def test_partial_save_preserves_peer_updates_and_never_copies_user_preferences(case):
    module, store, _, _, _ = case
    store.set("phase.chains", {"roles": {"publisher": ["codex:other"]}}, scope="user", origin="explicit")
    result = build(case)
    store.set("phase.chains", {"steps": {"publish": ["codex:peer"]}}, scope="repository", origin="explicit")
    module.remember_offer(store, result, selections=["phase.chains/compose"], reuse=True)
    saved = store.inspect(scope="repository")
    assert set(saved) == {"phase.chains"}
    assert saved["phase.chains"]["value"] == {"steps": {
        "publish": ["codex:peer"], "compose": ["codex:primary", "claude:backup"]}}


def test_changed_selected_value_rejects_entire_batch_without_partial_save(case):
    module, store, _, _, _ = case
    result = build(case)
    store.set("phase.chains", {"steps": {"compose": ["codex:peer"]}}, scope="repository", origin="explicit")
    before = store._store("repository").path.read_bytes()
    with pytest.raises(ValueError, match="changed since display"):
        module.remember_offer(store, result, selections=["conversation.locale", "phase.chains/compose"], reuse=True)
    assert store._store("repository").path.read_bytes() == before


@pytest.mark.parametrize("alter", ["project", "store", "value", "selection", "forbidden"])
def test_wrong_or_modified_offer_cannot_save(case, tmp_path, alter):
    module, store, _, _, _ = case
    result = build(case)
    selections = ["*"]
    if alter == "project":
        result["repository_identity"] = "path:/other/project"
    elif alter == "store":
        result["config_dir"] = str(tmp_path / "other-config")
    elif alter == "value":
        result["entries"][0]["value"] = "en-US"
    elif alter == "selection":
        selections = ["not-displayed"]
    else:
        result["entries"][0].update(key="permission", id="permission")
        result["offer_id"] = module.fingerprint({k: v for k, v in result.items() if k != "offer_id"})
    with pytest.raises(ValueError):
        module.remember_offer(store, result, selections=selections, reuse=True)
    assert not store._store("repository").path.exists()


@pytest.mark.parametrize("value", [None, {"steps": {"absent": ["codex:old"]}}, {"roles": "invalid"}])
def test_bad_saved_preference_is_reported_not_offered_as_missing(case, value):
    module, store, _, _, _ = case
    store.set("phase.chains", value, scope="repository", origin="explicit")
    result = build(case)
    assert "phase.chains" in result["problems"]
    assert all(e["key"] != "phase.chains" for e in result["entries"])
    section, prompt = module.render_offer(result, zh=False, table=lambda *_: "rows")
    assert "does not block kickoff" in section and prompt


def test_corrupt_store_is_not_overwritten(case):
    module, store, _, _, _ = case
    snapshot = build(case)
    path = store._store("repository").path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken JSON")
    result = build(case)
    assert result["entries"] == [] and "store" in result["problems"]
    with pytest.raises(ValueError):
        module.remember_offer(store, snapshot, selections=["*"], reuse=True)
    assert path.read_text() == "broken JSON"


def test_literal_issue_templates_are_not_offered_but_valid_templates_are(case):
    _, _, args, proposal, _ = case
    args.worktree = str(args.project_root / "tasks" / args.issue_name)
    args.deliver_description = ["Deliver {issue_name}".format(issue_name=args.issue_name)]
    proposal["delivery_contract"]["closeout_plan"]["deliver"] = [{"argv": ["deliver-tool", args.issue_name]}]
    bad = build(case, templates={
        "worktree.convention": args.worktree,
        "delivery.convention": {"deliver": [["deliver-tool", args.issue_name]],
                                "deliver_description": args.deliver_description}})
    assert {"worktree.convention", "delivery.convention"} <= set(bad["problems"])
    assert not any(e["key"] in {"worktree.convention", "delivery.convention"} for e in bad["entries"])
    good = build(case, templates={
        "worktree.convention": "{project_root}/tasks/{issue_name}",
        "delivery.convention": {"deliver": [["deliver-tool", "{issue_name}"]],
                                "deliver_description": ["Deliver {issue_name}"]}})
    assert good["problems"] == {}
    assert {"worktree.convention", "delivery.convention"} <= {e["key"] for e in good["entries"]}


def test_default_cleanup_does_not_save_literal_issue_description(case):
    _, _, args, proposal, _ = case
    proposal["delivery_contract"]["closeout_plan"]["cleanup"] = [{"argv": ["cafe", "close"]}]
    args.cleanup_description = [f"Archive {args.issue_name}"]
    result = build(case)
    assert "cleanup.convention" in result["unavailable_templates"]
    assert all(e["key"] != "cleanup.convention" for e in result["entries"])
