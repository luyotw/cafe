"""Preparation copies configured values; current decisions retain precedence."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_global_catalog")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def _project(tmp_path):
    cafe = tmp_path / ".cafe"
    (cafe / "playbooks").mkdir(parents=True)
    (cafe / "skills/custom-step").mkdir(parents=True)
    (cafe / "skills/custom-step/SKILL.md").write_text(
        "---\nname: custom-step\ndescription: Write an outline.\n---\n"
    )
    (cafe / "playbooks/example.yaml").write_text(
        "playbook: {id: example, conversation_locale: ja-JP}\n"
        "roles: {writer: {}}\nsteps:\n"
        "  outline: {role: writer, assignee_type: agent, skill: custom-step, on: {await_agent: _done}}\n"
    )
    (cafe / "phases.yaml").write_text(
        "outline:\n  name: Writer\n  role: writer\n  clis:\n  - {cli: codex, model: configured-model}\n"
    )
    (cafe / "strategic_context.yaml").write_text(
        "version: 1\nrepository_language: {content_locale: zh-TW}\n"
    )
    return {"schema_version": 1, "project_root": str(tmp_path), "issue_name": "new", "playbook_id": "example"}


def test_configured_values_are_written_to_the_actual_draft_without_granting_actions(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    report = module.assemble_kickoff(request)
    draft = module.preparation_template(request, report["formatter_draft"])["formatter_inputs"]
    assert draft["repository_content_locale"] == "zh-TW"
    assert draft["effective_locale"] == "ja-JP"
    assert draft["locale_source"] == "playbook:example"
    assert draft["phase_chain"] == ["outline=codex:configured-model"]
    assert draft["user_required"] == draft["manager_confirmable"] == []
    assert draft["proactive_review_decision"] == ["outline=not_required"]
    assert draft["need_permission"] == "user_required"
    assert draft["need_clarification"] == "manager_confirmable"
    assert draft["alignment_checkpoint"] == "manager_resolvable_when_clear"
    assert draft["deliver"] is None and draft["cleanup"] == [["cafe", "close"]]
    assert draft["delivery_contract"]["permissions"] == []
    assert draft["capability_choice"] == [] and draft["manager_mode"] == "event-driven"
    assert draft["current_checkout"] is True
    assert "phase_chain" in report["prefilled"]
    assert report["status"] == "incomplete"
    assert not (tmp_path / ".cafe/issues").exists()


@pytest.mark.parametrize("source,expected", [("explicit", "zh-TW"), ("inferred", "fr-FR")])
def test_schema_example_locale_uses_one_editable_map_and_preserves_precedence(tmp_path, source, expected):
    module = load_kickoff_module("kickoff_inputs")
    request = module.request_schema()["request_example"]
    request.update(_project(tmp_path))
    request.pop("preflight_files")
    assert "current_explicit_inputs" not in request
    request["formatter_inputs"]["locale_source"] = source
    prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(tmp_path / "prefs", repository_root=tmp_path)
    prefs.set("conversation.locale", "fr-FR", scope="user", origin="explicit")
    report = module.assemble_kickoff(request, preference_store=prefs)
    assert report["formatter_draft"]["effective_locale"] == expected
    assert report["formatter_draft"]["repository_content_locale"] == "en-US"
    assert report["formatter_draft"]["phase_chain"] == ["outline=codex:configured-model"]


def test_explicit_values_and_invalid_inputs_are_never_replaced_by_defaults(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    explicit = {"phase_chain": ["outline=codex:chosen-model"], "effective_locale": "fr-FR",
                "locale_source": "explicit", "repository_content_locale": "en-US",
                "need_clarification": "user_required", "user_required": [], "manager_confirmable": [],
                "proactive_review_decision": [], "deliver": [], "cleanup": [],
                "capability_choice": ["unknown=false"], "current_checkout": False}
    request["current_explicit_inputs"] = explicit
    report = module.assemble_kickoff(request)
    assert all(report["formatter_draft"][k] == v for k, v in explicit.items())
    assert report["formatter_draft"]["deliver_description"] == []
    assert report["formatter_draft"]["cleanup_description"] == []
    request["formatter_inputs"] = {"effective_locale": "de-DE"}
    conflict = module.assemble_kickoff(request)
    assert conflict["status"] == "invalid"
    assert "conflicting_explicit_input:effective_locale" in conflict["diagnostics"]


@pytest.mark.parametrize("mode,key,value,field", [
    ("attached", "manager.poll_interval_seconds", 15, "poll_interval_seconds"),
    ("event-driven", "manager.event_manager", ["codex"], "event_manager"),
])
def test_only_applicable_explicit_preferences_prefill_mode_dependencies(tmp_path, mode, key, value, field):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(tmp_path / "prefs", repository_root=tmp_path)
    prefs.set("manager.mode", mode, scope="user", origin="explicit")
    prefs.set(key, value, scope="user", origin="explicit")
    report = module.assemble_kickoff(request, preference_store=prefs)
    assert report["formatter_draft"][field] == value
    request["current_explicit_inputs"] = {"manager_mode": "unattended"}
    assert field not in module.assemble_kickoff(request, preference_store=prefs)["formatter_draft"]
    request["current_explicit_inputs"] = {"manager_mode": mode, field: None}
    assert module.assemble_kickoff(request, preference_store=prefs)["formatter_draft"][field] is None


@pytest.mark.parametrize("key,invalid,fields,resolution", [
    ("phase.chains", {"steps": {"absent": ["codex:model"]}}, ["phase_chain"], {"phase_chain": ["outline=codex:chosen"]}),
    ("worktree.convention", "{unknown}", ["worktree", "current_checkout"], {"current_checkout": True}),
    ("confirmation.assignments", {"mandatory_task": False}, ["user_required", "manager_confirmable"], {"user_required": [], "manager_confirmable": []}),
    ("review.decisions", {"absent": "required"}, ["proactive_review_decision"], {"proactive_review_decision": ["outline=not_required"]}),
    ("delivery.convention", {"wrong": []}, ["deliver", "deliver_description"], {"deliver": [], "deliver_description": []}),
    ("cleanup.convention", {"wrong": []}, ["cleanup", "cleanup_description"], {"cleanup": [], "cleanup_description": []}),
])
def test_invalid_saved_preference_requires_explicit_resolution_without_catalog_discovery(
    tmp_path, key, invalid, fields, resolution
):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    request["formatter_inputs"] = {"manager_mode": "unattended"}
    prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(
        tmp_path / "prefs", repository_root=tmp_path
    )
    prefs.set(key, invalid, scope="repository", origin="explicit")

    blocked = module.assemble_kickoff(request, preference_store=prefs)

    assert blocked["preferences"][key]["diagnostic"]
    assert any(key in row["requirement"] for row in blocked["missing_decisions"])
    assert all(field not in blocked["formatter_draft"] for field in fields)
    request["current_explicit_inputs"] = resolution
    repaired = module.assemble_kickoff(request, preference_store=prefs)
    assert not any(key in row["requirement"] for row in repaired["missing_decisions"])
    assert all(repaired["formatter_draft"][field] == value for field, value in resolution.items())


def test_changed_config_is_resolved_fresh_and_broken_config_stays_unresolved(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    config = tmp_path / ".cafe/phases.yaml"
    before = module.assemble_kickoff(request)
    config.write_text(config.read_text().replace("configured-model", "changed-model"))
    after = module.assemble_kickoff(request)
    assert before["formatter_draft"]["phase_chain"] != after["formatter_draft"]["phase_chain"]
    assert after["formatter_draft"]["phase_chain"] == ["outline=codex:changed-model"]
    config.write_text("outline: {clis: []}\n")
    broken = module.assemble_kickoff(request)
    assert "phase_chain" not in broken["formatter_draft"]
    assert any("resolve configured inputs" in gap["requirement"] for gap in broken["missing_decisions"])


def test_existing_workflow_locale_is_read_without_mutating_state(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    state = tmp_path / ".cafe/issues/new/blackboard.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"workflow_id": "existing", "conversation_locale": "de-DE",
                                 "conversation_locale_source": "explicit"}))
    before = state.read_bytes()
    report = module.assemble_kickoff(request)
    assert report["formatter_draft"]["effective_locale"] == "de-DE"
    assert state.read_bytes() == before


def test_partial_model_override_keeps_its_value_and_fills_other_custom_steps(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    playbook = tmp_path / ".cafe/playbooks/example.yaml"
    playbook.write_text(playbook.read_text().replace("await_agent: _done", "await_agent: compose") +
                        "  compose: {role: writer, assignee_type: agent, skill: custom-step, on: {await_agent: _done}}\n")
    config = tmp_path / ".cafe/phases.yaml"
    config.write_text(config.read_text() +
                      "compose: {name: Writer, role: writer, clis: [{cli: codex, model: second-model}]}\n")
    request["formatter_inputs"] = {"phase_chain": ["outline=claude:chosen-model"]}
    report = module.assemble_kickoff(request)
    assert report["formatter_draft"]["phase_chain"] == ["outline=claude:chosen-model", "compose=codex:second-model"]


def test_issue_id_derives_checkout_cleanup_and_current_cli_without_creating_them(tmp_path, monkeypatch):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    request.pop("issue_name")
    request["issue_id"] = 123
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/example/project.git"], check=True)
    monkeypatch.setenv("CODEX_THREAD_ID", "test-thread")
    report = module.assemble_kickoff(request)
    draft = report["formatter_draft"]
    assert draft["issue_name"] == "issue123"
    assert draft["worktree"] == str(tmp_path / ".cafe/worktrees/issue123")
    assert draft["cleanup"] == [["gh", "issue", "close", "123", "--repo", "example/project"], ["cafe", "close"]]
    assert len(draft["cleanup_description"]) == 2
    assert draft["manager_mode"] == "event-driven" and draft["event_manager"] == ["codex"]
    assert not (tmp_path / ".cafe/worktrees").exists()
    assert not (tmp_path / ".cafe/issues").exists()
    # A local name that resembles an issue number is not a GitHub binding.
    request.pop("issue_id")
    request["issue_name"] = "issue123"
    assert module.assemble_kickoff(request)["formatter_draft"]["cleanup"] == [["cafe", "close"]]


def test_cached_delivery_fills_only_missing_fields_on_a_valid_hit(tmp_path):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    request.update(issue_id="123", manager_cli="claude")
    template = {"deliver": [["deploy-tool", "--issue", "{issue_id}", "--workspace", "{worktree}"]],
                "deliver_description": ["Deliver {issue_name}."]}
    discovery = {"delivery": {"status": "hit", "delivery_template": template}}
    report = module.assemble_kickoff(request, discovery=discovery)
    draft = report["formatter_draft"]
    assert draft["deliver"] == [["deploy-tool", "--issue", "123", "--workspace", str(tmp_path)]]
    assert draft["deliver_description"] == ["Deliver new."]
    assert draft["event_manager"] == ["claude"]
    # An earlier incomplete draft has a null action slot, not an exclusion.
    request["formatter_inputs"] = {"deliver": None}
    assert module.assemble_kickoff(request, discovery=discovery)["formatter_draft"]["deliver"] == draft["deliver"]
    request.pop("formatter_inputs")
    request["current_explicit_inputs"] = {"deliver": [], "cleanup": [], "current_checkout": True,
                                          "manager_mode": "unattended"}
    overridden = module.assemble_kickoff(request, discovery=discovery)["formatter_draft"]
    assert overridden["deliver"] == overridden["cleanup"] == []
    assert overridden["deliver_description"] == overridden["cleanup_description"] == []
    assert "worktree" not in overridden and "event_manager" not in overridden
    request.pop("current_explicit_inputs")
    discovery["delivery"]["status"] = "miss"
    assert "deliver" not in module.assemble_kickoff(request, discovery=discovery)["formatter_draft"]


def test_default_mode_uses_saved_dependency_and_does_not_invent_caller_identity(tmp_path, monkeypatch):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    assert "event_manager" not in module.assemble_kickoff(request)["formatter_draft"]
    prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(tmp_path / "prefs", repository_root=tmp_path)
    prefs.set("manager.event_manager", ["claude"], scope="user", origin="explicit")
    assert module.assemble_kickoff(request, preference_store=prefs)["formatter_draft"]["event_manager"] == ["claude"]


@pytest.mark.parametrize("issue_id", [True, -1, "123; rm", "../123", "0"])
def test_issue_id_is_not_a_command_or_path_fragment(tmp_path, issue_id):
    module = load_kickoff_module("kickoff_inputs")
    with pytest.raises(ValueError, match="positive integer"):
        module.assemble_kickoff({"schema_version": 1, "project_root": str(tmp_path), "issue_id": issue_id})


@pytest.mark.parametrize("field,value", [("phase_models", ["outline=codex:chosen"]),
                                           ("event_manager", "not-an-array"),
                                           ("activate_confirmed", True)])
def test_invalid_field_preserves_prefill_and_can_be_repaired_in_one_place(tmp_path, field, value):
    module = load_kickoff_module("kickoff_inputs")
    request = _project(tmp_path)
    request["current_explicit_inputs"] = {field: value}
    report = module.assemble_kickoff(request)
    assert report["status"] != "ready" and report["diagnostics"]
    assert report["formatter_draft"]["phase_chain"] == ["outline=codex:configured-model"]
    draft = module.preparation_template(request, report["formatter_draft"])
    assert draft["formatter_inputs"]["manager_mode"] == "event-driven"
    assert draft["formatter_inputs"]["cleanup"] == [["cafe", "close"]]
    assert draft["current_explicit_inputs"][field] == value
    assert field not in draft["formatter_inputs"]  # No duplicate typo to repair.
    assert module.render_kickoff(report["formatter_draft"])["status"] == "invalid"
    del draft["current_explicit_inputs"][field]
    repaired = module.assemble_kickoff(draft)
    assert not repaired["diagnostics"]
    assert repaired["formatter_draft"]["phase_chain"] == report["formatter_draft"]["phase_chain"]
    assert repaired["formatter_draft"]["cleanup"] == report["formatter_draft"]["cleanup"]


def test_draft_without_playbook_preserves_preferences_until_selection(tmp_path, capsys):
    cli = load_kickoff_module("prepare_kickoff")
    _project(tmp_path)
    config, cache, draft = [tmp_path / name for name in ("config", "cache", "draft.json")]
    prefs = load_kickoff_module("kickoff_preferences").PreferenceStore(config, repository_root=tmp_path)
    prefs.set("manager.mode", "attached", scope="user", origin="explicit")
    prefs.set("manager.poll_interval_seconds", 20, scope="user", origin="explicit")
    assert cli.main(["draft", "--project-root", str(tmp_path), "--issue-name", "new",
                     "--output", str(draft), "--config-dir", str(config), "--cache-dir", str(cache)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["selected_playbook"] is None
    assert any(c["id"] == "example" for c in report["catalog"]["candidate_overview"])
    request = json.loads(draft.read_text())
    assert request["formatter_inputs"]["manager_mode"] == "attached"
    assert request["formatter_inputs"]["poll_interval_seconds"] == 20
    request["playbook_id"] = "example"
    inputs = load_kickoff_module("kickoff_inputs")
    result = inputs.assemble_kickoff(request, preference_store=prefs)
    assert result["formatter_draft"]["phase_chain"] == ["outline=codex:configured-model"]
    assert result["formatter_draft"]["manager_mode"] == "attached"
    assert not (tmp_path / ".cafe/issues").exists()
