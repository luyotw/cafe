"""Contract tests for bundled write-cafe-phase guidance."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "write-cafe-phase"


def test_write_cafe_phase_repairs_only_its_declarative_layer() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_spec = " ".join(spec.split())

    assert "## Declarative Repair Boundary" in skill
    assert ".cafe/skills/<skill-name>/" in skill
    assert "src/cafe/data/skills/<skill-name>/" in skill
    assert "return the classification to the driver for `write-cafe-playbook`" in normalized_skill
    assert "return a CAFE core-defect diagnosis" in normalized_skill
    assert "Do not edit driver/meta skills such as `use-cafe-workflow`" in normalized_skill
    assert "This skill is not a general CAFE self-modifier" in normalized_skill
    assert "Driver diagnosis 與 declarative repair 邊界" in spec
    assert "不要建立隱含的 `write-cafe-driver` fallback" in normalized_spec
    assert "沒有 user 明確授權不得 自動 create、comment 或 close issue" in normalized_spec
    assert "playbook `skills.workflow`" in spec
    assert "playbook `skills.chat`" in spec
    assert "workflow.execution_profile" in skill
    assert "provider-neutral execution-requirement metadata" in normalized_spec
    assert "Do not name a CLI provider, model, pricing tier" in normalized_spec
    assert "conservatively aggregate every declared variant" in normalized_spec
