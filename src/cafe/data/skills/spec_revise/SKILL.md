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

## Available scripts

- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Instructions
- 讀取上一版 spec 輸出與使用者回饋
- 修訂內容並寫回指定輸出檔
- User 確認暫停、交給 `plan` 前是否執行 GitHub sync、以及 baton 順序：請依 shared skill「workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**，並搭配 `github_sync` skill；本 skill 不重複敘述。
- 若仍需 user 再看一輪：把 next-step baton 寫入 `user`，不要直接交給 `plan`（其餘細節以 workflow-common 為準）。
- 若仍缺資訊，輸出 `questions.xml` 並依 workflow-common 暫停給 `user`。

## Output
Write spec to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
