---
name: cafe-incident_mitigate
description: 緩解與復原（維運應變流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, state-change, rollback]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue mitigation.
      input_schema: feedback
---

# Incident Mitigate

## Role
Read your agent file: {agent_file}

## Instructions
執行緩解措施、驗證服務恢復，並記錄變更與回滾點；狀況變更時可回到分類或偵測。

## Output
Write mitigation log to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
