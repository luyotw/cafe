---
name: cafe-research_question
description: Define a research question and its assumption boundaries
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: standard
    risk_domains: [scope, assumptions]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to refine the research question.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [research_notes, causal_todo]
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

# Research Question

## Role
Read your agent file: {agent_file}

## Instructions
Narrow the topic into a testable research question with scope, success criteria, known limits, and unresolved assumptions. When a correction source is supplied, consume every canonical Todo item and preserve its ID.

## Output
Write research question to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
