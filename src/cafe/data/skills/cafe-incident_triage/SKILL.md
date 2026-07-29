---
name: cafe-incident_triage
description: 分類與處置決策（維運應變流程）
version: 1.0.1
workflow:
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue triage.
      input_schema: feedback
---

# Incident Triage

## Role
Read your agent file: {agent_file}

## Instructions
判定優先級、指派與緩解策略，必要時回到偵測步驟補齊資訊。

## Output
Write triage report to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
