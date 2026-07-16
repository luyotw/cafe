"""Tests for bundled use-cafe-workflow skill guidance."""

from pathlib import Path

from cafe.core.playbook import confirmation_gate_steps
from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_use_cafe_workflow_skill_guides_alignment_checkpoint_delegation() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Alignment Checkpoints" in skill
    assert "resolve the checkpoint on behalf of the user" in skill
    assert "explicit JSON decision payload" in skill
    assert "Plain text must not be used for alignment approval" in skill
    assert "Do not write `strategic_documents_updated`" in skill
    assert "stop and ask the user" in skill


def test_use_cafe_workflow_skill_requires_playbook_derived_kickoff_stop_contract() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Kickoff Stop Contract (first blocking gate)" in skill
    assert "Before `cafe prepare`, any repository mutation, or the first `cafe make`" in skill
    assert "cafe playbook confirmation-gates <playbook-id>" in skill
    assert '`steps.<step>."on".confirm_output`' in skill
    assert "Do not reuse a repo default or another issue's contract silently" in normalized
    assert "their union must equal the derived" in normalized
    assert "driver_confirmable" in skill
    assert (
        "`need_clarification`, `need_permission`, and `alignment_checkpoint` are reactive"
        in normalized
    )
    assert ".cafe/issues/<issue-name>/issue.yaml" in skill
    assert "is not parsed or auto-approved by CAFE" in normalized


def test_builtin_confirmation_gate_candidates_come_from_playbook_declarations() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    actual = {
        playbook_id: confirmation_gate_steps(loader.load_model(playbook_id).model)
        for playbook_id in (
            "default",
            "simple",
            "tdd",
            "editorial",
            "hotfix",
            "incident",
            "research",
        )
    }

    assert actual == {
        "default": ("spec", "plan"),
        "simple": ("spec",),
        "tdd": ("spec", "plan"),
        "editorial": ("brief",),
        "hotfix": (),
        "incident": (),
        "research": (),
    }


def test_use_cafe_workflow_skill_protects_issue_overrides() -> None:
    skill = (
        PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "### Issue overrides are opt-in only" in skill
    assert "The `issues:` section is protected" in skill
    assert "do not write to this section" in skill
    assert "Do not create `issues.<issue-name>` just because" in skill
    assert "Do not store workflow progress, baton state, phase outputs" in skill
    assert "leave `issues:` untouched unless the user explicitly requested" in skill
