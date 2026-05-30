# CAFE v0.3: Human-Agent Workflow + Trusted Capability Milestones

本文件是 **v0.3 的 milestone plan**。
版本定位、長期邊界、以及 `v0.4+` 的演進方向以 [docs/roadmap.md](../roadmap.md) 為準；本文件只定義 `v0.3` 要把哪些能力做成可驗收的交付。

## 版本定位

v0.3 的主軸不是再擴大 workflow engine，而是把 CAFE 從「agent 依序執行 steps」推進到「agent 與 human 都是一級執行者」。

這版要回答的產品問題：

- 小團隊是否願意把人類任務正式掛進 CAFE，而不是把協作退回 Slack、Notion、Linear 或口頭 handoff？

這版要回答的架構問題：

- `HumanTask` 是否能自然嵌入 playbook / blackboard / baton model，而不是變成另一種 `need_clarification`？
- host-side mutation 是否能透過 capability contract / policy / receipt 執行，而不是把 script path 本身當成權限？

## 核心需求

1. **HumanTask 是一級模型** — 人類任務有明確 assignee、instructions、completion schema、due date、status、result。
2. **WaitState 是 runtime 狀態** — workflow 可因 human task、approval、capability receipt 暫停，並在完成後自動 resume。
3. **Playbook 可宣告 ownership** — step 可明確標示 `agent` / `human` / `hybrid` / `auto`，且語意可驗證。
4. **Inbox 是必要操作面** — CLI 至少能列出、查看、完成 pending human tasks。
5. **CapabilityContract 取代 script path 權限** — workflow 只能要求已註冊 capability，host 再依 policy 決定是否執行。
6. **File-based runtime store 延續 v0.2** — v0.3 仍以 `.cafe/issues/` + JSON/YAML/MD 實作，不引入 structured store。

## 新增抽象

| 抽象 | 用途 |
|---|---|
| `HumanTask` | workflow 建立給人執行的工作項目 |
| `TaskResult` | human 完成任務後回填的結構化結果 |
| `WaitState` | workflow 暫停原因、resume 條件、關聯 task / capability request |
| `Assignment` | step / task 的 owner、role、assignee type |
| `CapabilityContract` | host 可執行 capability 的名稱、args schema、權限、輸入輸出 |
| `CapabilityPolicy` | host 是否允許、拒絕、或要求 approval 的規則 |
| `ExecutionRequest` | agent / workflow 對 host capability 的 declarative request |
| `ExecutionReceipt` | host 執行外部 side effect 後回寫的可驗證結果 |

## Runtime store

v0.3 延續 v0.2 的 `WorkflowInstance`：

```text
.cafe/issues/{issue}/
  blackboard.json
  tasks/
    task_001.yaml
    task_002.yaml
  waits/
    current_wait.json
  capabilities/
    requests/
      request_001.json
    receipts/
      receipt_001.json
```

原則：

- `blackboard.json` 仍是 workflow instance 的摘要狀態與事件來源。
- `tasks/` 保存可被 inbox 操作的 human task。
- `waits/current_wait.json` 保存 workflow 為何暫停、等待什麼條件。
- `capabilities/requests/` 與 `capabilities/receipts/` 保存 host mutation 的 contract 與結果。
- v0.3 不把 task inbox 做成跨 repo / org store；那是 v0.4+ structured store 評估範圍。

## Playbook schema 擴充

### Human-owned step

```yaml
steps:
  interview:
    type: task
    role: hiring_manager
    assignee_type: human
    task:
      title: "Interview candidate"
      instructions: "Review the resume and fill the scorecard."
      completion_schema:
        type: object
        required: [decision, notes]
        additionalProperties: false
        properties:
          decision:
            type: string
            enum: [advance, reject, hold]
          notes:
            type: string
      due_in: "P2D"
    output_artifact: interview_result
    "on":
      task_completed: next_step
      task_cancelled: _failed
```

### Hybrid step

```yaml
steps:
  content_review:
    type: skill
    skill: content_review
    role: editor
    assignee_type: hybrid
    human_task:
      trigger_intents: [needs_human_review]
      completion_schema_ref: schemas/editor_review.json
    "on":
      await_agent: publish_request
      task_completed: content_review
```

### Host capability request

```yaml
hooks:
  publish_output:
    - capability: publish_pr
      args:
        title: "{artifact:pr_title}"
        body: "{artifact:pr_body}"
      require_receipt: pr_synced
```

## Milestones

### Milestone A: HumanTask contract 與 runtime 狀態

目標：先定義穩定資料模型與 transition 語意，避免 inbox / UI 先綁死錯誤抽象。

交付：

1. 定義 `HumanTask` / `TaskResult` / `WaitState` / `Assignment` model。
2. 擴充 Blackboard event types：
   - `human_task_created`
   - `human_task_completed`
   - `human_task_cancelled`
   - `workflow_waiting`
   - `workflow_resumed`
3. 定義 task lifecycle：
   - `pending`
   - `in_progress`
   - `completed`
   - `cancelled`
   - `expired`
4. 定義 `assignee_type: auto` 語意：
   - 若 step 有可自動完成的 agent path，先跑 agent。
   - 若 agent 回傳需要人類輸入的 intent，建立 human task。
   - 不允許 auto 靜默跳過 required human completion schema。
5. `BlackboardWorkflowRuntime` 能在遇到 human-owned step 時建立 task、寫入 wait state、暫停 workflow。

完成標準：

- playbook 可宣告 `assignee_type: human` 並被 runtime 正式處理。
- workflow 遇到 human task 時不再報 v0.2 reserved error，而是建立 task 並 pause。
- wait state 可被序列化、讀回、用於 resume 判斷。

### Milestone B: CLI task inbox 與 completion flow

