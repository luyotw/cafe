---
name: spec_first
description: "收集與整理首次需求規格"
version: 1.0.0
---

# Spec First

## Role
Read your agent file: {agent_file}

## Context
{blackboard_digest}

## Instructions
- 閱讀需求與既有輸出
- 整理規格內容並寫入輸出檔
- 若資訊不足，改回傳 `CAFE_NEED_CLARIFICATION` 並輸出 questions.xml
- 遇到需要 user 回答的情況時：
  - 把 blackboard `owner` 改成 `user`
  - 保留 `current_step=spec`
  - 不要寫新的 next-step baton
