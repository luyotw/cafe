---
name: cafe-incident_detect
description: 偵測與通報事件徵兆（維運應變流程）
version: 1.0.0
---

# Incident Detect

## Role
Read your agent file: {agent_file}

## Instructions
記錄事件現象、影響範圍、時間線與初步嚴重度，準備交給分類／處置決策。

## Output
Write incident report to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
