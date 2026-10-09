---
name: cafe-deliver
description: Prepare delivery readiness for the exact confirmed compact endpoint.
version: 1.0.1
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains: [external-side-effects]
    fallback_strength: equivalent
  notification:
    step_label:
      message_key: notification.cafe_deliver.step_label
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_deliver.clarification_feedback.prompt}
      input_schema: feedback
    - id: permission-answers
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_deliver.permission_answers.prompt}
      input_schema: feedback
  checklist:
    variants:
      - when: {}
        sections:
          - reference: delivery_readiness.md
---

# Compact delivery readiness

## Role
Read your agent file: {agent_file}

## Context
Read the current request and resolved execution context, including the confirmed
Delivery Contract, current independent review and final scope checkpoint.

## Instructions
Prepare the confirmed route's readiness evidence after current independent
review and a fresh final scope checkpoint. Retain exact remote and branches/effects;
readiness is not publication or push success. The Manager uses the existing
capability or confirmed closeout adapter to execute the endpoint after worker
quiescence.

Write delivery_readiness.json with the confirmed authority digest, route, exact
endpoint, current review reference and final checkpoint receipt. A changed or
missing target or effects, unavailable review, or scope violation requires the
existing focused user handoff. Do not re-ask an unchanged already-granted decision.
Mandatory host approvals remain human-owned. Preserve normal Git hooks and
unrelated existing changes.

On acceptance, select the active graph's declared terminal readiness outcome.
The Manager reports verified PR URL/source/target or pushed SHA/branch separately
after execution; partial failure and unknown outcome never count as delivered.
Merge, force push, issue closure and cleanup are outside this contract.

## Output
Write the delivery readiness summary to: {output_file}

## Handoff
Write next-step baton for this result; the runtime updates the blackboard.
