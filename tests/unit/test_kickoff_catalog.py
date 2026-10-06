"""U07-U09: complete effective playbook discovery and dependency reuse."""

from __future__ import annotations

from pathlib import Path

import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module

pytestmark = pytest.mark.release_extended


def test_lightweight_selection_does_not_resolve_unselected_skill_catalog(tmp_path):
    import yaml

    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    playbooks = project / ".cafe/playbooks"
    playbooks.mkdir(parents=True)
    for name, mode in [("chosen", "compact"), ("other", "full")]:
        (playbooks / f"{name}.yaml").write_text(yaml.safe_dump({
            "playbook": {"id": name, "applicability": {
                "summary": name, "use_when": ["small change"], "avoid_when": ["research"]}},
            "contract": {"mode": mode},
            "roles": {"operator": {}},
            "steps": {"run": {"role": "operator", "skill": "missing-skill",
                               "on": {"await_agent": "_done"}}},
        }))
    kwargs = dict(project_root=project, global_root=tmp_path / "global",
                  builtin_root=tmp_path / "builtin", cache_file=tmp_path / "catalog.json")
    report = module.discover_index(**kwargs, lightweight=True)
    assert not report["diagnostics"]
    assert {item["id"]: item["contract_mode"] for item in report["candidates"]} == {
        "chosen": "compact", "other": "full"}
    assert all("profiles" not in item for item in report["candidates"])
    selected = module.discover_index(**kwargs, lightweight=True, selected_id="chosen")
    assert [item["id"] for item in selected["candidates"]] == ["chosen"]
    (playbooks / "chosen.yaml").write_text("contract: {mode: broken}\n")
    invalid = module.discover_index(**kwargs, lightweight=True, selected_id="chosen")
    assert not invalid["candidates"]
    assert invalid["diagnostics"][0]["id"] == "chosen"


@pytest.mark.parametrize("scope,key", [("roles", "operator"), ("steps", "first")])
def test_alias_overlay_cache_tracks_effective_skill_and_direct_override(tmp_path, scope, key):
    import yaml

    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"

    def write_skill(name, tool):
        path = project / f".cafe/skills/{name}/SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: Test\n"
            f"workflow: {{required_tools: [{tool}]}}\n---\n"
        )
        return path

    write_skill("plain", "Read")
    canonical = write_skill("cafe-workflow-common", "Agent")
    playbook = project / ".cafe/playbooks/custom.yaml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(yaml.safe_dump({
        "playbook": {
            "id": "custom",
            "applicability": {"summary": "Custom", "use_when": ["x"], "avoid_when": ["y"]},
        },
        "roles": {"operator": {}},
        "skills": {"workflow": {
            "shared": [], scope: {key: {"mode": "extend", "skills": ["workflow-common"]}},
        }},
        "steps": {"first": {
            "role": "operator", "skill": "plain", "allowed_tools": ["Read", "Agent"],
            "on": {"await_agent": "_done"},
        }},
    }))
    kwargs = dict(
        project_root=project, global_root=tmp_path / "global", builtin_root=tmp_path / "builtin",
        cache_file=tmp_path / "catalog.json",
    )

    def profile(report):
        candidate = next(item for item in report["candidates"] if item["id"] == "custom")
        assert candidate["eligible"] is True
        return candidate["native_subagent_steps"], candidate["profiles"]["first"]

    cold = module.discover_index(**kwargs)
    assert profile(cold)[0] == ["first"]
    assert profile(cold)[1]["skills"] == ["plain", "cafe-workflow-common"]
    assert module.discover_index(**kwargs)["reuse"]["custom"] is True

    canonical.write_text(canonical.read_text().replace("[Agent]", "[Read]"))
    changed = module.discover_index(**kwargs)
    assert changed["reuse"]["custom"] is False
    assert profile(changed)[0] == []
    assert profile(changed)[1]["required_tools"] == ["Read"]

    direct = write_skill("workflow-common", "Agent")
    overridden = module.discover_index(**kwargs)
    assert overridden["reuse"]["custom"] is False
    assert profile(overridden)[0] == ["first"]
    assert profile(overridden)[1]["skills"] == ["plain", "workflow-common"]

    canonical.write_text(canonical.read_text().replace("[Read]", "[Agent]"))
    assert module.discover_index(**kwargs)["reuse"]["custom"] is True

    direct.write_text(direct.read_text().replace("[Agent]", "[Read]"))
    refreshed = module.discover_index(**kwargs)
    assert refreshed["reuse"]["custom"] is False
    assert profile(refreshed)[0] == []
    assert profile(refreshed)[1]["required_tools"] == ["Read"]


