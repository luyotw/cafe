---
name: cafe-develop_agent_review
description: "依計畫完成程式開發與測試，並以 detail、scope 兩個原生 subagent 完成 PR 前審查"
version: 1.0.0
workflow:
  execution_profile:
    workload: implementation
    reasoning: standard
    risk_domains: [integration, state-change]
    fallback_strength: equivalent_or_stronger
  required_tools:
    - Agent
  human_tasks:
    - id: no-change-decision
      pattern: no_changes_needed
      prompt: Review the implementation reasoning and choose how to continue.
      input_schema: decision
      decisions:
        - id: agree
          label: Agree that no further changes are needed
        - id: disagree
          label: Request further changes
          requires_feedback: true
          correction: true
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification or implementation feedback needed to continue.
      input_schema: feedback
    - id: permission-answers
      pattern: revision_feedback
      prompt: Provide the permission decision or access details needed to continue development.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: spec
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: spec
    - artifacts: [plan]
      placeholder: plan_file
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: plan
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: plan
    - artifacts: [qa_feedback, review_feedback, pr_result]
      placeholder: feedback_file_path
      required: false
    - artifacts: [qa_feedback, review_feedback, pr_result]
      placeholder: feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  checklist:
    context_references:
      normal_plan_context: normal_plan_context.md
      normal_plan_verification: normal_plan_verification.md
      correction_plan_context: correction_plan_context.md
      correction_plan_test_list: correction_plan_test_list.md
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {feedback: true}
        sections:
          - reference: execution_steps_correction.md
          - todo_projection: {artifact: causal_todo, causal: true}
          - optional_checklist: basic_principles.md
      - when: {}
        sections:
          - reference: execution_steps_normal.md
          - todo_projection: {artifact: plan, source: plan}
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Develop

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. When a specification or plan is supplied, treat it as authoritative for this run.

