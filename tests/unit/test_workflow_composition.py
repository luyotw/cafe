"""Invariant coverage for source-aware workflow declaration composition."""

from pathlib import Path
from threading import Event, Thread

import pytest

from cafe.catalogs.resolver import global_catalog_lock
from cafe.core.playbook import resolve_playbook_skills
from cafe.skills.contracts import SkillWorkflowDeclaration
from cafe.skills.loader import SkillLoader
from cafe.skills.workflow_composition import (
    WorkflowCompositionError,
    resolve_step_workflow_composition,
)


def _write_skill(root: Path, name: str, workflow: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    block = f"workflow:\n{workflow}" if workflow else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n{block}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _loader(tmp_path: Path) -> SkillLoader:
    return SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )


def test_stable_environment_order_deduplicates_primary_and_removed_skills(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    for name in ("primary", "role-only", "step-only"):
        _write_skill(root, name, f"  required_tools: [{name}]\n")
    playbook = {
        "skills": {
            "workflow": {
                "shared": ["primary", "removed"],
                "roles": {"developer": {"mode": "replace", "skills": ["role-only"]}},
                "steps": {
                    "develop": {
                        "mode": "extend",
                        "skills": ["primary", "step-only", "role-only"],
                    }
                },
            }
        }
    }

    contributors = resolve_playbook_skills(
        playbook, channel="workflow", role="developer", step_name="develop"
    )
    composition = resolve_step_workflow_composition(
        _loader(tmp_path),
        primary_skill="primary",
        workflow_skills=contributors,
        step_name="develop",
    )

    assert composition.skill_names == ("primary", "role-only", "step-only")
    assert composition.required_tools == ("primary", "role-only", "step-only")


def test_provenance_uses_selected_catalog_entry_and_declaration_file(tmp_path: Path) -> None:
    _write_skill(tmp_path / "global" / "skills", "primary")
    _write_skill(tmp_path / "project" / ".cafe" / "skills", "primary")

    composition = resolve_step_workflow_composition(
        _loader(tmp_path), primary_skill="primary", step_name="develop"
    )
    source = composition.contributors[0].source

    assert source.skill_identity == "primary"
    assert source.catalog_source == "project"
    assert source.skill_root == tmp_path / "project" / ".cafe" / "skills" / "primary"
    assert source.declaration_file == source.skill_root / "SKILL.md"
    assert source.field_location == "workflow"


@pytest.mark.parametrize(
    "overlay_input",
    [
        "    artifacts: [other, spec]\n    placeholder: spec_file\n",
        "    artifacts: [spec, other]\n    placeholder: spec_file\n    required: true\n",
        "    artifacts: [spec, other]\n    placeholder: spec_file\n"
        "    load_policy:\n      - when: {feedback: true}\n        mode: packet\n"
        "        contract_kind: spec\n",
    ],
)
def test_prompt_inputs_deduplicate_only_complete_normalized_matches(
    tmp_path: Path, overlay_input: str
) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    declared = "  prompt_inputs:\n  - artifacts: [spec, other]\n    placeholder: spec_file\n"
    _write_skill(root, "primary", declared)
    _write_skill(root, "overlay", "  prompt_inputs:\n  - " + overlay_input.lstrip())

    with pytest.raises(WorkflowCompositionError) as exc_info:
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=["overlay"],
            step_name="develop",
        )

    message = str(exc_info.value)
    assert all(token in message for token in ("develop", "prompt_inputs", "spec_file"))
    assert "primary" in message and "overlay" in message
    assert "SKILL.md" in message


