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
- 第一次把 spec 草稿整理完成後，先回傳 `CAFE_READY_FOR_REVIEW`，讓 workflow 先交給 user 確認；不要直接回 `CAFE_CONFIRMED`
- 只有在 workflow 已經帶著 user 的確認結果回來時，才可以回傳 `CAFE_CONFIRMED` 並往 `plan` 前進
- 若資訊不足，改回傳 `CAFE_NEED_CLARIFICATION` 並輸出 questions.xml
- 遇到需要 user 回答的情況時：
  - 把 blackboard `current_step` 改成 `user`
  - 寫入 next-step baton，內容只放 `user`
