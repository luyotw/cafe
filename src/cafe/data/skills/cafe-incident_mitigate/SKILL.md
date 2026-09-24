---
name: cafe-incident_mitigate
description: Mitigate and recover from an operational incident
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, state-change, rollback]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue mitigation.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [incident_recovery, causal_todo]
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

# Incident Mitigate

## Role
Read your agent file: {agent_file}

## Instructions
Execute mitigation, verify service recovery, and record changes and rollback points. If conditions change, return to triage or detection. When returning to triage, emit canonical correction Todo items.

## Output
Write mitigation log to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
