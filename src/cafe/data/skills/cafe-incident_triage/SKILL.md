---
name: cafe-incident_triage
description: Classify incidents and choose response actions
version: 1.2.0
workflow:
  notification:
    task_labels:
      clarification-feedback:
        message_key: notification.action_labels.clarification_feedback
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, prioritization]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_incident_triage.clarification_feedback.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_incident_triage.clarification_feedback.prompt}
      input_schema: feedback
  prompt_inputs:
    - artifacts: [incident_recovery, incident_learning, causal_todo]
      placeholder: correction_source
      required: false
  checklist:
    variants:
      - when: {feedback: true}
        sections:
          - todo_projection: {artifact: causal_todo, causal: true}
      - when: {}
        sections:
          - reference: correction_contract.md
---

# Incident Triage

## Role
Read your agent file: {agent_file}

## Instructions
Set priority, ownership, and mitigation strategy, returning to detection when information is incomplete. When a correction source is supplied, consume every canonical Todo item and preserve its ID.

## Output
Write triage report to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
