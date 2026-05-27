---
name: spec
description: "收集、整理或修訂需求規格（依 iteration 切換行為）"
version: 1.0.0
---

# Spec

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

依 iteration 不同採取不同流程：

- **第一輪（iteration == 1）**：閱讀原始需求與既有輸出，整理規格內容並寫入輸出檔。
- **後續輪（iteration > 1）**：讀取上一版 spec 輸出與使用者回饋，修訂內容並寫回指定輸出檔；不要在輸出中暗示先前迭代的存在。

共通規則：

- User 確認暫停、交給 `plan` 前是否執行 GitHub sync、以及 baton 順序：請依 shared skill「workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**，並搭配 `github_sync` skill；本 skill 不重複敘述。
- 第一次草稿需 user 確認時：把 blackboard `current_step` 改成 `user`，並把 next-step baton 寫入 `user`，不要直接交給 `plan`（其餘細節以 workflow-common 為準）。
- 後續輪若仍需 user 再看一輪：同樣把 next-step baton 寫入 `user`。
- 若資訊不足，輸出 `questions.xml` 並依 workflow-common 暫停給 `user`。

## Output
Write spec to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
