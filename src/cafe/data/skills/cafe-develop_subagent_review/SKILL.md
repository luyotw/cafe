---
name: cafe-develop_subagent_review
description: "以 detail、scope 兩個原生 subagent 完成 PR 前審查的 Develop checklist overlay"
version: 2.0.0
workflow:
  required_tools:
    - Agent
  checklist_overlay:
    variants:
      - when: {}
        sections:
          - reference: dual_review_gate.md
---

# Dual-subagent review overlay

### Review policy
- 完成實作與 targeted checks 後，parent agent 必須啟動剛好兩個原生 subagent：`detail` 與 `scope`；兩者都啟動後才能等待任一結果，使審查並行。沒有 repository change 時也必須讓兩者審查 no-change reasoning；若 user 已接受 `no_changes_needed` 並回到本 step，取得雙方 approval 後以正常 `await_agent` handoff，不得再次用 `no_changes_needed` 繞過 gate。
- 兩位 reviewer 都取得同一份權威需求、目前完整 diff／HEAD 與測試 evidence。`detail` 檢查 correctness、edge cases、regression risk 與 tests；`scope` 檢查 issue acceptance、omissions、overreach 與 architectural placement。
- Reviewer 僅能審查，不得編輯檔案、commit、改變 workflow state 或執行外部 mutation；所有修正由 parent agent 負責。
- 任一 reviewer 回報 blocking issue 時，不得 handoff。Parent agent 修正成立的 blocker、重跑相關 targeted checks，然後重新並行啟動 `detail` 與 `scope`；任何修正都使先前兩份 approval 失效。
- 若 parent agent 判定 finding 不成立，必須提供具體 evidence 並取得該 reviewer 新一輪明確的 no-blocking 結論，不得自行降級或忽略 blocker。
- 只有同一輪的 `detail` 與 `scope` 都明確回報 no blocking issues，才能把 baton 交給 playbook 的下一個 step。
- 若原生 subagent 能力不可用，或技術限制使 review 無法完成，寫出真實狀態並使用 `manual_handoff`；不得偽造 reviewer 結論。
