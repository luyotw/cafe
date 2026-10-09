---
name: cafe-deliver_development
description: Present exact development integration and selected issue receipts, recover
  incomplete actions and request outcome acceptance.
version: 1.0.3
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
    - id: confirm_cleanup
      label: {message_key: human_task.delivery.confirm_cleanup}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm_cleanup}
    - id: confirm_archive
      label: {message_key: human_task.delivery.confirm_archive}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm_archive}
    - id: confirm
      label: {message_key: human_task.delivery.confirm}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm}
    - id: revise
      requires_feedback: true
      correction: true
      label: {message_key: human_task.delivery.revise}
      label_locales:
        zh-TW: {message_key: human_task.delivery.revise}
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

## Role

Read your agent file: {agent_file}

## Instructions

The declared host hook owns operations. Read the action and receipt paths supplied in the current delivery evidence continuation; these receipts are the evidence authority. Perform no direct Git/GitHub mutation. Render actual integration commit, selected proposal IDs and issue URLs, remaining actions and the host post-integration verification state in {output_file}. Include each required workflow run URL and attempt; identify what its required jobs and steps prove about the agreed CI, deployment and public checks. Report missing, pending, failed and unknown checks accurately.

The host waits up to 15 minutes for the approved target-branch push workflows on the actual integration commit. It reads existing Actions results and never replays integration or starts deployment. A pending check is waiting, not acceptance; after the bounded wait expires use the declared recovery route with the remaining checks. For a failed required check, normalize necessary implementation corrections into the existing Todo List and use the injected correction route. An API or permission failure is unknown, never success. Legacy snapshots lacking a verification plan require fresh PR action review; preserve successful effects and never edit the old approval.

Only when `delivery_complete` is true, including every required verification or the explicitly approved no-verification reason, may you request the declared user `confirm_output` handoff. The host preserves distinct action and capability authority from the one displayed PR decision. Include the host-displayed terminal plan with the results: the same user reply accepts the outcome and chooses the exact cleanup plan, archive-only, or leaving external state unchanged. Manager executes that choice only after workflow completion; this phase performs no cleanup. For incomplete effects use the declared recovery/permission task. For conflicts or changed implementation, normalize the correction into the existing Todo List contract and select the injected discretionary implementation route; do not resolve conflicts or redesign here. Revised actions need fresh PR action review, never edits to the approved snapshot. Other branch deletion/release/deployment needs separate explicit authority.

## Output

Write the delivery summary to: {output_file}

## Handoff

Write next-step baton for this result; the runtime updates the blackboard.
