---
name: cafe-incident_detect
description: Detect and report incident signals for operational response
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, incomplete-evidence]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue detection.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [incident_plan, incident_learning, causal_todo]
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

# Incident Detect

## Role
Read your agent file: {agent_file}

## Instructions
Record the symptoms, impact, timeline, and initial severity for triage. When a correction source is supplied, consume every canonical Todo item and preserve its ID.

## Output
Write incident report to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
