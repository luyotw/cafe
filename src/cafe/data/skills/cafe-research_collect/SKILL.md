---
name: cafe-research_collect
description: Collect, organize, and record research sources
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: standard
    risk_domains: [source-quality, traceability]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue evidence collection.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [research_synthesis, causal_todo]
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

# Research Collect

## Role
Read your agent file: {agent_file}

## Instructions
Collect and organize sources into traceable notes and citations, marking credibility and gaps. When a correction source is supplied, consume every canonical Todo item and preserve its ID.

## Output
Write collected sources to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
