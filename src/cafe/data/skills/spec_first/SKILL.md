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

## Available scripts

- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Instructions
- 閱讀需求與既有輸出
- 整理規格內容並寫入輸出檔
- 第一次把 spec 草稿整理完成後，把 blackboard `current_step` 改成 `user`，並在 next-step baton 寫入 `user`，讓 workflow 先交給 user 確認；不要直接交給 `plan`
- 只有在 workflow 已經帶著 user 的確認結果回來時，才可以把 next-step baton 寫成 `plan`
- 如果這輪要交給 `plan`，先依照 issue 設定判斷是否需要同步，若需要就先執行：
  ```bash
  bash scripts/sync_github.sh --phase spec --output {output_file}
  ```
  - Script 會輸出 JSON 到 stdout（`action=commented|skipped`）
  - 同步執行完再把 next-step baton 寫成 `plan`
- 若資訊不足，輸出 questions.xml 並把 next-step baton 寫成 `user`
- 遇到需要 user 回答的情況時：
  - 把 blackboard `current_step` 改成 `user`
  - 寫入 next-step baton，內容只放 `user`
