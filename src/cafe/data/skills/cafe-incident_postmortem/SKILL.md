---
name: cafe-incident_postmortem
description: 事後檢討與行動項目（維運應變流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: standard
    risk_domains: [root-cause, prevention]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue the postmortem.
      input_schema: feedback
---

# Incident Postmortem

## Role
Read your agent file: {agent_file}

## Instructions
整理根因、時間線、學到的教訓與預防措施；若事件仍在演變，回到分類或偵測更新狀態。

## Output
Write postmortem to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
