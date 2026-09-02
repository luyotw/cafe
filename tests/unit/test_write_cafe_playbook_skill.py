"""Contract tests for bundled write-cafe-playbook guidance."""

from pathlib import Path

from cafe.skills.loader import SkillLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "write-cafe-playbook"


def test_write_cafe_playbook_skill_is_discoverable(tmp_path: Path) -> None:
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=PROJECT_ROOT / "src" / "cafe" / "data",
    )

    items = loader.discover()

    assert any(item.name == "write-cafe-playbook" and item.source == "builtin" for item in items)


def test_write_cafe_playbook_skill_preserves_core_contracts() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL_ROOT / "references" / "playbook-spec.md").read_text(
        encoding="utf-8"
    )
    normalized_reference = " ".join(reference.split())

    assert "cafe playbook validate <id> --strict" in skill
    assert "cafe playbook simulate <id> --dot" in skill
    assert "input_artifacts: [plan]" in reference
    assert "output_artifact: plan" in reference
    assert "Serial Plan Bridge" in reference
    assert "not_required" in reference
    assert "UserInputCollector" in reference
    assert "planned output confirmation gate" in normalized_reference
    assert "confirmation-gates" in skill
    assert "mandatory HumanTask gates" in skill
    assert "not assignable to the driver" in reference
    assert "no unreachable steps" in reference
    assert "write-cafe-phase" in skill
    assert "skills.workflow" in reference
    assert "skills.chat" in reference
    assert "extend" in reference
    assert "replace" in reference
    assert "explicit empty" in normalized_reference
    assert "missing channel" in normalized_reference


def test_write_cafe_playbook_repairs_only_its_declarative_layer() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "## Declarative Repair Boundary" in skill
    assert ".cafe/playbooks/<id>.yaml" in skill
    assert "src/cafe/data/playbooks/<id>.yaml" in skill
    assert "return the classification to the driver for `write-cafe-phase`" in normalized
    assert "return a CAFE core-defect diagnosis" in normalized
    assert "Do not edit driver/meta skills such as `use-cafe-workflow`" in normalized
    assert "This skill is not a general CAFE self-modifier" in normalized


def test_write_cafe_playbook_defines_conversation_locale_contract() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL_ROOT / "references" / "playbook-spec.md").read_text(
        encoding="utf-8"
    )
    normalized_skill = " ".join(skill.split())
    normalized_reference = " ".join(reference.split())

    assert "playbook.conversation_locale" in skill
    assert "BCP 47 language tag" in normalized_skill
    assert "### Conversation locale" in reference
    assert "omitted conversation locale values resolve to `auto`" in normalized_reference
    assert "does not require a separate user confirmation" in normalized_reference
    assert "asking about the language choice is not an override" in normalized_reference
    assert "does not translate commands, paths, playbook or step names" in normalized_reference


def test_write_cafe_playbook_requires_bounded_applicability_and_migration() -> None:
    """U8 — authoring, migration, and final review share one applicability contract."""
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL_ROOT / "references" / "playbook-spec.md").read_text(
        encoding="utf-8"
    )
    normalized_skill = " ".join(skill.split())
    normalized_reference = " ".join(reference.split())

    assert "applicability:" in reference
    assert "summary:" in reference
    assert "use_when:" in reference
    assert "avoid_when:" in reference
    assert "160" in reference
    assert "200" in reference
    assert "1–6" in reference
    assert "selection intent" in normalized_reference
    assert "resolved graph remains authoritative" in normalized_reference
    assert "missing" in normalized_reference
    assert "ineligible for automatic recommendation" in normalized_reference
    assert "cafe playbook validate <id> --strict" in reference
    assert "applicability" in normalized_skill
    assert "positive and negative conditions" in normalized_skill
    assert "Final Review" in skill
    assert "graph facts" in normalized_skill