@pytest.mark.parametrize("scope", ["shared", "roles", "steps"])
def test_index_normalizes_workflow_skill_names_and_tracks_their_dependencies(tmp_path, scope):
    import yaml
    from cafe.playbooks.loader import PlaybookLoader

    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = tmp_path / "builtin"
    for name, declaration in {"plain": "{}", "partner": "{required_tools: [Agent]}"}.items():
        skill = project / f".cafe/skills/{name}/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: {name}\ndescription: Test\nworkflow: {declaration}\n---\n"
        )
    workflow = {"shared": []}
    if scope == "shared":
        workflow[scope] = [" partner "]
    else:
        key = "operator" if scope == "roles" else "first"
        workflow[scope] = {key: {"mode": "extend", "skills": [" partner "]}}
    playbook = project / ".cafe/playbooks/custom.yaml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(yaml.safe_dump({
        "playbook": {
            "id": "custom",
            "applicability": {"summary": "Custom", "use_when": ["x"], "avoid_when": ["y"]},
        },
        "roles": {"operator": {}},
        "skills": {"workflow": workflow},
        "steps": {"first": {
            "role": "operator", "skill": "plain", "allowed_tools": ["Read", "Agent"],
            "on": {"await_agent": "_done"},
        }},
    }))
    kwargs = dict(project_root=project, global_root=global_root, builtin_root=builtin)
    loaded = PlaybookLoader(**kwargs).load_model("custom")
    assert module.resolve_playbook_skills(
        loaded.model, channel="workflow", role="operator", step_name="first",
    ) == ["partner"]

    kwargs["cache_file"] = tmp_path / "catalog.json"
    cold = module.discover_index(**kwargs)
    assert not cold["diagnostics"]
    candidate = next(item for item in cold["candidates"] if item["id"] == "custom")
    assert candidate["eligible"] is True
    assert candidate["profiles"]["first"]["skills"] == ["plain", "partner"]
    assert candidate["native_subagent_steps"] == ["first"]
    assert module.discover_index(**kwargs)["reuse"]["custom"] is True

    partner = project / ".cafe/skills/partner/SKILL.md"
    partner.write_text(partner.read_text().replace("[Agent]", "[Read]"))
    changed = module.discover_index(**kwargs)
    assert changed["reuse"]["custom"] is False
    candidate = next(item for item in changed["candidates"] if item["id"] == "custom")
    assert candidate["profiles"]["first"]["required_tools"] == ["Read"]
    assert candidate["native_subagent_steps"] == []
    assert module.discover_index(**kwargs)["reuse"]["custom"] is True


def test_native_subagent_requirements_include_variants_and_effective_overlays(tmp_path):
    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data"
    for name, declaration in {
        "plain": "{}",
        "delegate": "{required_tools: [Agent]}",
        "role-overlay": "{required_tools: [Read]}",
        "step-overlay": "{}",
    }.items():
        skill = project / f".cafe/skills/{name}/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: {name}\ndescription: Test\nworkflow: {declaration}\n---\n"
        )
    playbook = project / ".cafe/playbooks/custom.yaml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(
        "playbook: {id: custom, applicability: {summary: Custom, use_when: [x], avoid_when: [y]}}\n"
        "roles: {operator: {}}\n"
        "skills:\n  workflow:\n"
        "    shared: [delegate]\n"
        "    roles: {operator: {mode: replace, skills: [role-overlay]}}\n"
        "    steps: {overlay: {mode: extend, skills: [step-overlay]}}\n"
        "steps:\n"
        "  variant: {role: operator, skill: {'1': plain, default: delegate}, "
        "allowed_tools: [Read, Agent], on: {await_agent: overlay}}\n"
        "  overlay: {role: operator, skill: plain, allowed_tools: [Read, Agent], "
        "on: {await_agent: _done}}\n"
    )
    args = dict(project_root=project, global_root=tmp_path / "global", builtin_root=builtin,
                cache_file=tmp_path / "catalog.json")

    def custom(report):
        return next(item for item in report["candidates"] if item["id"] == "custom")

    cold = module.discover_index(**args)
    assert custom(cold)["native_subagent_steps"] == ["variant"]
    assert custom(cold)["profiles"]["overlay"]["required_tools"] == ["Read"]
    assert custom(cold)["profiles"]["overlay"]["skills"] == ["plain", "role-overlay", "step-overlay"]
    assert module.discover_index(**args)["reuse"]["custom"] is True

    overlay = project / ".cafe/skills/step-overlay/SKILL.md"
    overlay.write_text(overlay.read_text().replace("workflow: {}", "workflow: {required_tools: [Agent]}"))
    changed = module.discover_index(**args)
    assert changed["reuse"]["custom"] is False
    assert custom(changed)["native_subagent_steps"] == ["variant", "overlay"]

    role = project / ".cafe/skills/role-overlay/SKILL.md"
    role.write_text(role.read_text().replace("[Read]", "[Read, Agent]"))
    assert module.discover_index(**args)["reuse"]["custom"] is False


