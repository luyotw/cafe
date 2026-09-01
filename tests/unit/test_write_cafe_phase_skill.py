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


def test_write_cafe_phase_requires_interrupt_safe_batch_progress() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_spec = " ".join(spec.split())

    assert "version: 2.9.4" in skill
    assert "## Interruptible and Batch Phases" in skill
    assert "it is not a per-target resume ledger" in normalized_skill
    assert (
        "output template, downstream consumers, finalizers, and publish hooks"
        in normalized_skill
    )
    assert "per-target/stage dependency fingerprints" in normalized_skill
    assert "including relevant dirty or mutable content" in normalized_skill
    assert "loses at most the unit currently running" in normalized_skill
    assert "when impact is ambiguous" in normalized_skill
    assert "a separate ledger hashes the complete final-artifact bytes" in normalized_skill
    assert (
        "canonical domain-payload projection that excludes ledger/finalization metadata"
        in normalized_skill
    )
    assert "Record the algorithm and scope/projection version" in normalized_skill
    assert "post-success runtime/host hook or later retention policy" in normalized_skill
    assert "migrate only deterministic local evidence" in normalized_skill
    assert (
        "Phase preparation refreshes an existing iteration's derived `checklist.md`"
        in normalized_skill
    )
    assert "new or changed gates reopen" in normalized_skill
    assert "do not add an internal infinite retry loop" in normalized_skill

    assert "## 17. 可中斷與大量工作 phase 的 checkpoint/resume contract" in spec
    assert "簡單、單一、可快速原子完成的 phase 不必硬加 ledger" in normalized_spec
    assert "大量工作預設最多損失 1 個 target" in normalized_spec
    assert "`{output_file}` 不是無條件預設" in normalized_spec
    assert "output 是 exact-shape" in normalized_spec
    assert "單一 Git HEAD 不足以代表 dirty 或 target-specific inputs" in normalized_spec
    assert "只有 stage dependency fingerprint 相同" in normalized_spec
    assert "impact 無法證明" in normalized_spec
    assert "`finalized` digest receipt" in normalized_spec
    assert "Separate ledger 對完整 final artifact bytes 計算 digest" in normalized_spec
    assert "embedded ledger 對排除 ledger 與 finalization metadata" in normalized_spec
    assert "algorithm、scope 與 scope/projection schema version" in normalized_spec
    assert "不得對含 digest 欄位的整個檔案做自我參照 hash" in normalized_spec
    assert "依 receipt 記錄的 algorithm 與 scope/projection version 重算" in normalized_spec
    assert "phase agent 不得因已產生 final artifact 就移除或覆寫唯一 ledger" in normalized_spec
    assert "post-success runtime/host hook 或 retention policy" in normalized_spec
    assert "外層 repair agent 不手改 generated issue artifacts" in normalized_spec
    assert "沒有明確 receipt 的 consumer review" in normalized_spec
    assert "新增或變更項目會以未完成 gate 出現" in normalized_spec
    assert "完整內容完全相同的 completed item" in normalized_spec
    assert "runtime 會重新安裝 resolved skill" in normalized_spec
    assert "active `SKILL.md`" in normalized_spec
