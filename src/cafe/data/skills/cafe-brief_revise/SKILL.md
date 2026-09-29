---
name: cafe-brief_revise
description: Revise an editorial brief from declared correction feedback
version: 1.2.0
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
      prompt_locales:
        zh-TW: "核可編輯綱要，或要求修訂。"
      input_schema: decision
      decisions:
        - id: approve
          label: Approve brief
          label_locales:
            zh-TW: "核可綱要"
        - id: revise
          label: Request brief revision
          label_locales:
            zh-TW: "要求修訂綱要"
          requires_feedback: true
          correction: true
    - id: editorial-clarification
      pattern: answer_questions
      prompt: Answer the editorial clarification questions.
      prompt_locales:
        zh-TW: "回覆編輯相關的釐清問題。"
      input_schema: answers
      questions:
        - id: audience
          prompt: Who is the intended audience?
          prompt_locales:
            zh-TW: "目標讀者是誰？"
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

# Revise Editorial Brief

## Role
Read your agent file: {agent_file}

## Instructions
Update the brief from the complete editorial correction source or clarification results while preserving the audience and argument. Preserve every incoming Todo item ID.

## Output
Write revised brief to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
