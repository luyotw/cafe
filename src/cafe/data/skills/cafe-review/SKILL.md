---
name: cafe-review
description: "審查程式碼品質與風險"
version: 1.5.0
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
          - reference: execution_steps.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2}
        sections:
          - reference: correction_review_strategy.md
          - reference: execution_steps.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. Review every supplied requirement, plan, implementation artifact, and feedback item that applies to this run.

## Instructions
- 以缺陷與風險為主
- 先確認目前提供的需求、計畫與實作是否一致
- 優先指出行為回歸、缺少測試與高風險問題
- Repo 搜尋與輸出上限：請依 shared skill「cafe-workflow-common」的 **Bounded repository inspection**；本 skill 不重複敘述。
- 測試證據、repository hooks 與 CI 的分工：請依 shared skill「cafe-workflow-common」的 **Repository-owned quality gates**；本 skill 不重複敘述。
- 審查變更相關的 targeted test 選擇與品質；不得因缺少 CAFE verification receipt 打回 develop，也不在 review 重跑 repository-wide 驗證。
- 若需修改，把 next-step baton 寫成 `develop`
- 通過時，把 next-step baton 寫成下一個 workflow step（預設 playbook 為 `pr`）
- 與 developer 往返、仲裁、以及 blackboard/baton 更新：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Output
Write review result to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
