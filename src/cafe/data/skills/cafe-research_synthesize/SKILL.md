---
name: cafe-research_synthesize
description: 綜合發現與交叉驗證（非軟體研究流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: research
    reasoning: high
    risk_domains: [conflicting-evidence, inference]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue synthesis.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [research_report_doc]
      placeholder: correction_source
      required: false
---

# Research Synthesize

## Role
Read your agent file: {agent_file}

## Instructions
整合多來源的發現，指出共識、歧異與尚待驗證之處，形成可寫入報告的論點骨架；收到 correction source 時完整消化其 canonical Todo items 並保留 IDs。

## Output
Write synthesis to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
