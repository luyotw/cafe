"""U07-U09: complete effective playbook discovery and dependency reuse."""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


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
