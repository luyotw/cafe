---
name: cafe-develop
description: "依計畫進行程式開發與測試"
version: 1.10.0
workflow:
  execution_profile:
    workload: implementation
    reasoning: standard
    risk_domains: [integration, state-change]
    fallback_strength: equivalent_or_stronger
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
          - optional_checklist: basic_principles.md
      - when: {}
        sections:
          - reference: execution_steps_normal.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Develop

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. When a specification or plan is supplied, treat it as authoritative for this run.

## Instructions
- 依目前 workflow 已提供的需求與計畫逐項完成；若此 workflow 未提供 spec 或 plan，依使用者輸入與 review feedback 完成範圍內修正
- 先補測試再改程式
- 第一次探索只做一輪：讀一次已提供的 spec、plan 與 feedback，再針對可用 Test List 與預計修改點搜尋程式碼；未出現新證據時不得重讀同一檔案或重跑相同的搜尋、`git status`、`git diff`
- 實作中只執行與變更直接相關的 targeted checks，並保持輸出有界；若 workflow 提供 plan，將 checks 對應其 Test List；不要在本 phase 重複 repository 的 full-suite、coverage、release 或 pre-push gate
- 若 workflow 提供 plan，新增或修改的測試必須對應其 **Test List** 項目（範圍變更時先更新計畫）
- 斷言以 invariant 為主：避免綁定 UI copy、CSS class、DOM 結構、內部 state shape；允許 a11y role/label、`data-testid`、以及規格明訂的文案（見 `cafe-plan/references/test_invariants_policy.md`）
- 將一次 Develop CLI invocation 視為同一個持續執行單位：只要仍有已授權且可執行的未完成工作，就繼續處理；完成一個 bounded unit、commit 或 targeted check 都只是進度，不是 iteration 邊界、checkpoint 終點或 handoff 理由
- 每完成一個 bounded unit，先驗證 evidence，再立即更新權威進度：有 plan 時更新對應 task checkbox 與 `Task Status`，有 review feedback 時只把已實際解決並驗證的 blocker 勾選；同時更新 `{output_file}` 的累積摘要，記錄完成的 item ID、變更、commit（若有）、test command/result/revision、剩餘工作與下一個動作，然後直接繼續下一個未完成項目
- retry 或重新進入 phase 時，先從 plan、review feedback 與 `{output_file}` 重建未完成工作，並對照目前 worktree、dependency 與 evidence 驗證既有進度；不得只因 checkbox 已勾選或 commit 存在就假定工作完成，只跳過證據仍有效的項目
- 不得以純進度說明、要求外部再說 `continue`／`resume`、或等待下一次呼叫作為結束本次 invocation 的方式
- 只有三種情況可以結束本次 invocation：所有適用 checklist gate 與工作均已完成並寫出合法 handoff；確實需要 clarification、permission 或 user arbitration 並寫出合法 handoff；或 provider/tool 無法繼續。最後一種情況只保留真實 checkpoint，不得偽造已完成 checkbox、問題或 baton
- 更新 plan 的完成狀態時，authoritative body checkbox 使用 `[x]`，`## Downstream Contract` 的 `Task Status` 僅使用 schema 允許的 `completed`；不得寫 `done`
- 在 handoff 前寫入非空的 development summary 到 `{output_file}`
- 維持既有 commit 風格與程式碼註解語言
- 優先重用現有模式與工具
- Repo 搜尋與輸出上限：請依 shared skill「cafe-workflow-common」的 **Bounded repository inspection**；本 skill 不重複敘述。
- Repository hooks、CI 與 phase-local targeted checks 的分工：請依 shared skill「cafe-workflow-common」的 **Repository-owned quality gates**；本 skill 不重複敘述。
- 與 reviewer 往返、blackboard/baton 更新、以及 user 仲裁等跨 phase 規則：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Output
Write development summary to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
