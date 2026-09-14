---
name: cafe-pr
description: "整理提交內容並產出 pull request 標題與描述"
version: 1.4.1
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
agent-local-first completion and the confirmed workflow publication mode; treat
that text as authoritative alongside this skill. `pr.auto_create: false` means
the workflow is `local-only`, while `true` means the host must publish before
the review task can expose a verified PR URL.

## Instructions

### Corrective feedback curation mode
當 `workflow_feedback_file` 有本輪回饋，或 `Current user input for this iteration` 包含 PR review comments 時，這是 PR iteration 2：

- 若 runtime 提供 `workflow_feedback_batch_file`，它是本輪唯一且不可變的 source context；只能從該 batch 選擇 Todo，不能讀取或分類 batch 以外、之後才出現的 `workflow_feedback_file` 項目。那些項目保留給後續 cycle。否則，`workflow_feedback_file` 與 review comments 都是 PR agent 的 source context，不是 Develop 的工作清單；只處理本輪宣告要送到本 step 的 unresolved corrective input。resolved、stale、重複觀測、ordinary PR body、`## Test Plan`、資訊性討論與未決 Follow-up Proposal 都不得匯入。
- 將 current corrective cycle 的每個適用 source 整理為輸出檔中唯一的 `## Todo List`（最多 100 列）；使用已宣告的 Todo source 與 ID prefix，保持 source identity 的一對一對應，不得用相同文字合併兩個不同 source。沒有適用 source 時只能寫 canonical marker `No actionable work.`，不得留下空白區段。
- Todo rows 必須符合 ``- [ ] `<id>` — Source: `<source>` — Work: ... — Closure: ... — Evidence: ...``；只把整理後的 Todo List 寫到輸出檔，不要混入原始 PR comments 或 raw HumanTask feedback。
- 完成 curation 後，依本輪注入的 `{step_transitions}` 寫入宣告的 `manual_handoff`；不得硬編碼 step 名稱、跳過 curator，或選擇未宣告的路由。

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
6. 當 `pr.auto_create: true` 時，CAFE host-side hook 會在有效 handoff 進入人工 review 或完成前執行 `scripts/sync_pr.sh --output {output_file}`，依 `issue.yaml` 的 `base_branch` 自動加上 `--base`；只有本次成功且通過 output contract 的結果可產生 `pr_synced` evidence 與 review task 的 verified PR URL
7. 當 `pr.auto_create: false` 時，workflow 是 `local-only`：hook 不發布、不沿用舊 URL，review task 明示 `Publication mode: local-only. No PR URL exists.`
8. 當 `{step_transitions}` 宣告 `confirm_output` 時，只有綁定 HumanTask 的核准結果可以完成 workflow；PR agent 不得改寫成 `done` 或 `workflow_complete`

### Publication authority
- PR 內容與發布只依本 phase 和 `cafe.pr.publish` capability 契約處理；kickoff 問題、選項和 prepare 參數由 capability manifest 的 `setup_questions` 宣告。
- 建立或更新 PR、審查通過、workflow 完成，都不代表獲准 merge 或關閉 issue；本 phase 不執行這些操作。
- Merge 必須是另有明確授權的 integration 工作；不要因使用者說「剩下的做完」就自行執行。

### Gotchas
- Script 的 progress/error 輸出在 stderr，JSON result 在 stdout
- PR 已存在時 script 會 update（idempotent），不會重複建立
- 發布失敗、permission denied 或成功 receipt 缺少 URL 時，不得建立看似成功的 `local-review` handoff；approval resume 與直接成功使用同一個 validated `pr_synced` evidence contract
- 對外網路、GitHub 憑證、push/create/update PR 都由 host-side hook 處理，避免 agent sandbox 阻擋
- 如果遠端 branch/PR 尚不存在，這是 hook 執行前的正常狀態，不是 PR phase 未完成
- 不要在回應中重述 PR 內容；用 blackboard 與 next-step baton 表達 handoff。

## Output
Write PR content to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
