---
name: cafe-research_synthesize
description: 綜合發現與交叉驗證（非軟體研究流程）
version: 1.0.1
workflow:
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue synthesis.
      input_schema: feedback
---

# Research Synthesize

## Role
Read your agent file: {agent_file}

## Instructions
整合多來源的發現，指出共識、歧異與尚待驗證之處，形成可寫入報告的論點骨架。

## Output
Write synthesis to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