def test_equal_prompt_inputs_and_human_tasks_deduplicate(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    workflow = """  prompt_inputs:
  - artifacts: [spec, other]
    placeholder: spec_file
  human_tasks:
  - id: approve
    pattern: confirm_output
    prompt: Approve?
    input_schema: decision
    decisions:
    - {id: accept, label: Accept}
"""
    _write_skill(root, "primary", workflow)
    _write_skill(root, "overlay", workflow)

    composition = resolve_step_workflow_composition(
        _loader(tmp_path),
        primary_skill="primary",
        workflow_skills=["overlay"],
        step_name="develop",
    )

    assert len(composition.prompt_inputs) == 1
    assert len(composition.human_tasks) == 1


def test_human_task_conflict_is_source_aware(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    base = """  human_tasks:
  - id: approve
    pattern: confirm_output
    prompt: %s
    input_schema: decision
    decisions:
    - {id: accept, label: Accept}
"""
    _write_skill(root, "primary", base % "Approve?")
    _write_skill(root, "overlay", base % "Accept?")

    with pytest.raises(WorkflowCompositionError, match="human_tasks.*approve.*primary.*overlay"):
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=["overlay"],
            step_name="develop",
        )


def test_tools_and_profiles_aggregate_without_defaulting_empty_overlay(tmp_path: Path) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(
        root,
        "primary",
        """  required_tools: [Read]
  execution_profile:
    workload: implementation
    reasoning: routine
    risk_domains: [state-change]
    fallback_strength: equivalent
""",
    )
    _write_skill(root, "empty", "  required_tools: [Read, Write]\n")
    _write_skill(
        root,
        "strong",
        """  required_tools: [Bash]
  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [integration, state-change]
    fallback_strength: equivalent_or_stronger
""",
    )

    composition = resolve_step_workflow_composition(
        _loader(tmp_path),
        primary_skill="primary",
        workflow_skills=["empty", "strong"],
        step_name="develop",
    )

    assert composition.required_tools == ("Read", "Write", "Bash")
    assert composition.execution_requirements.workloads == ("implementation", "review")
    assert composition.execution_requirements.reasoning == "high"
    assert composition.execution_requirements.risk_domains == (
        "state-change",
        "integration",
    )
    assert composition.execution_requirements.fallback_strength == "equivalent_or_stronger"
    assert not composition.execution_requirements.uses_default


@pytest.mark.parametrize("field", ["prompt_references", "output_templates"])
def test_contributor_cannot_claim_primary_owned_metadata(tmp_path: Path, field: str) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary")
    if field == "prompt_references":
        workflow = "  prompt_references: {guide: guide.md}\n"
        refs = root / "overlay" / "references"
        refs.mkdir(parents=True)
        (refs / "guide.md").write_text("guide", encoding="utf-8")
    else:
        workflow = "  output_templates: {catalog: reports}\n"
        (root / "overlay" / "assets" / "templates").mkdir(parents=True)
    _write_skill(root, "overlay", workflow)

    with pytest.raises(WorkflowCompositionError) as exc_info:
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=["overlay"],
            step_name="develop",
        )

    assert all(token in str(exc_info.value) for token in ("develop", field, "overlay"))


def test_resolution_is_metadata_only_and_chat_skills_are_not_implicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary", "  required_tools: [Read]\n")
    _write_skill(root, "workflow", "  required_tools: [Write]\n")
    _write_skill(root, "chat", "  required_tools: [Forbidden]\n")
    loader = _loader(tmp_path)
    monkeypatch.setattr(loader, "activate", lambda *_args, **_kwargs: pytest.fail("body read"))
    monkeypatch.setattr(
        loader, "get_reference", lambda *_args, **_kwargs: pytest.fail("reference read")
    )

    composition = resolve_step_workflow_composition(
        loader,
        primary_skill="primary",
        workflow_skills=["workflow"],
        step_name="develop",
    )

    assert composition.required_tools == ("Read", "Write")
    assert "chat" not in composition.skill_names


