---
name: cafe-research_synthesize
description: Synthesize findings and cross-check evidence
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: high
    risk_domains: [conflicting-evidence, inference]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue synthesis.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [research_report_doc, causal_todo]
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

# Research Synthesize

## Role
Read your agent file: {agent_file}

## Instructions
Integrate findings from multiple sources, identify consensus, disagreement, and open verification work, and form an argument structure for the report. When a correction source is supplied, consume every canonical Todo item and preserve its ID.

## Output
Write synthesis to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
