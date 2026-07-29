---
name: cafe-review
description: "審查程式碼品質與風險"
version: 1.0.0
workflow:
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
    - artifacts: [code]
      placeholder: develop_file
      required: false
    - artifacts: [review_feedback, pr_result]
      placeholder: feedback_file
      required: false
  checklist:
    variants:
      - when: {}
        sections:
          - reference: execution_steps.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Instructions
- 以缺陷與風險為主
- 先確認需求、計畫與實作是否一致
- 優先指出行為回歸、缺少測試與高風險問題
- 若需修改，把 next-step baton 寫成 `develop`
- 通過時，把 next-step baton 寫成下一個 workflow step（預設 playbook 為 `pr`）
- 與 developer 往返、仲裁、以及 blackboard/baton 更新：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Output
Write review result to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
