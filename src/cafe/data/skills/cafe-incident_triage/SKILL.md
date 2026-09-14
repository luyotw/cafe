---
name: cafe-incident_triage
description: 分類與處置決策（維運應變流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, prioritization]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue triage.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [incident_recovery, incident_learning]
      placeholder: correction_source
      required: false
---

# Incident Triage

## Role
Read your agent file: {agent_file}

## Instructions
判定優先級、指派與緩解策略，必要時回到偵測步驟補齊資訊；收到 correction source 時完整消化其 canonical Todo items 並保留 IDs。

## Output
Write triage report to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