def test_composition_holds_catalog_lock_through_ownership_and_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary")
    _write_skill(root, "support", "  required_tools: [Read]\n")
    loader = _loader(tmp_path)
    validation_started = Event()
    allow_validation = Event()
    publisher_acquired = Event()
    errors: list[BaseException] = []
    original_validate = loader.validate_workflow_declaration_resources

    def pause_validation(skill_dir: Path, declaration: SkillWorkflowDeclaration) -> None:
        validation_started.set()
        assert allow_validation.wait(timeout=5)
        original_validate(skill_dir, declaration)

    def read() -> None:
        try:
            resolve_step_workflow_composition(
                loader,
                primary_skill="primary",
                workflow_skills=["support"],
                step_name="develop",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def publish() -> None:
        with global_catalog_lock(loader.global_root, exclusive=True):
            publisher_acquired.set()

    monkeypatch.setattr(loader, "validate_workflow_declaration_resources", pause_validation)
    reader = Thread(target=read)
    publisher = Thread(target=publish)
    reader.start()
    assert validation_started.wait(timeout=5)
    publisher.start()
    assert not publisher_acquired.wait(timeout=0.1)

    allow_validation.set()
    reader.join(timeout=5)
    publisher.join(timeout=5)

    assert not reader.is_alive()
    assert not publisher.is_alive()
    assert publisher_acquired.is_set()
    assert errors == []


def _overlay(root, name, local="local", extra=""):
    _write_skill(
        root,
        name,
        extra + f"""  checklist_overlay:
    when: {{feedback: true}}
    context_references: {{{local}: context.md}}
    variants:
    - sections: [{{reference: review.md}}, {{optional_checklist: absent.md}}]
""",
    )
    refs = root / name / "references"
    refs.mkdir()
    (refs / "context.md").write_text(name)
    (refs / "review.md").write_text("[ ] {" + local + "}\n")


def test_overlay_locals_are_isolated_and_inactive_requirements_survive(tmp_path):
    """U03/U04: repeated locals are legal; applicability cannot remove requirements."""
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary")
    _overlay(root, "one", extra="  required_tools: [Write]\n")
    _overlay(root, "two", extra="  execution_profile: {workload: review, reasoning: high}\n")
    result = resolve_step_workflow_composition(
        _loader(tmp_path),
        primary_skill="primary",
        workflow_skills=["one", "two"],
        step_name="assemble",
    )
    assert result.required_tools == ("Write",)
    assert result.execution_requirements.reasoning == "high"
    assert result.skill_names == ("primary", "one", "two")


@pytest.mark.parametrize("reverse", [False, True])
def test_overlay_local_cannot_shadow_later_global_input(tmp_path, reverse):
    """U03: build the reserved global set before checking any contributor."""
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary")
    _overlay(root, "local-policy")
    _write_skill(
        root, "global-policy", "  prompt_inputs:\n  - {artifacts: [notes], placeholder: local}\n"
    )
    names = ["local-policy", "global-policy"]
    with pytest.raises(WorkflowCompositionError) as error:
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=names[::-1] if reverse else names,
            step_name="assemble",
        )
    assert all(
        token in str(error.value)
        for token in ("assemble", "local-policy", "global-policy", "local")
    )


def test_inactive_overlay_missing_reference_is_source_aware(tmp_path):
    """U02: every required reference is checked, while optional files stay optional."""
    root = tmp_path / "project" / ".cafe" / "skills"
    _write_skill(root, "primary")
    _overlay(root, "policy")
    (root / "policy" / "references" / "review.md").unlink()
    with pytest.raises(ValueError) as error:
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=["policy"],
            step_name="assemble",
        )
    assert all(
        token in str(error.value)
        for token in ("assemble", "policy", "checklist_overlay", "review.md")
    )


@pytest.mark.parametrize(
    "key",
    [
        "current_step",
        "issue_dir",
        "iteration_dir",
        "input_loading_modes",
        "valid_baton_intents",
        "workflow_feedback_batch_file",
    ],
)
def test_inactive_overlay_cannot_shadow_values_added_by_runtime(tmp_path, key):
    """U03: runtime-owned locals are reserved even when not yet rendered."""
    root = tmp_path / "project" / ".cafe/skills"
    _write_skill(root, "primary")
    _overlay(root, "policy", local=key)
    with pytest.raises(ValueError) as error:
        resolve_step_workflow_composition(
            _loader(tmp_path),
            primary_skill="primary",
            workflow_skills=["policy"],
            step_name="assemble",
        )
    assert key in str(error.value) and "policy" in str(error.value)
