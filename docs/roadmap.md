# CAFE Roadmap

## 北極星

讓創業者用 CAFE 遞迴建立、執行、追蹤整個公司的流程，並把 agent 做不到的工作明確切分給人執行，再把結果餵回流程繼續推進。

這代表 CAFE 長期不只是開發工作流工具，而是：

- 流程定義系統
- agent / human 協作系統
- 組織記憶系統
- 長週期營運系統

## 現況判斷

目前 CAFE 更接近：

- 一套以軟體開發為主的 workflow assistant
- 有 phase 概念，但 phase 固定
- 有 agent 協作，但 human task 不是一級公民
- 有歷史紀錄，但沒有組織級 knowledge / object model

v0.2 重構完成後，預期能到：

- 可宣告的流程引擎底座
- 可擴充的 step / skill / playbook 模型
- 具備跨 step 共享狀態的 Blackboard

但還不是公司流程 OS。

## 系統邊界立場

CAFE 長期採 **repo-first** 的 definition model，但不預設最終所有流程執行都只靠 repository 介面承載。

比較合理的長期分層是：

- **GitHub repository = definition layer**
  - playbooks
  - skills
  - templates
  - policies / SOP
  - 可版本化文件

- **runtime store = execution layer**
  - workflow instances
  - blackboard
  - pending tasks
  - business objects

- **execution interface = 操作層**
  - 早期以 CLI 為主
  - 中後期可能擴展到 TUI / local app / web app

這代表：
- repo 會長期是重要的 source of truth 之一
- 但 human collaboration / long-running workflows / task inbox / analytics 不應被硬綁在 repo 介面內
- roadmap 不預先承諾某一種前端產品形態，但會保留這個演化方向

短中期立場：
- `v0.2~v0.3` 的 runtime store 以 file-based（`.cafe/issues/ + JSON/YAML/MD`）為主
- `v0.4` 再評估是否需要抽離到 structured store（如 SQLite 或 remote store）

## 路線圖總覽

### v0.2

目標：
把 CAFE 從固定 phase 開發工具，重構成通用流程引擎底座。

核心能力：
- `Skill + Playbook + Blackboard + Hook`
- GenericPhase / PlaybookRunner
- 動態 step orchestration
- status code + `CAFE_GOTO`
- 共享 artifacts / events / decisions
- suspend / resume 基礎模型
- 明確定義 `WorkflowInstance`

這版刻意不做完整產品化：
- dashboard
- org-wide object model
- subflow recursion
- multi-team governance
- `HumanTask` 正式實作

完成標準：
- default 開發流程可完全跑在新架構上
- custom skill / custom playbook 可運作
- v0.2 的狀態機與 Blackboard 不會把人工介入硬綁成 `interactive_qa` 單一路徑
- 至少一條非軟體開發流程可用 custom playbook 完成 `writeable + validatable`
- 若範圍允許，再補 `simulatable`

這版應預留但不正式實作：
- human task event types / wait state 擴充入口
- subflow step type 擴充入口
- object reference 擴充入口
- `assignee_type` step 欄位（`agent` / `human` / `auto`；v0.2 只正式執行 `agent`）

說明：
- `assignee_type: auto` 在 v0.2 僅作為保留值存在，精確語意延後到 v0.3 前定義

### v0.2.x

目標：
補齊流程引擎周邊能力，讓 v0.3 可以專注在 HumanTask。

核心能力：
- custom hooks / scripts automation
- playbook / skill tooling
- validation / simulation / dry-run 強化
- 更多 custom playbook 驗證樣本

完成標準：
- `scripts/` 可透過 hooks 自動觸發
- `cafe skill` / `cafe playbook` tooling 足以支撐自訂流程開發
- 至少支援 `cafe playbook validate`
- 若範圍允許，支援 `cafe playbook simulate <playbook>` 走 transition graph 而不真的呼叫 agent
- 可用至少一條非軟體開發流程做 validate / dry-run 驗證

### v0.3

目標：
把 CAFE 從「流程引擎」推進到「人機協作流程系統」。

核心能力：
- 正式 `HumanTask` 模型
- human task completion payload schema
- inbox / pending task view
- reminders / due dates / SLA 基礎
- playbook step 可明確宣告 human-owned / agent-owned / hybrid

建議新增抽象：
- `HumanTask`
- `TaskResult`
- `WaitState`
- `Assignment`

