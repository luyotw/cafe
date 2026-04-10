---
name: spec_revise
description: "依回饋修訂需求規格"
version: 1.0.0
---

# Spec Revise

## Role
Read your agent file: {agent_file}

## Context
{blackboard_digest}

## Instructions
- 讀取上一版 spec 輸出與使用者回饋
- 修訂內容並寫回指定輸出檔
- 若仍缺資訊，回傳 `CAFE_NEED_CLARIFICATION`
- 遇到需要 user 回答的情況時：
  - 把 blackboard `owner` 改成 `user`
  - 保留 `current_step=spec`
  - 不要寫新的 next-step baton

## Output
Write spec to: {output_file}

## Status Code
{status_code_instruction}
