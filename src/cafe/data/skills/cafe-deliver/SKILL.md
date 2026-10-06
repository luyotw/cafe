---
name: cafe-deliver
description: Prepare delivery readiness for the exact confirmed compact endpoint.
version: 1.0.0
workflow:
  notification:
    step_label:
      message_key: notification.step_labels.pr
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_develop.clarification_feedback.prompt}
      input_schema: feedback
    - id: permission-answers
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_develop.permission_answers.prompt}
      input_schema: feedback
  checklist:
    variants:
      - when: {}
        sections:
          - reference: delivery_readiness.md
---

# Compact delivery readiness

Read the current request and resolved execution context. Prepare the confirmed
route's readiness evidence after current independent review and a fresh final
scope checkpoint. Retain exact remote and branches/effects; readiness is not
publication or push success. The Manager uses the existing capability or
confirmed closeout adapter to execute the endpoint after worker quiescence.

Use output_file for the delivery summary. Write delivery_readiness.json with
the confirmed authority digest, route, exact endpoint, current review reference
and final checkpoint receipt. A changed/missing target or effects, unavailable
review, or scope violation requires the existing focused user handoff. Do not
re-ask an unchanged already-granted decision. Mandatory host approvals remain
human-owned. Preserve normal Git hooks and unrelated existing changes.

On acceptance, use the active graph's declared terminal readiness outcome.
For streamlined, write the explicit terminal baton
`{"version":1,"to_owner":"done","to_step":"done","intent":"workflow_complete"}`. The Manager reports
verified PR URL/source/target or pushed SHA/branch separately after execution;
partial failure and unknown outcome never count as delivered. Merge, force
push, issue closure and cleanup are outside this contract.
