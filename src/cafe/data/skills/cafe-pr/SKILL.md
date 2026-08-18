---
name: cafe-pr
description: "整理提交內容並產出 pull request 標題與描述"
version: 1.2.0
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains: [external-side-effects]
    fallback_strength: equivalent
  human_tasks:
    - id: local-review
      pattern: confirm_output
      prompt: Review the prepared local changes and choose how to continue.
      input_schema: decision
      decisions:
        - id: approve
          label: Approve
        - id: request_changes
          label: Request changes
          requires_feedback: true
          correction: true
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [plan]
      placeholder: plan_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [code]
      placeholder: develop_file
      required: false
    - artifacts: [review_feedback]
      placeholder: feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  prompt_references:
    spec_context: pr_spec_context.md
    plan_context: pr_plan_context.md
  checklist:
    context_references:
      spec_read_instruction: spec_read_instruction.md
      plan_read_instruction: plan_read_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_steps_iteration_1.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2}
        sections:
          - reference: execution_steps_iteration_n.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# PR

## Role
Read your agent file: {agent_file}

## Context
{spec_context}{plan_context}

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

When the generic runtime includes a handoff block for the PR step, it repeats
local-first completion and publish ordering; treat that text as authoritative
alongside this skill.

## Instructions

### PR review comments mode
如果 `Current user input for this iteration` 內是 PR review comments：
- 把 comments 整理成 developer 可執行的 todo list
- 只把 todo list 寫到輸出檔，不要混入原始 PR comments
- 把 next-step baton 寫成 `develop`

### PR content mode
其他情況（沒有 PR review comments）：

1. 閱讀本 workflow 提供的需求規格、實作計畫與目前分支上的 commits
2. 編輯 `{output_file}`，產出 PR title 與 description：
   - Title 必須放在第一行 `#` 標題，精簡清楚，不超過 80 字元
   - Body 維持 `Summary`、`Changes`、`Test Plan` 結構
3. 不要直接呼叫 GitHub connector、GitHub API、`gh pr create`，也不要自行執行 `scripts/sync_pr.sh`
4. 不要查詢或等待遠端 branch/PR；遠端 publish 是 agent 回傳後才由 host-side hook 執行
5. 完成本地 PR artifact 與 checklist 後，依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新
6. CAFE host-side hook 會執行 `scripts/sync_pr.sh --output {output_file}`，依 `issue.yaml` 的 `base_branch` 自動加上 `--base`
7. Hook 會把 PR URL 作為 `pr_synced` event 回傳，CLI 會印出 PR URL
8. 完成本地 artifact 後，把 next-step baton 寫成 `done`；不要用 response text status code 代表 workflow completion

### Gotchas
- Script 的 progress/error 輸出在 stderr，JSON result 在 stdout
- PR 已存在時 script 會 update（idempotent），不會重複建立
- 對外網路、GitHub 憑證、push/create/update PR 都由 host-side hook 處理，避免 agent sandbox 阻擋
- 如果遠端 branch/PR 尚不存在，這是 hook 執行前的正常狀態，不是 PR phase 未完成
- 不要在回應中重述 PR 內容；用 blackboard 與 next-step baton 表達 handoff。

## Output
Write PR content to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
