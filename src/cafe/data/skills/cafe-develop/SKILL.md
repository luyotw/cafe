---
name: cafe-develop
description: "依計畫進行程式開發與測試"
version: 1.4.0
workflow:
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
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification or implementation feedback needed to continue.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
    - artifacts: [plan]
      placeholder: plan_file
      required: false
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
    - artifacts: [review_feedback, pr_result]
      placeholder: feedback_file_path
      required: false
    - artifacts: [review_feedback, pr_result]
      placeholder: feedback_file
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
- 在第一次實質修改前，以及任兩次實質修改之間，唯讀工具呼叫（read/search/list/status/diff 等）上限皆為 **20 次**；每次實質修改會重設計數，測試執行不會重設此上限
- runtime 會硬性中止超過唯讀上限的 attempt，並只允許在同一 session 續跑一次；續跑時必須在 3 次唯讀呼叫內完成下一個相關檔案修改，不得重新開始探索
- 到達上限前必須寫入第一個相關的 failing test；若依計畫不需新增測試，則寫入第一個實作修改。若仍無法安全修改，立即提出具體 clarification 並交棒給 user，不得繼續探索
- 若 workflow 提供 plan，新增或修改的測試必須對應其 **Test List** 項目（範圍變更時先更新計畫）
- 斷言以 invariant 為主：避免綁定 UI copy、CSS class、DOM 結構、內部 state shape；允許 a11y role/label、`data-testid`、以及規格明訂的文案（見 `cafe-plan/references/test_invariants_policy.md`）
- 每輪完成後更新 checklist
- 維持既有 commit 風格與程式碼註解語言
- 優先重用現有模式與工具
- 與 reviewer 往返、blackboard/baton 更新、以及 user 仲裁等跨 phase 規則：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Output
Write development summary to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
