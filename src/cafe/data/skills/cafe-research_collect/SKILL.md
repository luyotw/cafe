---
name: cafe-research_collect
description: 搜尋、整理與記錄來源（非軟體研究流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: standard
    risk_domains: [source-quality, traceability]
    fallback_strength: equivalent
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue evidence collection.
      input_schema: feedback
---

# Research Collect

## Role
Read your agent file: {agent_file}

## Instructions
蒐集與整理資料來源，建立可追溯的筆記與引用，標註可信度與缺口。

## Output
Write collected sources to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
