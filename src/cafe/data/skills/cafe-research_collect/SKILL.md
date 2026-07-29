---
name: cafe-research_collect
description: 搜尋、整理與記錄來源（非軟體研究流程）
version: 1.0.1
workflow:
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
- 依照本輪結果更新 blackboard 與 next-step baton。
