---
name: pr
description: "整理提交內容並產出 pull request 標題與描述"
version: 1.0.0
---

# PR

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Commits
{commits}

## Available scripts

- **`scripts/sync_pr.sh`** — Push branch, create/update GitHub PR, and (when enabled) post completed todo list comment

```bash
bash scripts/sync_pr.sh --help
```

In workflow mode, do not run this script directly from the agent. The CAFE
host-side `GitHubPRCreator` publish hook runs it after the PR artifact is ready,
so GitHub/network access happens outside the agent sandbox.

Important ordering: the host-side publish hook cannot run until this agent
finishes the local PR artifact and returns the workflow status. Do not wait for,
verify, or require a remote GitHub branch/PR before returning the status code.

## Instructions

### PR review comments mode
如果 `Current user input for this iteration` 內是 PR review comments：
- 把 comments 整理成 developer 可執行的 todo list
- 只把 todo list 寫到輸出檔，不要混入原始 PR comments
- 把 next-step baton 寫成 `develop`

### PR content mode
其他情況（沒有 PR review comments）：

1. 閱讀需求規格、實作計畫與目前分支上的 commits
2. 編輯 `{output_file}`，產出 PR title 與 description：
   - Title 必須放在第一行 `#` 標題，精簡清楚，不超過 80 字元
   - Body 維持 `Summary`、`Changes`、`Test Plan` 結構
3. 不要直接呼叫 GitHub connector、GitHub API、`gh pr create`，也不要自行執行 `scripts/sync_pr.sh`
4. 不要查詢或等待遠端 branch/PR；遠端 publish 是 agent 回傳後才由 host-side hook 執行
5. 完成本地 PR artifact 與 checklist 後，依照本輪結果更新 blackboard 與 next-step baton
6. CAFE host-side hook 會執行 `scripts/sync_pr.sh --output {output_file}`，依 `issue.yaml` 的 `base_branch` 自動加上 `--base`
7. Hook 會把 PR URL 作為 `pr_synced` event 回傳，CLI 會印出 PR URL
8. 若目前 runtime 仍透過 phase-level status code 完成遷移，才回傳 `CAFE_CONFIRMED`

### Gotchas
- Script 的 progress/error 輸出在 stderr，JSON result 在 stdout
- PR 已存在時 script 會 update（idempotent），不會重複建立
- 對外網路、GitHub 憑證、push/create/update PR 都由 host-side hook 處理，避免 agent sandbox 阻擋
- 如果遠端 branch/PR 尚不存在，這是 hook 執行前的正常狀態，不是 PR phase 未完成
- 不要在回應中重述 PR 內容；用 blackboard 與 next-step baton 表達 handoff。

## Output
Write PR content to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
