---
name: cafe-publish
description: 定稿與發佈前整理
version: 1.1.0
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains: [external-side-effects]
    fallback_strength: equivalent
---

# Publish Piece

## Role
Read your agent file: {agent_file}

## Instructions
產出可發佈版本：標題、摘要與最後潤飾說明。

## Output
Write final piece to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
