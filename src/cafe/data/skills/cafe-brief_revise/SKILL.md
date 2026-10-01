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
      prompt: {message_key: human_task.cafe_brief_revise.editorial_output_review.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_brief_revise.editorial_output_review.prompt}
      input_schema: decision
      decisions:
        - id: approve
          label: {message_key: human_task.cafe_brief_revise.editorial_output_review.decisions.approve.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_brief_revise.editorial_output_review.decisions.approve.label}
        - id: revise
          label: {message_key: human_task.cafe_brief_revise.editorial_output_review.decisions.revise.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_brief_revise.editorial_output_review.decisions.revise.label}
          requires_feedback: true
          correction: true
    - id: editorial-clarification
      pattern: answer_questions
      prompt: {message_key: human_task.cafe_brief_revise.editorial_clarification.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_brief_revise.editorial_clarification.prompt}
      input_schema: answers
      questions:
        - id: audience
          prompt: {message_key: human_task.cafe_brief_revise.editorial_clarification.questions.audience.prompt}
          prompt_locales:
            zh-TW: {message_key: human_task.cafe_brief_revise.editorial_clarification.questions.audience.prompt}
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
