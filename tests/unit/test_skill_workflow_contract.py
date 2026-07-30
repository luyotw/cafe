"""Tests for skill-owned workflow metadata contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cafe.skills.checklist_composer import compose_declared_checklist
from cafe.skills.contracts import (
    DeclaredArtifactError,
    SkillWorkflowContract,
    resolve_prompt_inputs,
)
from cafe.skills.loader import SkillLoader


def _contract_data() -> dict:
    return {
        "prompt_inputs": [
            {
                "artifacts": ["research_notes", "legacy_notes"],
                "placeholder": "evidence_file",
                "required": True,
            },
            {
                "artifacts": ["optional_review"],
                "placeholder": "review_file",
                "required": False,
            },
        ],
        "prompt_references": {"optional_review_instruction": "optional_review.md"},
        "checklist": {
            "context_references": {"xml_questions_instruction": "xml_questions.md"},
            "variants": [
                {
                    "when": {"iteration": 1},
                    "sections": [{"reference": "execution_first.md"}],
                },
                {
                    "when": {"min_iteration": 2},
                    "sections": [{"reference": "execution_later.md"}],
                },
            ],
            "include_role_guidance": True,
        },
        "output_templates": {"catalog": "research-report"},
    }


def test_workflow_contract_parses_declared_inputs_checklists_and_templates() -> None:
    """Valid metadata keeps declared ordering and the custom catalog intact."""
    contract = SkillWorkflowContract.model_validate(_contract_data())

    assert contract.prompt_inputs[0].artifacts == ("research_notes", "legacy_notes")
    assert contract.prompt_references == {"optional_review_instruction": "optional_review.md"}
    assert contract.checklist is not None
    assert contract.checklist.variants[1].when.min_iteration == 2
    assert contract.output_templates is not None
    assert contract.output_templates.catalog == "research-report"


def test_workflow_contract_parses_reusable_human_task_defaults() -> None:
    """Skills may own reusable response shape and participant-facing copy."""
    contract = SkillWorkflowContract.model_validate(
        {
            "human_tasks": [
                {
                    "id": "review-output",
                    "pattern": "confirm_output",
                    "prompt": "Review the proposed brief",
                    "input_schema": "decision",
                    "decisions": [
                        {"id": "confirm", "label": "Approve"},
                        {
                            "id": "revise",
                            "label": "Request revision",
                            "requires_feedback": True,
                        },
                    ],
                }
            ]
        }
    )

    assert contract.human_tasks[0].id == "review-output"


@pytest.mark.parametrize(
    "placeholder",
    ["output_file", "checklist_file", "questions_xml_file", "next_step_path"],
)
def test_workflow_contract_rejects_runtime_owned_input_placeholders(placeholder: str) -> None:
    """Skill inputs cannot replace locations that the runtime exclusively owns."""
    data = _contract_data()
    data["prompt_inputs"][0]["placeholder"] = placeholder

    with pytest.raises(ValidationError, match="runtime-owned"):
        SkillWorkflowContract.model_validate(data)


@pytest.mark.parametrize(
    "placeholder",
    ["output_file", "checklist_file", "questions_xml_file", "next_step_path"],
)
def test_workflow_contract_rejects_runtime_owned_context_reference_placeholders(
    placeholder: str,
) -> None:
    """Checklist references cannot replace values supplied by the runtime."""
    data = _contract_data()
    data["checklist"]["context_references"] = {placeholder: "xml_questions.md"}

    with pytest.raises(ValidationError, match="runtime-owned"):
        SkillWorkflowContract.model_validate(data)


def test_workflow_contract_rejects_context_reference_that_overlaps_prompt_input() -> None:
    """Context references cannot overwrite declared artifact input values."""
    data = _contract_data()
    data["checklist"]["context_references"] = {"evidence_file": "evidence.md"}

    with pytest.raises(ValidationError, match="must not overlap"):
        SkillWorkflowContract.model_validate(data)


def test_workflow_contract_rejects_prompt_reference_that_overlaps_prompt_input() -> None:
    """Prompt references reserve their own marker names."""
    data = _contract_data()
    data["prompt_references"] = {"evidence_file": "evidence.md"}

    with pytest.raises(ValidationError, match="must not overlap"):
        SkillWorkflowContract.model_validate(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["prompt_inputs"].append(
            {
                "artifacts": ["other"],
                "placeholder": "evidence_file",
                "required": True,
            }
        ),
        lambda data: data["checklist"]["variants"][0]["sections"].__setitem__(
            0, {"reference": "../outside.md"}
        ),
        lambda data: data["checklist"].__setitem__("unexpected", True),
        lambda data: data["checklist"]["variants"].__setitem__(
            0, {"when": {"iteration": 1}}
        ),
    ],
)
def test_workflow_contract_rejects_ambiguous_or_unsafe_declarations(mutate) -> None:
    """Authors receive a validation failure for invalid contract fields."""
    data = _contract_data()
    mutate(data)

    with pytest.raises(ValidationError):
        SkillWorkflowContract.model_validate(data)


def test_declared_inputs_use_first_available_artifact_and_omit_optional_absences() -> None:
    """Artifact resolution is deterministic and never invents legacy context keys."""
    contract = SkillWorkflowContract.model_validate(_contract_data())

    resolved = resolve_prompt_inputs(
        contract,
        {"legacy_notes": ".cafe/issues/42/research/notes.md"},
    )

    assert resolved == {"evidence_file": ".cafe/issues/42/research/notes.md"}


def test_declared_inputs_report_missing_required_mapping_before_execution() -> None:
    """A missing required input names the contract placeholder and candidates."""
    contract = SkillWorkflowContract.model_validate(_contract_data())

    with pytest.raises(DeclaredArtifactError) as exc_info:
        resolve_prompt_inputs(contract, {})

    assert exc_info.value.placeholder == "evidence_file"
    assert exc_info.value.artifacts == ("research_notes", "legacy_notes")


def test_custom_contract_composes_selected_variant_and_explicit_role_guidance(
    tmp_path, monkeypatch
) -> None:
    """A custom skill owns first/later checklist choice and role-guidance opt-in."""
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "synthesis"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: synthesis\ndescription: synthesis\n---\n", encoding="utf-8"
    )
    (skill_dir / "references" / "first.md").write_text(
        "[ ] Read {evidence_file}\n", encoding="utf-8"
    )
    (skill_dir / "references" / "later.md").write_text(
        "[ ] Revise {evidence_file}\n", encoding="utf-8"
    )
    contract = SkillWorkflowContract.model_validate(
        {
            "prompt_inputs": [
                {"artifacts": ["research"], "placeholder": "evidence_file", "required": True}
            ],
            "checklist": {
                "variants": [
                    {"when": {"iteration": 1}, "sections": [{"reference": "first.md"}]},
                    {"when": {"min_iteration": 2}, "sections": [{"reference": "later.md"}]},
                ],
                "include_role_guidance": False,
            },
        }
    )
    loader = SkillLoader(project_root=tmp_path)
    loader.discover()
    monkeypatch.setattr(
        "cafe.skills.checklist_composer.AgentManager.get_agent_file_path",
        lambda *_: "agent.md",
    )
    output = tmp_path / "checklist.md"

    assert compose_declared_checklist(
        skill_name="synthesis",
        contract=contract,
        agent_name="Ada",
        role="researcher",
        checklist_file_path=output,
        iteration=2,
        context={"evidence_file": "research.md"},
        artifacts={"research": "research.md"},
    )
    assert output.read_text(encoding="utf-8") == "[ ] Revise research.md\n"


def test_custom_contract_rejects_unresolved_optional_instruction_when_absent(
    tmp_path, monkeypatch
) -> None:
    """Optional inputs need an explicit context reference, never line deletion."""
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "synthesis"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: synthesis\ndescription: synthesis\n---\n", encoding="utf-8"
    )
    (skill_dir / "references" / "execution.md").write_text(
        "[ ] Read optional review {review_file}\n[ ] Write the result\n",
        encoding="utf-8",
    )
    contract = SkillWorkflowContract.model_validate(
        {
            "prompt_inputs": [
                {"artifacts": ["review"], "placeholder": "review_file", "required": False}
            ],
            "checklist": {
                "variants": [{"sections": [{"reference": "execution.md"}]}],
                "include_role_guidance": False,
            },
        }
    )
    loader = SkillLoader(project_root=tmp_path)
    loader.discover()
    monkeypatch.setattr(
        "cafe.skills.checklist_composer.AgentManager.get_agent_file_path", lambda *_: "agent.md"
    )
    output = tmp_path / "checklist.md"

    with pytest.raises(ValueError, match="review_file"):
        compose_declared_checklist(
            skill_name="synthesis",
            contract=contract,
            agent_name="Ada",
            role="researcher",
            checklist_file_path=output,
            iteration=1,
            context={},
            artifacts={},
        )


def test_context_reference_omits_only_its_dedicated_optional_instruction(
    tmp_path, monkeypatch
) -> None:
    """A declared context reference is safely empty when its optional input is absent."""
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".cafe" / "skills" / "synthesis"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: synthesis\ndescription: synthesis\n---\n", encoding="utf-8"
    )
    (skill_dir / "references" / "execution.md").write_text(
        "[ ] Prepare result\n{optional_review_instruction}\n[ ] Write the result\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "optional_review.md").write_text(
        "[ ] Read optional review {review_file}\n", encoding="utf-8"
    )
    contract = SkillWorkflowContract.model_validate(
        {
            "prompt_inputs": [
                {"artifacts": ["review"], "placeholder": "review_file", "required": False}
            ],
            "checklist": {
                "context_references": {"optional_review_instruction": "optional_review.md"},
                "variants": [{"sections": [{"reference": "execution.md"}]}],
                "include_role_guidance": False,
            },
        }
    )
    loader = SkillLoader(project_root=tmp_path)
    loader.discover()
    monkeypatch.setattr(
        "cafe.skills.checklist_composer.AgentManager.get_agent_file_path", lambda *_: "agent.md"
    )

    without_review = tmp_path / "without-review.md"
    assert compose_declared_checklist(
        skill_name="synthesis",
        contract=contract,
        agent_name="Ada",
        role="researcher",
        checklist_file_path=without_review,
        iteration=1,
        context={},
        artifacts={},
    )
    assert "Read optional review" not in without_review.read_text(encoding="utf-8")
    assert "Write the result" in without_review.read_text(encoding="utf-8")

    with_review = tmp_path / "with-review.md"
    assert compose_declared_checklist(
        skill_name="synthesis",
        contract=contract,
        agent_name="Ada",
        role="researcher",
        checklist_file_path=with_review,
        iteration=1,
        context={"review_file": "review.md"},
        artifacts={"review": "review.md"},
    )
    assert "[ ] Read optional review review.md" in with_review.read_text(encoding="utf-8")