def test_builtin_subagent_flow_reports_planning_and_review_steps(tmp_path):
    module = load_kickoff_module("kickoff_catalog")
    report = module.discover_index(
        project_root=tmp_path / "project", global_root=tmp_path / "global",
        builtin_root=Path(__file__).resolve().parents[2] / "src/cafe/data",
        cache_file=tmp_path / "catalog.json",
    )
    for candidate_id in ("subagent-flow", "subagent-flow-qa"):
        candidate = next(item for item in report["candidates"] if item["id"] == candidate_id)
        assert candidate["native_subagent_steps"] == ["spec_plan", "develop"]


def test_index_keeps_effective_candidates_and_invalid_overlay_diagnostics(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data"
    custom = project / ".cafe/playbooks/custom.yaml"
    custom.parent.mkdir(parents=True)
    custom.write_text(
        "playbook: {id: custom, applicability: {summary: Custom, use_when: [x], avoid_when: [y]}}\n"
        "roles: {operator: {}}\nskills: {workflow: {shared: [cafe-workflow-common]}, chat: {shared: []}}\n"
        "steps:\n  first: {role: operator, skill: custom-step, on: {await_agent: _done}}\n",
        encoding="utf-8",
    )
    skill = project / ".cafe/skills/custom-step/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: custom-step\ndescription: Custom\nworkflow:\n"
        "  execution_profile:\n    workload: implementation\n    reasoning: high\n"
        "    risk_domains: [integration]\n    fallback_strength: equivalent\n---\n",
        encoding="utf-8",
    )
    invalid = project / ".cafe/playbooks/standard.yaml"
    invalid.write_text("playbook: {id: standard}\nsteps: []\n", encoding="utf-8")

    report = module.discover_index(
        project_root=project,
        global_root=global_root,
        builtin_root=builtin,
        cache_file=tmp_path / "catalog.json",
    )

    by_id = {candidate["id"]: candidate for candidate in report["candidates"]}
    assert "custom" in by_id, report["diagnostics"]
    assert by_id["custom"]["applicability"]["summary"] == "Custom"
    assert by_id["custom"]["steps"]["first"]["on"]["await_agent"] == "_done"
    assert set(by_id["custom"]["profiles"]["first"]["skills"]) == {
        "custom-step",
        "cafe-workflow-common",
    }
    assert any(item["id"] == "standard" and item["status"] == "invalid" for item in report["diagnostics"])
    assert by_id["custom"]["eligible"] is True


def test_index_exposes_mandatory_tasks_and_capability_setup_questions(tmp_path: Path) -> None:
    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data"

    report = module.discover_index(
        project_root=project,
        global_root=global_root,
        builtin_root=builtin,
        cache_file=tmp_path / "catalog.json",
    )
    standard = next(item for item in report["candidates"] if item["id"] == "standard")

    assert standard["mandatory_confirmation_gates"]
    assert "cafe.pr.publish" in standard["capability_requirements"]
    assert standard["capability_setup"]["cafe.pr.publish"]["setup_questions"]


