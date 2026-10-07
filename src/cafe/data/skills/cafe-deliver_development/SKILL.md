---
name: cafe-deliver_development
description: Present exact development integration and selected issue receipts, recover
  incomplete actions and request outcome acceptance.
version: 1.0.0
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains:
    - external-side-effects
    fallback_strength: equivalent
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  human_tasks:
  - id: delivery-outcome
    pattern: confirm_output
    prompt:
      message_key: human_task.delivery.delivery_outcome
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_outcome
    input_schema: decision
    decisions:
    - id: confirm
      label: Confirm the displayed completed results
      label_locales:
        zh-TW: 確認顯示的已完成結果
    - id: revise
      requires_feedback: true
      correction: true
      label: Recover or revise delivery
      label_locales:
        zh-TW: 恢復或修訂交付
  - id: delivery-recovery
    pattern: revision_feedback
    prompt:
      message_key: human_task.delivery.delivery_recovery
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_recovery
    input_schema: feedback
  - id: delivery-permission
    pattern: revision_feedback
    prompt:
      message_key: human_task.delivery.delivery_permission
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_permission
    input_schema: feedback
  checklist:
    variants:
    - when: {}
      sections:
      - reference: delivery.md
    include_role_guidance: true
---

# Development delivery

Read your agent file: {agent_file}

The declared host hook owns operations. Read the action and receipt paths supplied in the current delivery evidence continuation; these receipts are the evidence authority. Perform no direct Git/GitHub mutation. Render actual integration commit, selected proposal IDs and issue URLs, remaining actions and unknown/pending outcomes in {output_file}.

Only when `delivery_complete` is true may you request the declared user `confirm_output` handoff. Host approval is distinct from action selection and outcome acceptance. For incomplete effects use the declared recovery/permission task. For conflicts or changed implementation, normalize the correction into the existing Todo List contract and select the injected discretionary implementation route; do not resolve conflicts or redesign here. Revised actions need fresh PR action review, never edits to the approved snapshot. Cleanup/archive/branch deletion/issue closure/release/deployment remain separately authorized.

Write the outcome baton to {next_step_path}; runtime owns blackboard updates.
