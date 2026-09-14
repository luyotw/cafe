---
name: cafe-brief_revise
description: 依回饋修訂內容大綱（編輯流程）
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
    - artifacts: [review_feedback]
      placeholder: correction_source
      required: false
---

# Revise Editorial Brief

## Role
Read your agent file: {agent_file}

## Instructions
依完整的審閱 correction source 或釐清結果更新大綱，維持受眾與論述主軸一致；保留輸入 Todo item IDs。

## Output
Write revised brief to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
