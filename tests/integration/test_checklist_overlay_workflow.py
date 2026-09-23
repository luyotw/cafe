"""Production journeys for independently selected checklist overlays."""

from pathlib import Path

import pytest
import yaml

from cafe.playbooks.loader import PlaybookLoader


def write_skill(root, name, workflow=None, references=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "description": name, "workflow": workflow or {}}
    (directory / "SKILL.md").write_text("---\n" + yaml.safe_dump(data) + "---\n# Policy\n")
    for filename, content in (references or {}).items():
        target = directory / "references" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return directory


def overlay(reference="review.md", **extra):
    return {"checklist_overlay": {"variants": [{"sections": [{"reference": reference}]}], **extra}}


def test_author_validates_inactive_policy_and_primary_alternatives(tmp_path):
    """I01/U02/U04: strict public loading validates project overrides and all branches."""
    builtin = tmp_path / "builtin"
    project = tmp_path / "project"
    for name in ("first", "later"):
        write_skill(builtin / "skills", name)
    write_skill(builtin / "skills", "policy")
    policy = overlay(when={"feedback": True})
    policy["required_tools"] = ["Write"]
    policy["prompt_inputs"] = [{"artifacts": ["notes"], "placeholder": "notes_file", "required": True}]
    directory = write_skill(project / ".cafe/skills", "policy", policy, {"review.md": "[ ] Review\n"})
    playbook = {
        "playbook": {"id": "overlay", "applicability": {"summary": "test", "use_when": ["test"], "avoid_when": ["other"]}},
        "roles": {"operator": {}},
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "skills": {"workflow": {"shared": ["policy"]}, "chat": {"shared": []}},
        "steps": {"assemble": {"role": "operator", "skill": {"1": "first", "default": "later"}, "input_artifacts": ["notes"], "allowed_tools": ["Read", "Write"], "on": {"await_agent": "_done"}}},
    }
    folder = builtin / "playbooks"
    folder.mkdir(parents=True)
    path = folder / "overlay.yaml"
    loader = PlaybookLoader(project_root=project, global_root=tmp_path / "global", builtin_root=builtin)
    def validate():
        path.write_text(yaml.safe_dump(playbook))
        return loader.load_model("overlay", strict=True)
    validate()
    playbook["steps"]["assemble"]["allowed_tools"] = ["Read"]
    with pytest.raises(ValueError, match="Write"):
        validate()
    playbook["steps"]["assemble"]["allowed_tools"].append("Write")
    playbook["steps"]["assemble"]["input_artifacts"] = []
    with pytest.raises(ValueError, match="notes_file"):
        validate()
    playbook["steps"]["assemble"]["input_artifacts"] = ["notes"]
    (directory / "references/review.md").unlink()
    with pytest.raises(ValueError) as error:
        validate()
    assert all(token in str(error.value) for token in ("assemble", "policy", "checklist_overlay", "review.md"))
    (directory / "references/review.md").write_text("[ ] Review\n")
    write_skill(builtin / "skills", "later", {"checklist": {"variants": [{"sections": [{"reference": "missing.md"}]}]}})
    with pytest.raises(ValueError, match="missing.md"):
        validate()


def executor_fixture(tmp_path, monkeypatch, primary="primary", injections=("policy",), iteration=1):
    from types import SimpleNamespace
    from cafe.core.blackboard import BlackboardStore
    from cafe.phases.generic_phase import GenericPhase
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from cafe.skills.loader import SkillLoader
    from cafe.skills.native_bridge import NativeSkillBridge
    monkeypatch.chdir(tmp_path)
    loader = SkillLoader(project_root=tmp_path, global_root=tmp_path / "global", builtin_root=Path(__file__).resolve().parents[2] / "src/cafe/data")
    phase = GenericPhase(loader, skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"))
    step = {"skill": primary, "role": "developer", "on": {"await_agent": "_done"}, "allowed_tools": ["Read", "Write"]}
    playbook = {"playbook": {"id": "overlay"}, "skills": {"workflow": {"shared": list(injections)}, "chat": {"shared": []}}, "steps": {"assemble": step}}
    issue = tmp_path / ".cafe/issues/example"
    issue.mkdir(parents=True)
    executor = GenericWorkflowStepExecutor(issue_dir=issue, issue_name="example", playbook=playbook, generic_phase=phase, agent_manager=SimpleNamespace(), git_ops=SimpleNamespace(get_default_base_branch=lambda: "main"), role_agent_map={"developer": "David"})
    executor.phase_name = "assemble"
    executor.phase_dir = issue / "assemble"
    executor.iteration = iteration
    directory = executor.phase_dir / f"iteration_{iteration:03}"
    directory.mkdir(parents=True)
    state = BlackboardStore(issue).load_or_create("assemble")
    return executor, step, state, directory


def generate(executor, step, state, directory, **kwargs):
    executor._generate_checklist(step_name="assemble", skill_name=executor._resolve_skill_name(step, executor.iteration), agent_name="David", step_def=step, blackboard_state=state, checklist_file=directory / "checklist.md", output_file=directory / "output.md", questions_xml_file=directory / "questions.xml", **kwargs)
    return (directory / "checklist.md").read_text()


@pytest.mark.parametrize("legacy", [False, True])
def test_custom_fallback_appends_overlay_before_single_guidance(tmp_path, monkeypatch, legacy):
    """I03/U08: both empty and legacy fallbacks retain independent overlays."""
    root = tmp_path / ".cafe/skills"
    write_skill(root, "primary", references={"execution_steps_normal.md": "[ ] Legacy work\n"} if legacy else {})
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Independent policy\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory)
    assert content.count("[ ] Independent policy") == 1
    assert ("[ ] Legacy work" in content) == legacy
    assert content.count("## Agent Guidelines Checklist") <= 1
    if legacy:
        assert content.index("Legacy work") < content.index("Independent policy") < content.index("## Agent Guidelines Checklist")
    executor._save_user_input("continue")
    import json
    assert json.loads((directory / "iteration.json").read_text())["effective_checklist"]


