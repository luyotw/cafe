---
name: cafe-brief_first
description: Create an initial editorial brief and drafting requirements
version: 1.1.0
workflow:
  execution_profile:
    workload: content
    reasoning: standard
    risk_domains: [audience-alignment]
    fallback_strength: equivalent
  human_tasks:
    - id: editorial-output-review
      pattern: confirm_output
      prompt: Approve the editorial brief or request a revision.
      input_schema: decision
      decisions:
        - id: approve
          label: Approve brief
        - id: revise
          label: Request brief revision
          requires_feedback: true
          correction: true
    - id: editorial-clarification
      pattern: answer_questions
      prompt: Answer the editorial clarification questions.
      input_schema: answers
      questions:
        - id: audience
          prompt: Who is the intended audience?
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

# Editorial Brief

## Role
Read your agent file: {agent_file}

## Instructions
Turn the request or complete editorial correction source into a clear brief covering audience, angle, missing information, and acceptance points. Preserve every incoming Todo item ID.

## Output
Write brief to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
