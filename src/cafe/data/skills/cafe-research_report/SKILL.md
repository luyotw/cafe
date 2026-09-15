---
name: cafe-research_report
description: Produce a sourced research report
version: 1.1.0
workflow:
  execution_profile:
    workload: content
    reasoning: standard
    risk_domains: [source-fidelity, limitations]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to complete the report.
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

# Research Report

## Role
Read your agent file: {agent_file}

## Instructions
Write for the reader with conclusions, evidence, limits, and next recommendations, primarily in Markdown. When returning to synthesize, emit canonical correction Todo items.

## Output
Write report to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