完成標準：
- 一條流程中可同時混合 agent steps 與 human tasks
- human 完成任務後，流程可自動接續
- 不需要靠模糊的 `NEED_CLARIFICATION` 承載所有人工介入情境
- 可用招募 / onboarding / content 類流程驗證 human-agent collaboration

### v0.4

目標：
把 CAFE 從單一流程系統推進到可遞迴的流程網路。

核心能力：
- `Subflow` / playbook call
- 子流程輸入/輸出 contract
- 平行流程與聚合
- 流程模板化
- 跨流程共享 business objects

建議新增抽象：
- `WorkflowInstance`
- `PlaybookCallStep`
- `BusinessObject`
- `ObjectReference`

完成標準：
- 一個流程可啟動另一個流程
- 子流程結果可回傳父流程
- 可支援部門流程串接，例如產品 launch 觸發法務、行銷、銷售 enablement 子流程

待決策架構分叉：
- 路徑 A：Blackboard artifact 逐步演化成 Business Object store
- 路徑 B：Business Object 是獨立系統，Blackboard 只存 reference

目前傾向：
- **路徑 B**

原因：
- Blackboard 比較像 workflow instance state
- Business Object 的生命週期跨流程、跨 instance、跨時間
- 不應過早把 Blackboard 綁成 org-wide object store

### v0.5

目標：
把 CAFE 從流程網路推進到組織級 operating layer。

核心能力：
- org-wide memory
- policies / SOP / decision records
- cross-workflow analytics
- dashboard / reporting
- governance / versioning / migrations
- role capability matrix

建議新增抽象：
- `OrgMemory`
- `Policy`
- `Capability`
- `WorkflowMetrics`
- `VersionedPlaybook`

完成標準：
- 可從組織視角看流程健康度
- 可追蹤瓶頸、延遲、失敗率
- 可演化 playbook 而不破壞既有執行中的流程

## 這條路線裡最關鍵的 3 個架構跳躍

### 1. 從 Agent Workflow 到 Human-Agent Workflow

要補的不是更多 status code，而是正式支援：

- 誰該做這件事
- 這件事要交付什麼
- 什麼時候算完成
- 完成後怎麼回填流程

也就是 `HumanTask`。

### 2. 從 Workflow State 到 Business Objects

單純的 artifact/event/decision 還不夠支撐公司流程。  
你最終會需要跨流程共享的物件，例如：

- candidate
- customer
- deal
- contract
- campaign
- vendor
- budget

流程是圍繞 business object 運作，而不只是圍繞檔案。

### 3. 從 Single Flow 到 Recursive Flow Network

你說的「遞迴建立整個公司流程」本質上就是：

- 流程可以產生流程
- 流程可以調用流程
- 上層流程只看下層流程的 contract

沒有 `Subflow`，這件事做不到。

## 我對 v0.2 的建議範圍

### v0.2 必做

- 完成 `Skill + Playbook + Blackboard + Hook`
- 完成 GenericPhase 與 PlaybookRunner
- 完成 suspend / resume
- 明確定義 `WorkflowInstance`
- 驗證至少一條非軟體開發流程的 custom playbook dry-run

### v0.2 應預留介面

- `Subflow` step type
- role assignment abstraction
- object references
- wait state metadata
- human task event types
- `assignee_type`

### v0.2 不應硬做

- 正式 `HumanTask` 實作
- 完整 dashboard
- 完整 business object database
- 多人協作 UI
- org governance 平台

## 最小可行的公司流程能力

如果要判斷 CAFE 是否開始跨過「開發工具」邊界，我會看它能不能支援這三種流程：

1. 招募流程
   需要 agent 分析履歷，也需要人面試與回填判斷。

2. 客戶 onboarding 流程
   需要 agent 產文件，也需要人跟客戶同步資訊。

3. 內容 production 流程
   需要 agent 產草稿，也需要人拍攝、編修、發布。

只要這三類能跑，代表 CAFE 已經不再只是 dev workflow 工具。

## 目前我認為最值得優先驗證的問題

1. custom playbook 是否真的足以表達非軟體開發流程？
2. suspend / resume 是否足夠穩，能支撐跨天流程？
3. `HumanTask` 是否能自然嵌進 v0.3 的 playbook / blackboard 模型？
4. `Subflow` 未來是否能無痛接進現在的 step model？
5. Blackboard 未來是否應只作為 instance state，而不是 business object store？

## 建議的決策順序

