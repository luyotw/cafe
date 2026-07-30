---
name: cafe-draft
description: 依核定大綱撰寫初稿
version: 1.0.1
workflow:
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue drafting.
      input_schema: feedback
---

# Draft Article

## Role
Read your agent file: {agent_file}

## Instructions
依大綱撰寫初稿：結構清楚、論述具體、符合讀者情境。

## Output
Write draft to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
