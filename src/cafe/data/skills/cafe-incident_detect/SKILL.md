---
name: cafe-incident_detect
description: 偵測與通報事件徵兆（維運應變流程）
version: 1.1.0
workflow:
  execution_profile:
    workload: operations
    reasoning: high
    risk_domains: [service-impact, incomplete-evidence]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the incident details needed to continue detection.
      input_schema: feedback
---

# Incident Detect

## Role
Read your agent file: {agent_file}

## Instructions
記錄事件現象、影響範圍、時間線與初步嚴重度，準備交給分類／處置決策。

## Output
Write incident report to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
