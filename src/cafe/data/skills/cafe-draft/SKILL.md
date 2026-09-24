---
name: cafe-draft
description: Draft an article from an approved editorial brief
version: 1.1.0
workflow:
  execution_profile:
    workload: content
    reasoning: standard
    risk_domains: [source-fidelity]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue drafting.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [review_feedback, causal_todo]
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

# Draft Article

## Role
Read your agent file: {agent_file}

## Instructions
Write the draft from the brief or complete editorial correction source with a clear structure, concrete reasoning, and audience-appropriate detail. Preserve every incoming Todo item ID.

## Output
Write draft to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
