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
    assert "planned kickoff confirmation gate" in normalized_reference
    assert "confirmation-gates" in skill
    assert "no unreachable steps" in reference
    assert "write-cafe-phase" in skill