@pytest.mark.parametrize("feedback", [False, True])
def test_real_develop_materializes_primary_and_independent_policy(tmp_path, monkeypatch, feedback):
    """I02: real Develop metadata retains normal/correction selection with overlays."""
    from cafe.core.blackboard import BlackboardStore
    root = tmp_path / ".cafe/skills"
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Independent policy\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch, primary="cafe-develop")
    store = BlackboardStore(executor.issue_dir)
    todo = tmp_path / "todo.md"
    todo.write_text("## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: implement — Closure: works — Evidence: tests\n")
    store.set_artifact(state, "plan", str(todo))
    if feedback:
        # The same metadata-driven causal path used by correction phases.
        executor.playbook["steps"]["inspect"] = {"skill": "cafe-review", "role": "reviewer", "output_artifact": "review_feedback", "output": {"artifact": "review_feedback"}, "on": {"await_agent": "assemble"}}
        from cafe.core.todo import TodoSourceArtifact
        # Causal routing gets dedicated production coverage in I05/I08; here use
        # the supplied causal artifact, just as an inbound materialized input.
        step["input_artifacts"] = ["plan", "causal_todo"]
        store.set_artifact(state, "causal_todo", str(todo))
    content = generate(executor, step, state, directory)
    assert content.count("[ ] Independent policy") == 1
    assert content.count("## Agent Guidelines Checklist") == 1
    assert content.index("Independent policy") < content.index("## Agent Guidelines Checklist")
    assert "PLAN-001" in content


@pytest.mark.parametrize("mutation", ["unchanged", "source", "content", "condition", "continuation", "missing_metadata", "corrupt_metadata"])
def test_interrupted_identical_gates_restore_only_proven_source(tmp_path, monkeypatch, mutation):
    """I04/U09/U10: identical text never transfers completion across sources."""
    import json
    from cafe.utils.checklist_validator import validate_checklist
    root = tmp_path / ".cafe/skills"
    write_skill(root, "primary", {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}}, {"work.md": "[ ] Same gate\n"})
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Same gate\n  Retain evidence\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory)
    path = directory / "checklist.md"
    # Only the overlay gate is complete, not the identical primary gate.
    path.write_text(content.replace("[ ] Same gate\n  Retain", "[x] Same gate\n  Retain"))
    if mutation == "source":
        write_skill(root, "replacement", overlay(), {"review.md": "[ ] Same gate\n  Retain evidence\n"})
        executor.playbook["skills"]["workflow"]["shared"] = ["replacement"]
    elif mutation in {"content", "continuation"}:
        (root / "policy/references/review.md").write_text("[ ] Same gate\n  Changed rule\n" if mutation == "continuation" else "[ ] Changed gate\n  Retain evidence\n")
    elif mutation == "condition":
        write_skill(root, "policy", overlay(when={"iteration": 1}), {"review.md": "[ ] Same gate\n  Retain evidence\n"})
    elif mutation == "missing_metadata":
        (directory / "iteration.json").unlink()
    elif mutation == "corrupt_metadata":
        (directory / "iteration.json").write_text('{"effective_checklist": {"version": 999}}')
    resumed = generate(executor, step, state, directory, preserve_completed_items=True)
    assert "[x] Same gate\n\n## Checklist source: policy" not in resumed
    assert resumed.count("[x]") == (1 if mutation == "unchanged" else 0)
    assert not validate_checklist(path).is_complete


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "alter", "reorder", "continuation"])
def test_success_validation_rejects_missing_or_changed_expected_gates(tmp_path, monkeypatch, mutation):
    """U09/I04: checking a partial or edited gate set is never complete."""
    from cafe.utils.checklist_validator import validate_checklist
    root = tmp_path / ".cafe/skills"
    write_skill(root, "primary", {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}}, {"work.md": "[ ] Primary\n"})
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Policy A\n  Required rule\n[ ] Policy B\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory).replace("[ ]", "[x]")
    path = directory / "checklist.md"
    path.write_text(content)
    assert validate_checklist(path).is_complete
    if mutation == "delete":
        content = content.replace("[x] Policy B\n", "")
    elif mutation == "duplicate":
        content += "[x] Policy B\n"
    elif mutation == "alter":
        content = content.replace("Policy B", "Other task")
    elif mutation == "reorder":
        content = content.replace("Policy A", "swap").replace("Policy B", "Policy A").replace("swap", "Policy B")
    else:
        content = content.replace("Required rule", "Weakened rule")
    path.write_text(content)
    assert not validate_checklist(path).is_complete
