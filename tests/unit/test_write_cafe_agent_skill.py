"""Contract tests for bundled write-cafe-agent guidance."""

from pathlib import Path

from cafe.skills.loader import SkillLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "write-cafe-agent"


def test_write_cafe_agent_skill_is_discoverable(tmp_path: Path) -> None:
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=PROJECT_ROOT / "src" / "cafe" / "data",
    )

    items = loader.discover()

    assert any(item.name == "write-cafe-agent" and item.source == "builtin" for item in items)


def test_write_cafe_agent_defines_checklist_coupling_and_boundaries() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL_ROOT / "references" / "agent-spec.md").read_text(encoding="utf-8")
    normalized = " ".join(f"{skill}\n{reference}".split())

    assert "## Checklist Coupling" in skill
    assert "## Agent Guidelines Checklist" in normalized
    assert "trimmed form starts with `- `" in normalized
    assert "Indented bullets are also extracted" in reference
    assert "Do not rely on paragraphs alone" in skill
    assert "write-cafe-phase" in normalized
    assert "write-cafe-playbook" in normalized
    assert ".cafe/agents/<role>/<name>.md" in skill
    assert "src/cafe/data/agents/<role>/<name>.md" in skill


def test_write_cafe_agent_uses_canonical_role_examples() -> None:
    reference = (SKILL_ROOT / "references" / "agent-spec.md").read_text(encoding="utf-8")

    assert "agents/pm/Roger.md" in reference
    assert "agents/developer/David.md" in reference
    assert "agents/developer/Nick.md" in reference
    assert "agents/reviewer/Richard.md" in reference
    assert "母語為繁體中文。" in reference
