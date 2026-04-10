---
name: develop
description: "依計畫進行程式開發與測試"
version: 1.0.0
---

# Develop

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Instructions
- 依計畫逐項完成
- 先補測試再改程式
- 每輪完成後更新 checklist
- 維持既有 commit 風格與程式碼註解語言
- 優先重用現有模式與工具
- Runtime prompt 會提供 shared workflow blackboard 路徑與 next-step baton 路徑。
- blackboard `current_step` 代表目前 workflow 接下來要去哪個 phase；必要時可以把它設成 built-in phase `user`。
- 如果 reviewer 的要求合理，直接修正並用正常流程完成，不要多做 handoff。
- 如果你認為 reviewer 的要求不合理，先把技術理由寫進本輪 `output.md`，再把同樣的爭議摘要追加到 blackboard `events`。
- 第一次對 reviewer 提出異議時：
  - 把 blackboard `current_step` 改成 `review`
  - 寫入 next-step baton，內容只放 `review`
  - 然後回傳 `CAFE_NO_CHANGES_NEEDED`
- develop/review 對同一個爭議最多往返 3 輪。
- 如果 blackboard 已經顯示同一個 review feedback 已經往返到第 3 輪，且 reviewer 仍未接受，就不要再回新的 `CAFE_NO_CHANGES_NEEDED`。
- 遇到重複爭議時，改成請 user 仲裁：
  - 在 `questions.xml` 清楚列出雙方分歧與需要 user 決定的點
  - 在 blackboard 記錄「developer requests user arbitration」
  - 把 blackboard `current_step` 改成 `user`
  - 寫入 next-step baton，內容只放 `user`
  - 回傳 `CAFE_NEED_CLARIFICATION`
- 如果這輪開發工作已經真的完成，而且接下來應由 user 決定是否還要繼續，允許把 blackboard `current_step` 改成 `user`，並在 blackboard summary/event 說明目前已完成、等待 user 決策。
- 不要讓同一個 reviewer/developer 爭議在沒有新資訊的情況下無限往返。

## Status Code
{status_code_instruction}
