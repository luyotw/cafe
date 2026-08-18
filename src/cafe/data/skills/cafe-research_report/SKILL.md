---
name: cafe-research_report
description: 產出研究報告（非軟體研究流程）
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
---

# Research Report

## Role
Read your agent file: {agent_file}

## Instructions
依讀者需求撰寫報告：結論、證據、限制與後續建議；格式以 Markdown 為主。

## Output
Write report to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
