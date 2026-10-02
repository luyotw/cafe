---
name: cafe-research_question
description: Define a research question and its assumption boundaries
version: 1.2.0
workflow:
  notification:
    task_labels:
      clarification-feedback:
        message_key: notification.action_labels.clarification_feedback
  execution_profile:
    workload: research
    reasoning: standard
    risk_domains: [scope, assumptions]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_research_question.clarification_feedback.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_research_question.clarification_feedback.prompt}
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
