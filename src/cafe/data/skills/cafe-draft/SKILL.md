---
name: cafe-draft
description: 依核定大綱撰寫初稿
version: 1.1.0
workflow:
  execution_profile:
    workload: content
    reasoning: standard
    risk_domains: [source-fidelity]
    fallback_strength: equivalent
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
