---
name: cafe-incident_postmortem
description: Produce an incident postmortem and prevention actions
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: standard
    risk_domains: [root-cause, prevention]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue the postmortem.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [incident_learning, causal_todo]
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

# Incident Postmortem

## Role
Read your agent file: {agent_file}

## Instructions
Document root cause, timeline, lessons, and prevention actions. If the incident is still evolving, return to triage or detection with updated state. When returning to triage, emit canonical correction Todo items.

## Output
Write postmortem to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