## Instructions
- `## Todo Progress` 的 completed item 必須使用可驗證 evidence contract：先以 `cafe verification run --output-file {output_file} --scope targeted -- <test command>` 執行框架不限的 targeted command 並產生本 iteration receipt；每項最多 32 個 `Files` 與 8 個 `Commit`；`Files` 列出 backtick 包住的 repo-relative tracked paths，且至少一個 test path 必須由同一項的 targeted command 執行；`Commit` 列出涵蓋這些檔案且可解析的完整 backtick SHA；`Targeted evidence` 使用 ``command=`...`; exit=0; head=`<完整 HEAD SHA>` ``，並須完全匹配該 receipt，不得自行宣稱執行結果。確實沒有 repository change 時，`Files` 寫 `N/A (no repository changes)`、`Commit` 寫 `N/A (no repository changes): <reason>`，且只在 tracked worktree clean 時有效。不得使用自由文字或杜撰的檔案、commit、測試結果。
- 依目前 workflow 已提供的需求與計畫逐項完成；若此 workflow 未提供 spec 或 plan，依使用者輸入與 review feedback 完成範圍內修正
- 先補測試再改程式
- 第一次探索只做一輪：讀一次已提供的 spec、plan 與 feedback，再針對可用 Test List 與預計修改點搜尋程式碼；未出現新證據時不得重讀同一檔案或重跑相同的搜尋、`git status`、`git diff`
- 實作中只執行與變更直接相關的 targeted checks，並保持輸出有界；若 workflow 提供 plan，將 checks 對應其 Test List；不要在本 phase 重複 repository 的 full-suite、coverage、release 或 pre-push gate
- 若 workflow 提供 plan，新增或修改的測試必須對應其 **Test List** 項目（範圍變更時先更新計畫）
- 斷言以 invariant 為主：避免綁定 UI copy、CSS class、DOM 結構、內部 state shape；允許 a11y role/label、`data-testid`、以及規格明訂的文案（見 `cafe-plan/references/test_invariants_policy.md`）
- 將一次 Develop CLI invocation 視為同一個持續執行單位：只要仍有已授權且可執行的未完成工作，就繼續處理；完成一個 bounded unit、commit 或 targeted check 都只是進度，不是 iteration 邊界、checkpoint 終點或 handoff 理由
- 每完成一個 bounded unit，先驗證 evidence，再立即更新 `{output_file}` 的 `## Todo Progress` ledger；accepted plan 與 feedback 是不可變輸入，不得修改其 checkbox 或 Task Status。每筆 ledger 記錄 item ID、status、source fingerprint、files、commit（無 commit 時明列理由）、targeted evidence、remaining work 與 next action，然後直接繼續下一個未完成項目
- retry 或重新進入 phase 時，先從 plan、review feedback 與 `{output_file}` 重建未完成工作，並對照目前 worktree、dependency 與 evidence 驗證既有進度；不得只因 checkbox 已勾選或 commit 存在就假定工作完成，只跳過證據仍有效的項目
- 不得以純進度說明、要求外部再說 `continue`／`resume`、或等待下一次呼叫作為結束本次 invocation 的方式
- 只有三種情況可以結束本次 invocation：所有適用 checklist gate 與工作均已完成並寫出合法 handoff；確實需要 clarification、permission 或 user arbitration 並寫出合法 handoff；或 provider/tool 無法繼續。最後一種情況只保留真實 checkpoint，不得偽造已完成 checkbox、問題或 baton
- checklist 的 `[x]` 只有在本輪 Todo item 的 source fingerprint 與 ledger evidence 仍匹配時才可保留；不得以相似 wording、既有 commit 或上游 artifact checkbox 取代 evidence
- 在 handoff 前寫入非空的 development summary 到 `{output_file}`
- 維持既有 commit 風格與程式碼註解語言
- 優先重用現有模式與工具
- Repo 搜尋與輸出上限：請依 shared skill「cafe-workflow-common」的 **Bounded repository inspection**；本 skill 不重複敘述。
- Repository hooks、CI 與 phase-local targeted checks 的分工：請依 shared skill「cafe-workflow-common」的 **Repository-owned quality gates**；本 skill 不重複敘述。
- 與 reviewer 往返、blackboard/baton 更新、以及 user 仲裁等跨 phase 規則：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

### Dual-subagent review gate
- 完成實作與 targeted checks 後，parent agent 必須啟動剛好兩個原生 subagent：`detail` 與 `scope`；兩者都啟動後才能等待任一結果，使審查並行。沒有 repository change 時也必須讓兩者審查 no-change reasoning；若 user 已接受 `no_changes_needed` 並回到本 step，取得雙方 approval 後以正常 `await_agent` handoff，不得再次用 `no_changes_needed` 繞過 gate。
- 兩位 reviewer 都取得同一份權威需求、目前完整 diff／HEAD 與測試 evidence。`detail` 檢查 correctness、edge cases、regression risk 與 tests；`scope` 檢查 issue acceptance、omissions、overreach 與 architectural placement。
- Reviewer 僅能審查，不得編輯檔案、commit、改變 workflow state 或執行外部 mutation；所有修正由 parent agent 負責。
- 任一 reviewer 回報 blocking issue 時，不得 handoff。Parent agent 修正成立的 blocker、重跑相關 targeted checks，然後重新並行啟動 `detail` 與 `scope`；任何修正都使先前兩份 approval 失效。
- 若 parent agent 判定 finding 不成立，必須提供具體 evidence 並取得該 reviewer 新一輪明確的 no-blocking 結論，不得自行降級或忽略 blocker。
- 只有同一輪的 `detail` 與 `scope` 都明確回報 no blocking issues，才能把 baton 交給 playbook 的下一個 step。
- 若原生 subagent 能力不可用，或技術限制使 review 無法完成，寫出真實狀態並使用 `manual_handoff`；不得偽造 reviewer 結論。

## Output
Write development summary to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