1. 先確認 v0.2 不納入 `HumanTask` 正式實作，只預留入口
2. 再確認 v0.2 Milestone C 要加入非軟體開發流程 dry-run 驗證
3. 再確認 v0.3 是否聚焦在 HumanTask，而非與 custom hooks 綁在一起
4. 再確認 v0.4 的主軸是否就是 `Subflow + BusinessObject`
5. 最後才討論 dashboard / governance / org analytics

## 各版本的關鍵實驗

| 版本 | 關鍵實驗 |
|------|----------|
| v0.2 | custom playbook 能否表達非 default 的流程，至少可 dry-run 一條非軟體開發流程？ |
| v0.2.x | custom hooks / tooling 是否足以支撐使用者真正開始自訂 skill / playbook？ |
| v0.3 | HumanTask 能否自然嵌入流程，而不是退化成另一種 clarification？ |
| v0.4 | subflow 能否支撐流程串接，而不讓 step model 失控？ |
| v0.5 | org memory / governance / analytics 是否提供實際組織價值，而不是更多配置負擔？ |

## 驗證層級

為避免把 `dry-run` 混成單一概念，roadmap 採三層驗證能力：

- `writeable`
  - 能寫出 playbook YAML 與 skill 結構
- `validatable`
  - `cafe playbook validate` 能檢查 schema、skill existence、transition 合法性
- `simulatable`
  - `cafe playbook simulate <playbook>` 能用 mock executor 走 transition graph，而不真的呼叫 agent

版本預期：
- `v0.2` 至少保證 `writeable + validatable`
- `v0.2.x` 盡量補上 `simulatable`

補充：
- `simulate` 很有價值，但不應阻塞 v0.2 的核心重構
- 若 `simulate` 與核心 execution model 重寫互相競爭，優先保證 `validate`

## Workflow Instance

從 v0.2 開始，應明確把：

- `.cafe/issues/{issue}/`

視為一個 `WorkflowInstance`。

它至少包含：
- `playbook`：此 instance 使用哪個流程定義
- `blackboard.json`：此 instance 的共享狀態
- `iteration_*`：各 step 的歷史與產出

這個命名雖然不一定在 v0.2 就變成獨立 class，但文件上先定義好，能讓 v0.4 的 subflow 更自然落地。

## 向下相容原則

Roadmap 預設所有版本沿用以下原則：

- v0.2 的 playbook schema 到 v0.3+ 仍應可讀
- v0.2 的 SKILL.md 到 v0.3+ 仍應可讀
- `blackboard.json` 應加入 schema version，讓新版可讀舊版
- config 向下相容延續 v0.2 的設計

## 產品驗證軸

這份 roadmap 不只是一張架構演進圖，也對應一條產品驗證軸。  
每一版除了交付能力，還要回答一個產品問題：

- `v0.2`
  - 技術使用者是否能自己寫 custom playbook，且不只侷限於 default 開發流程
- `v0.2.x`
  - skill / playbook tooling 是否足以支撐真實的自訂流程迭代
- `v0.3`
  - 小團隊是否願意把人類任務正式掛進系統，而不是退回外部工具協作
- `v0.4`
  - subflow 是否真的讓複雜流程更清楚，而不是更難理解
- `v0.5`
  - governance / analytics / org memory 是否在不增加太多認知負擔的前提下提供價值

## Adoption 風險與產品現實

即使技術架構逐步到位，CAFE 作為「公司流程系統」仍有幾個非技術瓶頸需要持續考慮：

- **Authoring**
  - 誰來寫 playbook / skill？工程師以外的角色未必願意直接編輯 YAML
- **Migration**
  - 真實公司通常已經有 Notion、Jira、Linear、Spreadsheet 等既有流程，不能假設從零開始
- **Trust**
  - 開發流程較容易接受 agent；招募、客戶 onboarding、法務等流程的信任門檻更高

這些不一定在 v0.2 就解，但 roadmap 後段必須把 authoring、migration、trust-building 視為正式產品問題，而不只是實作細節。

## 結論

這條 roadmap 的核心不是把 CAFE 做得更「通用」，而是讓它從：

- 幫 agent 跑工作流

變成：

- 幫一間公司分配、追蹤、承接 agent 與 human 的工作

如果這是對的，那 v0.2 的成敗關鍵就不是只看 GenericPhase 漂不漂亮，而是看它有沒有把 `HumanTask`、`wait/resume`、以及未來 `Subflow` 的入口留出來。
