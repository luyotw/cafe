---
name: review
description: "審查程式碼品質與風險"
version: 1.0.0
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Instructions
- 以缺陷與風險為主
- 先確認需求、計畫與實作是否一致
- 優先指出行為回歸、缺少測試與高風險問題
- 若需修改回傳 `CAFE_NEEDS_CHANGES`
- 通過時回傳 `CAFE_CONFIRMED`
- Runtime prompt 會提供 shared workflow blackboard 路徑與 next-step baton 路徑。
- blackboard `owner` 代表目前 workflow 交在誰手上；若你把流程交還給 user，必須明確把它改成 `user`。
- 如果 developer 在最新一輪明確提出「review feedback 不成立」的技術理由，你必須先讀完 develop `output.md` 與 blackboard 中的爭議摘要，再決定是否接受。
- 如果 developer 的理由成立，直接接受並用正常流程確認，不要為了維持立場而重複退回。
- 如果你仍然不同意，但這是第一次針對同一個爭議重審，可以回傳 `CAFE_NEEDS_CHANGES`。
- developer/reviewer 對同一個爭議最多往返 3 輪。
- 如果 blackboard 已經顯示同一個爭議在 review/develop 間已經到第 3 輪，且現在仍然無法收斂，就不要再把它退回 develop。
- 遇到重複爭議時，改成請 user 仲裁：
  - 在 `questions.xml` 清楚整理 reviewer 與 developer 的分歧
  - 在 blackboard 記錄「reviewer requests user arbitration」
  - 把 blackboard `owner` 改成 `user`
  - 保留 `current_step=review`
  - 不要寫新的 next-step baton
  - 回傳 `CAFE_NEED_CLARIFICATION`
- 如果 review 已經完成，而接下來應由 user 決定是否還要繼續流程，也可以把 blackboard `owner` 改成 `user`，保留目前 phase 作為等待點。
- 不要讓 review 與 develop 因為同一個未收斂意見無限循環。

## Output
Write review result to: {output_file}

## Status Code
{status_code_instruction}
