---
name: cafe-review
description: "審查程式碼品質與風險"
version: 1.13.0
workflow:
  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [correctness, security]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue the review.
      input_schema: feedback
    - id: iteration-limit
      pattern: confirm_output
      prompt: The workflow reached its configured iteration limit. Increase the issue's limit if another review is authorized, then resume this phase.
      input_schema: decision
      decisions:
        - id: resume
          label: Resume after increasing the iteration limit
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [plan]
      placeholder: plan_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [code]
      placeholder: develop_file
      required: false
    - artifacts: [qa_feedback, review_feedback, pr_result]
      placeholder: feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  checklist:
    context_references:
      spec_read_instruction: spec_read_instruction.md
      plan_read_instruction: plan_read_instruction.md
      feedback_instruction: feedback_instruction.md
      spec_comparison_instruction: spec_comparison_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_preflight.md
          - reference: execution_risk_assessment.md
          - reference: execution_first_pass.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2, max_iteration: 3}
        sections:
          - reference: execution_preflight.md
          - reference: execution_correction.md
          - reference: execution_risk_assessment.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 4}
        sections:
          - reference: execution_preflight.md
          - reference: execution_convergence.md
          - reference: execution_risk_assessment.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. Review every supplied requirement, plan, implementation artifact, and feedback item that applies to this run.

## Available scripts
- `scripts/update_review_fallback.py` — maintainer-only updater for the pinned open-source review procedure; never run it during workflow execution.

    python scripts/update_review_fallback.py --help

## Instructions
- 以缺陷與風險為主
- 先確認目前提供的需求、計畫與實作是否一致
- 使用本版經 authoring-time 確認的 review discovery matrix：Codex 與 Claude 的既有 reviewer 是 host-side CLI command，不是 phase 內可直接組合的原生 Skill；Gemini、Cursor 與 Copilot 也沒有已確認的等價原生 Skill，因此五個 CLI 都使用 `references/review_procedure.md` 的 pinned 開源 procedure。不得在 runtime 自行搜尋、下載或替換 reviewer
- 讀取 `references/review_procedure.md`，每輪執行恰好一次候選缺陷掃描；首輪使用累積 change scope，correction 輪只使用本輪 `Correction Impact Set`，其輸出只能作為 candidate findings，不能取代本 phase 的 acceptance、risk、ledger 與 handoff 判定
- 把 pinned procedure 產生的 `Critical` / `Important` 標籤與數字當作「confidence bucket」，不當作 impact severity；在 CAFE finding 中分開記錄 `Impact: Critical | Important | Minor` 與 `Confidence: 0-100`，不用 `P1` 表示 finding severity 或 reviewer-assigned priority，避免與 Triggered Risk Coverage 的 `P1` obligation 衝突
- 只有同時具備 reachable production path、causal 或 reproducible evidence，且影響為 privilege escalation、secret exposure、irreversible data loss、destructive external mutation，或沒有 safe recovery 的 core workflow failure，才可判定 `Impact: Critical`；高 confidence、理論上無上限的輸入，或未證明的風險不足以成為 Critical
- 第 1–3 輪是 discovery mode：可將新的、有證據的 in-scope Critical 或 Important finding 列為 blocker，但當輪必須完成全部相關 scope 才交棒，不得找到第一項就提前結束
- 第 4 輪起是 convergence mode：原 blocker lineage 未解決、本輪修正直接造成的 regression，以及符合上述嚴格條件的新 Critical 仍然 blocking；其他新 Important / Minor finding 只能成為 follow-up proposal，不得重開 correction loop
- 每個 blocker 使用首次出現時分配的穩定 `BLK-NNN` ID，每個 follow-up 使用穩定 `FUP-NNN` ID；以 root cause identity 與 production path 判定同一 lineage，correction 輪必須沿用既有 ID，不得因描述改寫而重新編號或建立重複項目
- 每輪都要從上一輪帶入完整 `Finding Registry`，包含已關閉與已處置的 BLK/FUP lineage；只能依當前證據更新 status 與 evidence HEAD，不得刪除、改號或改寫 root-cause identity
- 本 phase 只提出 follow-up，不得開立、留言或關閉 GitHub issue；設為 follow-up 也不能讓 Critical 放行
- 若未來某個 CLI 提供可在 phase 內直接組合的原生 review Skill，必須先依 `write-cafe-phase` 的 selection matrix 流程取得 user 確認，再更新本 Skill；不得把 `codex review`、`claude ultrareview` 或其他巢狀 CLI subprocess 當成原生 Skill 偷跑
- 優先指出行為回歸、缺少測試與高風險問題
- Repo 搜尋與輸出上限：請依 shared skill「cafe-workflow-common」的 **Bounded repository inspection**；本 skill 不重複敘述。
- 測試證據、repository hooks 與 CI 的分工：請依 shared skill「cafe-workflow-common」的 **Repository-owned quality gates**；本 skill 不重複敘述。
- 審查變更相關的 targeted test 選擇與品質；不得因缺少 CAFE verification receipt 打回 develop，也不在 review 重跑 repository-wide 驗證。
- 首輪建立完整 issue acceptance closure 與 triggered risk coverage 基線；correction 輪只重開上輪 blocker、本輪修正影響的 row，以及依當前 discovery / convergence mode 分類後仍可 blocking 的新 finding，完整邊界經證明未變的 row 以 `closed_reused` 引用既有證據，不重跑 probe 或重寫內容；但 `closed_reused` 只省略重複證據，不得省略當輪必要的 cross-component seam coverage，已完整記錄且未受影響的 seam 可沿用。
- 若需修改，把 next-step baton 寫成 `develop`
- 通過時，把 next-step baton 寫成下一個 workflow step（預設 playbook 為 `pr`）；未解決的 non-Critical follow-up proposal 不是 blocker，但必須完整保留在當輪 review output 供 PR gate 處理
- 與 developer 往返、仲裁、以及 blackboard/baton 更新：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Output
Write review result to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
