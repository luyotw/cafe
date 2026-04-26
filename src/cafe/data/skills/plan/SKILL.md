---
name: plan
description: "產出可執行的開發計畫"
version: 1.0.0
---

# Plan

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}

## Available scripts

- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Instructions
- 依規格拆解實作步驟，先列測試，再列實作
- 嚴格遵守 TDD，避免直接寫程式碼
- 延續既有計畫格式與使用者需求
- 計畫草稿完成後，把 blackboard `current_step` 改成 `user`，並在 next-step baton 寫入 `user`，讓 workflow 先交給 user 確認；不要直接交給 `develop`
- 只有在 user 已確認計畫可接受時，才可以把 next-step baton 寫成 `develop`
- 如果這輪要交給 `develop`，先依照 issue 設定判斷是否需要同步，若需要就先執行：
  ```bash
  bash scripts/sync_github.sh --phase plan --output {output_file}
  ```
  - Script 會輸出 JSON 到 stdout（`action=commented|skipped`）
  - 同步執行完再把 next-step baton 寫成 `develop`
- 若計畫仍需要 user 決定或補充資訊：
  - 把 blackboard `current_step` 改成 `user`
  - 寫入 next-step baton，內容只放 `user`

## Output
Write plan to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
