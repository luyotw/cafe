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


def test_write_cafe_phase_requires_confirmed_supporting_skill_selection() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())

    native = spec.index("**CLI-native Skill**")
    open_source = spec.index("**開源 Skill**")
    self_authored = spec.index("**自行撰寫**")

    assert native < open_source < self_authored
    assert "## Supporting Skill Selection" in skill
    assert "independently for every supported target CLI" in normalized_skill
    assert "Stop at the first suitable tier for that CLI" in normalized_skill
    assert "unresolved CLIs may continue to lower tiers" in normalized_skill
    assert "Build one proposed selection matrix covering every target CLI" in normalized_skill
    assert "advance only that CLI to its next tier" in normalized_skill
    assert "wait for explicit confirmation" in normalized_skill
    assert "authoring time" in normalized_skill
    assert "Do not make workflow execution search the network" in normalized_skill
    normalized_spec = " ".join(spec.split())
    assert "對每個預定支援的 agent CLI **分別、獨立**" in normalized_spec
    assert "沒有合適方案時，該 CLI 才進入開源層" in normalized_spec
    assert "沒有合適方案時，該 CLI 才進入自行撰寫層" in normalized_spec
    assert "只有該 CLI 的原生與開源兩層都已完成評估" in normalized_spec
    assert "某個 CLI 已找到原生候選，不會阻止其他尚未解決的 CLI" in normalized_spec
    assert "先完成覆蓋所有目標 CLI 的 proposed selection matrix" in normalized_spec
    assert "決定使用哪一個方案前" in normalized_spec
    assert (
        "等待 user 明確確認後，才可採用原生 Skill、安裝或 vendor 開源內容，或開始撰寫"
        in normalized_spec
    )
    assert "Runtime 只負責安裝、啟用與執行已固定的 Skill 組合" in normalized_spec
    assert "不搜尋網路、不下載 mutable latest、不猜測替代品" in normalized_spec
    assert "不得在 phase runtime 自動拉取" in normalized_spec
    assert "只將該 CLI 進入下一層，更新整份 matrix 並重新取得確認" in normalized_spec
