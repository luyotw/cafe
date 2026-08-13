---
name: cafe-research_question
description: 成形研究問題與假設邊界（非軟體研究流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: standard
    risk_domains: [scope, assumptions]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to refine the research question.
      input_schema: feedback
---

# Research Question

## Role
Read your agent file: {agent_file}

## Instructions
把主題收斂成可驗證的研究問題：範圍、成功定義、已知限制與待釐清假設。

## Output
Write research question to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
