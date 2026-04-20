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

## Instructions

### PR review comments mode
如果 `Current user input for this iteration` 內是 PR review comments：
- 把 comments 整理成 developer 可執行的 todo list
- 只把 todo list 寫到輸出檔，不要混入原始 PR comments
- 回傳 `CAFE_NEEDS_CHANGES`

### PR content mode
其他情況（沒有 PR review comments）：

1. 閱讀需求規格、實作計畫與目前分支上的 commits
2. 編輯 `{output_file}`，產出 PR title 與 description：
   - Title 必須放在第一行 `#` 標題，精簡清楚，不超過 80 字元
   - Body 維持 `Summary`、`Changes`、`Test Plan` 結構
3. 執行 sync script 把 PR 推上 GitHub：
   ```bash
   bash scripts/sync_pr.sh --output {output_file} --base {base_branch}
   ```
   - 如果 issue.yaml 有 `base_branch`，加上 `--base <base_branch>`
  - Script 輸出 JSON 到 stdout（`{"action":"created"|"updated","pr_number":"...","pr_url":"..."}`）
  - 若 `issue.yaml` 的 `pr.post_todo_list=true`，script 會在 PR create/update 時自動檢查最新 todo list iteration，只有在全部項目都已完成 `[x]` 才會貼到 PR comment
   - 若 handoff 要求「重發 PR / 重開 PR / 重新同步 PR」，必須確認最後 GitHub 上存在符合目前 branch 與 `{base_branch}` 的 open/draft PR
   - 已關閉的舊 PR 不算完成 handoff；如果目前只剩 closed PR，應建立新的 PR
4. 把 PR URL 寫到 blackboard（`current_step` 改成 `user`，summary 說明 PR 已同步）
5. 寫入 next-step baton，內容只放 `user`
6. 回傳 `CAFE_CONFIRMED`

### Gotchas
- Script 的 progress/error 輸出在 stderr，JSON result 在 stdout
- PR 已存在時 script 會 update（idempotent），不會重複建立
- 不要在回應中重述 PR 內容，只回傳狀態碼

## Output
Write PR content to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