目標：提供最小可用的人類操作面，讓 human task 可被看見、完成、取消。

交付：

1. 新增 CLI：
   - `cafe task list`
   - `cafe task show <task-id>`
   - `cafe task complete <task-id> --payload <file-or-json>`
   - `cafe task cancel <task-id>`
2. `task complete` 會驗證 completion payload schema。
3. task completion 會寫入 `TaskResult`，並 publish 對應 output artifact。
4. workflow resume 時能消費 task result，依 `on.task_completed` transition 接續。
5. `cafe summary` / timeline 顯示 pending task 與 current wait reason。

完成標準：

- 一條 human-owned step 可透過 CLI inbox 完成，完成後 workflow 自動接下一步。
- completion payload schema 驗證失敗時，不會污染 blackboard 或 artifact。
- pending task 在 summary 中可見，避免 workflow 看起來像卡死。

### Milestone C: Human-agent playbook validation samples

目標：驗證 v0.3 schema 真的能表達非軟體開發流程，而不是只適配 default dev workflow。

交付：

1. 內建或 sample playbook 至少新增一條非 dev 流程：
   - 招募：resume screen → human interview → agent summary → decision
   - 或 content production：agent draft → human edit/review → publish request
2. `cafe playbook validate` 支援：
   - human task completion schema 檢查
   - `task_completed` / `task_cancelled` transition 檢查
   - `assignee_type` 與 `type` 的一致性檢查
   - hybrid step 的 `trigger_intents` 檢查
3. dry-run / mock executor 能走過 human task pause/resume path。

完成標準：

- 至少一條非軟體開發流程可 `validate`。
- 至少一條非軟體開發流程可用 mock / dry-run 驗證 human task pause/resume。
- role model 不再假設所有 owner 都是 PM / developer / reviewer。

### Milestone D: Trusted capability contract registry

目標：把 host-side mutation 從 script path execution 收斂為 capability contract execution。

交付：

1. 定義 `CapabilityContract` manifest：
   - `name`
   - `description`
   - `entrypoint`
   - `args_schema`
   - `expected_receipts`
   - `permissions`
   - `writes`
   - `network_destinations`
   - `requires_approval`
2. 定義 registry lookup：
   - builtin registry
   - project registry
   - global registry
3. 定義 policy outcome：
   - `allow`
   - `ask_approval`
   - `deny`
4. workflow / hook 只能引用 `capability` 名稱，不直接引用任意 host script path。
5. host 執行後必須寫入 `ExecutionReceipt`，runtime 以 receipt gate 判斷外部 side effect 是否完成。

完成標準：

- 至少一條 host mutation 流程透過 capability registry 執行。
- 未註冊 capability 不會被執行。
- args schema 不合法時不會觸發 host mutation。
- 缺少 required receipt 時 workflow 會保持 wait / blocked，而不是誤判完成。

### Milestone E: v0.3 end-to-end validation

目標：用端到端情境確認 HumanTask 與 capability model 能一起工作。

交付：

1. 招募 / onboarding / content 類流程至少完成一條 E2E smoke。
2. default dev workflow 至少保留相容 smoke：
   - `cafe make`
   - pause/resume
   - PR publish receipt gate
3. 新增 manual test log：
   - `docs/manual-test-v03.md`
4. 更新使用者文件：
   - Human task authoring
   - Task inbox CLI
   - Capability registry / policy

完成標準：

- mixed agent + human workflow 可從 start 跑到 done。
- human task completion 後 workflow 可自動接續。
- host mutation 只能透過 trusted capability + receipt 完成。
- v0.2 playbook / skill 仍可讀取，default workflow 不因 v0.3 schema 擴充而破壞。

## v0.3 不做的東西

1. **不做 multi-user web dashboard** — CLI inbox 足以驗證核心模型；dashboard 留到後續產品化。
2. **不做 org-wide task store** — v0.3 的 task store 綁定單一 workflow instance。
3. **不做 BusinessObject store** — v0.4 再處理跨流程物件與 structured store。
4. **不做 subflow runtime** — v0.3 只確保 wait/task model 不阻礙 v0.4 subflow。
5. **不做 agent-authored script auto-trust** — agent 新寫 script 仍需 manifest / policy / approval promotion。
6. **不做 arbitrary host shell execution** — host 權限只來自 capability registry。

## 驗證方式

1. **Milestone A**：
   - unit tests 覆蓋 HumanTask / TaskResult / WaitState serialization。
   - runtime test 覆蓋 human-owned step 建 task 並 pause。
2. **Milestone B**：
   - CLI tests 覆蓋 task list/show/complete/cancel。
   - integration test 覆蓋 task completion 後 resume。
3. **Milestone C**：
   - playbook validator tests 覆蓋 human / hybrid / auto schema。
   - sample non-dev playbook 通過 validate + dry-run。
4. **Milestone D**：
   - capability registry tests 覆蓋 allow / ask_approval / deny。
   - receipt gate tests 覆蓋 missing / invalid / accepted receipt。
5. **Milestone E**：
   - 端到端 smoke 覆蓋 mixed agent + human workflow。
   - manual test log 記錄 CLI inbox 與 capability receipt 行為。

## Sequencing 原則

建議順序：

1. 先做 data contract 與 wait state。
2. 再做 task inbox，讓人工任務可以被操作。
3. 再擴 validate / sample playbook，確認 schema 泛用性。
4. 再收斂 host capability registry，避免和 HumanTask 同時改太多 runtime 面。
5. 最後做 E2E smoke、manual test log、docs。

避免順序：

- 不先做 dashboard；否則會把 task model 綁到尚未驗證的 UI。
- 不先做 structured store；否則會把 v0.3 的核心風險擴大到 storage migration。
- 不讓 agent-authored script 直接取得 host 權限；否則會破壞 v0.3 的 trust model。
