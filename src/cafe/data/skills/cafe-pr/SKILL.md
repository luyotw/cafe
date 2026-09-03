---
name: cafe-pr
description: "整理提交內容並產出 pull request 標題與描述"
version: 1.4.0
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains: [external-side-effects]
    fallback_strength: equivalent
  human_tasks:
    - id: local-review
      pattern: confirm_output
      prompt: Review the prepared local changes and the Follow-up Proposals section in the PR description. Your decision applies to every open FUP; per-proposal mixed disposition is not supported. Fix all proposals now, record that all should become separate issues, or approve and continue without issues.
      input_schema: decision
      decisions:
        - id: fix_now
          label: Fix all proposed items now
          requires_feedback: true
          correction: true
        - id: create_follow_up
          label: Record issues for all proposals
        - id: continue_without_issue
          label: Approve / continue without issues
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
    - artifacts: [qa_feedback, review_feedback]
      placeholder: feedback_file
      required: false
    - artifacts: [review_feedback]
      placeholder: review_feedback_file
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
      review_feedback_instruction: review_feedback_instruction.md
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
   - Body 維持 `Summary`、`Changes`、`Test Plan`、`Follow-up Proposals` 結構
   - 從 workflow input 明確列出的 `review_feedback_file` 中，複製最新 review feedback `## Follow-up Proposals` 內每個 `status: open` 的穩定 `FUP-NNN` ID、impact、confidence、evidence 摘要與 draft issue title/body；不得改寫 ID 或自行新增 proposal
   - 沒有 open proposal 時明寫 `None`；有 proposal 時明寫 PR HumanTask 的單一選擇會套用全部 open `FUP-NNN`，不支援逐項混合處置，而 `create_follow_up` 只記錄 user 要求，不會自動建立 GitHub issue
3. 不要直接呼叫 GitHub connector、GitHub API、`gh pr create`，也不要自行執行 `scripts/sync_pr.sh`
4. 不要查詢或等待遠端 branch/PR；遠端 publish 是 agent 回傳後才由 host-side hook 執行
5. 完成本地 PR artifact 與 checklist 後，依本輪注入的 `{step_transitions}` 選擇 next-step baton：宣告 `confirm_output` 時交給 `user` review；只有宣告 `workflow_complete→done` 時才直接完成；不得選擇未宣告的路由，也不得代替 user 處置 follow-up proposal
6. CAFE host-side hook 會在有效 handoff 進入人工 review 或完成前執行 `scripts/sync_pr.sh --output {output_file}`，依 `issue.yaml` 的 `base_branch` 自動加上 `--base`
7. Hook 會把 PR URL 作為 `pr_synced` event 回傳，CLI 會印出 PR URL
8. 當 `{step_transitions}` 宣告 `confirm_output` 時，只有綁定 HumanTask 的核准結果可以完成 workflow；PR agent 不得改寫成 `done` 或 `workflow_complete`

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
