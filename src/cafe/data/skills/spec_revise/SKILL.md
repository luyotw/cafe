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
- 如果這輪修訂後還需要 user 再確認內容，回傳 `CAFE_READY_FOR_REVIEW`；不要直接回 `CAFE_CONFIRMED`
- 只有在 user 已經確認規格可接受時，才回傳 `CAFE_CONFIRMED` 並往 `plan` 前進
- 若仍缺資訊，回傳 `CAFE_NEED_CLARIFICATION`
- 遇到需要 user 回答的情況時：
  - 把 blackboard `current_step` 改成 `user`
  - 寫入 next-step baton，內容只放 `user`

## Output
Write spec to: {output_file}

## Status Code
{status_code_instruction}
