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
- User 確認暫停、交給 `develop` 前是否執行 GitHub sync、以及 baton 順序：請依 shared skill「workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**，並搭配 `github_sync` skill 的腳本說明；本 skill 不重複敘述。
- 計畫草稿需 user 確認時：把 next-step baton 寫入 `user`，不要直接交給 `develop`（其餘細節以 workflow-common 為準）。

## Output
Write plan to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