def test_index_reuses_unchanged_candidate_and_invalidates_referenced_skill(
    tmp_path: Path,
) -> None:
    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data"
    skill = project / ".cafe/skills/custom-step/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: custom-step\ndescription: Custom\n---\n\n# Custom\n",
        encoding="utf-8",
    )
    playbook = project / ".cafe/playbooks/custom.yaml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(
        "playbook: {id: custom, applicability: {summary: Custom, use_when: [x], avoid_when: [y]}}\n"
        "roles: {operator: {}}\nsteps: {first: {role: operator, skill: custom-step, on: {await_agent: _done}}}\n",
        encoding="utf-8",
    )
    cache = tmp_path / "catalog.json"
    kwargs = {"project_root": project, "global_root": global_root, "builtin_root": builtin, "cache_file": cache}

    cold = module.discover_index(**kwargs)
    warm = module.discover_index(**kwargs)
    skill.write_text(skill.read_text(encoding="utf-8").replace("Custom", "Changed"), encoding="utf-8")
    changed = module.discover_index(**kwargs)

    assert "custom" in {candidate["id"] for candidate in cold["candidates"]}, cold["diagnostics"]
    assert warm["reuse"]["custom"] is True
    assert changed["reuse"]["custom"] is False


def test_membership_changes_are_observed_and_root_errors_are_not_complete(
    tmp_path: Path,
) -> None:
    module = load_kickoff_module("kickoff_catalog")
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    builtin = Path(__file__).resolve().parents[2] / "src/cafe/data"
    cache = tmp_path / "catalog.json"
    playbooks = project / ".cafe/playbooks"
    playbooks.mkdir(parents=True)
    first = playbooks / "first.yaml"
    template = (
        "playbook: {id: ID}\nroles: {operator: {}}\n"
        "steps: {first: {role: operator, skill: cafe-spec, on: {await_agent: _done}}}\n"
    )
    first.write_text(template.replace("ID", "first"), encoding="utf-8")
    kwargs = {"project_root": project, "global_root": global_root, "builtin_root": builtin, "cache_file": cache}

    before = module.discover_index(**kwargs)
    second = playbooks / "second.yaml"
    second.write_text(template.replace("ID", "second"), encoding="utf-8")
    after_add = module.discover_index(**kwargs)
    first.unlink()
    after_remove = module.discover_index(**kwargs)

    ids_before = {item["id"] for item in before["candidates"]}
    ids_added = {item["id"] for item in after_add["candidates"]}
    ids_removed = {item["id"] for item in after_remove["candidates"]}
    assert "first" in ids_before
    assert {"first", "second"}.issubset(ids_added)
    assert "first" not in ids_removed and "second" in ids_removed

    bad_project = tmp_path / "bad-project"
    bad_root = bad_project / ".cafe/playbooks"
    bad_root.parent.mkdir(parents=True)
    bad_root.symlink_to(playbooks, target_is_directory=True)
    failed = module.discover_index(
        project_root=bad_project,
        global_root=global_root,
        builtin_root=builtin,
        cache_file=tmp_path / "bad-catalog.json",
    )
    assert failed["candidates"] == []
    assert failed["diagnostics"]


@pytest.mark.release_smoke
def test_installed_catalog_binds_running_dependency_files_and_refuses_unknown_identity(tmp_path, monkeypatch):
    """U08/U09: deployed skill layout must invalidate on effective code changes."""
    import importlib.util
    import shutil
    import cafe.catalogs.resolver as resolver
    module = load_kickoff_module('kickoff_catalog')
    installed = tmp_path / 'site-packages/cafe/data/skills/use-cafe-workflow/scripts/kickoff_catalog.py'
    installed.parent.mkdir(parents=True)
    shutil.copyfile(module.__file__, installed)
    spec = importlib.util.spec_from_file_location('installed_kickoff_catalog', installed)
    deployed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deployed)
    dependency = tmp_path / 'effective-resolver.py'
    dependency.write_bytes(Path(resolver.__file__).read_bytes())
    monkeypatch.setattr(resolver, '__file__', str(dependency))
    args = dict(project_root=tmp_path / 'project', global_root=tmp_path / 'global',
                builtin_root=Path(__file__).resolve().parents[2] / 'src/cafe/data', cache_file=tmp_path / 'cache.json')
    cold = deployed.discover_index(**args)
    assert cold['candidates']
    assert all(deployed.discover_index(**args)['reuse'].values())
    dependency.write_text(dependency.read_text() + '\n# changed effective implementation\n')
    changed = deployed.discover_index(**args)
    assert not any(changed['reuse'].values())
    dependency.unlink()
    missing = deployed.discover_index(**args)
    again = deployed.discover_index(**args)
    assert not any(missing['reuse'].values()) and not any(again['reuse'].values())
    assert missing['diagnostics'] and again['diagnostics']
