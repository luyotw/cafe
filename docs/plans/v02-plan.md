# CAFE v0.2: Skill-Driven Playbook + Blackboard 架構

本文件是 **v0.2 的 implementation plan / spec**。  
版本定位、產品邊界、以及 `v0.3+` 的長期演進方向以 [docs/roadmap.md](../roadmap.md) 為準；本文件只定義 `v0.2` 要做什麼、怎麼做、以及如何驗收。

版本名稱在本文件中沿用 roadmap 的開發週期語意：`0.N.0` 表示進入
`v0.N` 週期，該週期能力在 `0.N.x` 逐步交付，並在發布
`0.(N+1).0` 前滿足完成標準。因此下文標示 `v0.3` 的項目，是
`0.3.x` 開發週期的交付範圍。

> **備註：** 本檔為 v0.2 架構與驗收紀錄。內文主要 YAML 範例已對齊現行 **intent** 鍵（`on:` 轉移：`await_agent` / `confirm_output` / `need_clarification` / `need_permission` / `manual_handoff` / `no_changes_needed` 等）。下方 `CAFE_GOTO` 章節與「目前待執行項目」、「保留的相容層」段落仍提及 `CAFE_*` 字串，那是 v0.1 → v0.2 過渡的歷史對照，**不是現行操作介面**；請以 `src/cafe/data/playbooks/` 現行檔案為準。

## 核心需求

1. **Skill 是核心抽象** — 每個工作流步驟就是一個 Skill（遵循 agentskills.io 規範），包含 SKILL.md + scripts/ + references/ + assets/
2. **Phase 不特化** — 只有一個 GenericPhase 作為 Skill 執行引擎，透過 lifecycle hooks 實現可擴展行為
3. **Playbook = builtin + custom** — 編排器，透過 YAML 宣告 Skill 的執行順序和轉換邏輯
4. **Blackboard** — 共享狀態，解決跨 step 資訊斷層
5. **Baton + Receipt** — workflow 以 baton 決定下一棒，以 host-side capability receipt 驗證外部副作用是否真的完成
6. **WorkflowInstance** — `.cafe/issues/{issue}/` = 一個流程實例，為 v0.4 subflow 鋪路

## 為什麼 Skill 是核心

v0.1 的問題：每個 phase 的「知識」散落在三個地方 —— 特化 Phase class 的程式碼、checklist_templates.py 的字串、agent markdown。這導致：
- 新增一個 step 需要改好幾個檔案
- 特化邏輯寫死在 Python class 裡，使用者無法自訂
- Checklist 和 agent prompt 之間缺乏結構化的關聯

v0.2 的解法：**一個 Skill folder = 一個 step 的全部知識**。Skill 自帶指令（SKILL.md）、驗證腳本（scripts/）、參考資料（references/）。Playbook 只負責「誰先誰後」和「怎麼接」，不管每個 step 內部做什麼。

## 架構總覽

```
┌─────────────────────────────────────────────────────────────┐
│  Playbooks (builtin + custom)                                │
│  宣告 step 順序 + 轉換邏輯 + role 綁定                       │
├─────────────────────────────────────────────────────────────┤
│  BlackboardWorkflowRuntime — 編排器                           │
│  讀 playbook → 執行 step → 依 blackboard / baton / receipt    │
│  決定 pause、resume、transition、complete                    │
├─────────────────────────────────────────────────────────────┤
│  GenericPhase — Skill 執行引擎（lifecycle hooks pipeline）    │
│  載入 Skill → hooks → 執行 agent → hooks → 回傳結果          │
├──────────────┬──────────────────────────────────────────────┤
│              │  Blackboard — 共享狀態                        │
│  Skills      │  artifacts + events + decisions               │
│  (builtin    ├──────────────────────────────────────────────┤
│   + custom)  │  Agents + CLI Strategies（不動）              │
│              │                                               │
└──────────────┴──────────────────────────────────────────────┘
```

## Skill 規範（基於 agentskills.io）

### 目錄結構

```
skills/
  spec_first/
    SKILL.md              # 第一次迭代：收集需求
    references/
      spec_template.md
  spec_revise/
    SKILL.md              # 後續迭代：根據回饋修訂
  plan/
    SKILL.md
    references/
      plan_template_simple.md
      plan_template_default.md
      plan_template_bug.md
  develop/
    SKILL.md
    scripts/
      run_tests.sh        # agent 可手動執行；v0.3 開發週期支援自動觸發
  review/
    SKILL.md
    references/
      review_guidelines.md
  pr/
    SKILL.md
```

### SKILL.md 格式

```markdown
---
name: spec_first
description: "收集、釐清並文件化需求規格（首次迭代）"
version: 1.0.0
tags: [requirements, specification]
---

# Spec — 需求規格（首次迭代）

## Role
Read your agent file: {agent_file}

## Context
{blackboard_digest}

## Input
{input_artifacts_list}

## Instructions

你是 PM，負責釐清需求並產出 spec 文件。

- [ ] 閱讀 GitHub issue 或 user story
- [ ] 列出不明確的需求，產生 questions.xml
- [ ] 等待 user 回答後整合成 spec

## Output
Write spec to: {output_file}

## Status Code
{status_code_instruction}
```

> 註：這個區塊在 `v0.2` 後半段主要是給 legacy steps / 過渡相容用；blackboard-first steps 可以不再把 status code 當主要 completion signal。

### Frontmatter 欄位

| 欄位 | 必要 | 說明 |
|------|------|------|
| `name` | 是 | Skill 名稱，**必須與 folder name 一致**（Builtin 不一致 → error；Custom 不一致 → warning） |
| `description` | 是 | 一行描述，用於 progressive disclosure 的第一階段 |
| `version` | 否 | Skill 版本 |
| `tags` | 否 | 標籤，用於搜尋和分類 |

`allowed_tools`、`max_iterations` 等執行層面的設定放在 **Playbook** 而非 SKILL.md，因為同一個 Skill 在不同 Playbook 裡可能有不同的權限和限制。`valid_status_codes` 在 `v0.2` 後半段只視為 legacy 相容欄位；新 workflow 核心不應再依賴它作為主要 transition 依據。

### Progressive Disclosure（漸進式揭露）

為了控制 token 消耗，Skill 的載入分三階段：

1. **Startup** — 只載入 name + description（從 frontmatter），用於 Playbook 驗證和 `cafe skill list`
2. **Installation** — 在執行前把解析後的 Skill 同步到目前 agent CLI 的 native skills directory
3. **Invocation / On-demand** — agent 透過 CLI-native skill invocation 使用 skill，並自行透過 tool 讀取 references/ 和 scripts/

```python
class SkillLoader:
    def discover() -> List[SkillCatalogEntry]        # 階段 1: 掃描 frontmatter
    def get_skill_dir(name: str) -> Path             # 取得已解析來源 skill 目錄
    def get_reference(name: str, ref: str) -> str    # 讀指定 reference 檔案
```

### scripts/ 的使用層級

| 層級 | v0.2 | v0.3 開發週期（`0.3.x`） |
|------|------|------|
| Agent 手動執行 | ✅ agent 透過 bash 工具跑 `{skill_dir}/scripts/validate.sh` | ✅ |
| 系統自動執行（hooks） | ❌ | ✅ playbook `hooks:` 欄位觸發 |

v0.2 在 `cafe skill list` 標示 `scripts: available (manual)` 或 `scripts: none`。

### Builtin vs Custom

| 類型 | builtin | custom (project) | custom (global) |
|------|---------|-------------------|-----------------|
| Skills | `src/cafe/data/skills/` | `.cafe/skills/` | `~/.cafe/skills/` |
| Playbooks | `src/cafe/data/playbooks/` | `.cafe/playbooks/` | `~/.cafe/playbooks/` |
| Agents | `src/cafe/data/agents/` | `.cafe/agents/` | `~/.cafe/agents/` |
| Templates | `src/cafe/data/templates/` | `.cafe/templates/` | `~/.cafe/templates/` |

查找順序：project custom → global custom → builtin。Custom 同名覆蓋 builtin。

Skill override 規則：
- **folder name = lookup key**（playbook 的 `skill:` 欄位用 folder name）
- frontmatter `name` 必須與 folder name 一致：
  - **Builtin skill**：不一致 → **error**（repo 品質要求）
  - **Custom skill**：不一致 → **warning**（不阻擋試驗）
  - `cafe skill validate --strict`：warning 升級為 error
- `.cafe/skills/spec_first/SKILL.md` 會覆蓋 builtin 的 `spec_first` skill

## Playbook — 編排器

### Skill 與 Playbook 的分工

```
┌─────────────────────────────────────────────────────────┐
│                  Skill (SKILL.md)                         │
│  「做什麼」+「怎麼做」                                    │
│  ─ agent 的完整指令（checklist）                          │
│  ─ 參考資料 (references/)                                │
│  ─ 驗證腳本 (scripts/)                                   │
│  ─ placeholder 支援                                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                  Playbook (YAML)                          │
│  「誰做」+「什麼順序」+「怎麼接」                         │
│  ─ role 分配                                             │
│  ─ step 順序和轉換邏輯                                   │
│  ─ 工具權限                                              │
│  ─ 迭代限制                                              │
│  ─ Blackboard I/O 宣告                                   │
│  ─ hooks 開關                                            │
└─────────────────────────────────────────────────────────┘
```

同一個 Skill 可被不同 Playbook 複用：
- default playbook 的 `review` step 用 `review` skill，role = reviewer，max_iterations = 5
- hotfix playbook 的 `quick_review` step 也用 `review` skill，但 max_iterations = 1

### Schema（完整版）

```yaml
playbook:
  id: default
  name: "Standard Development Workflow"

roles:
  pm:
    description: "Product Manager"
    default_agent: "Roger"       # fallback，可被 config 覆蓋
    default_cli: "gemini"
  developer:
    description: "Developer"
    default_agent: "David"
    default_cli: "claude"
  reviewer:
    description: "Code Reviewer"
    default_agent: "Richard"
    default_cli: "gemini"

steps:
  spec:
    type: skill                  # 預設值，可省略；v0.4 開發週期支援 subflow
    skill:
      "1": spec_first            # 第一次迭代用 spec_first skill
      default: spec_revise       # 後續用 spec_revise skill
    role: pm
    assignee_type: agent         # v0.2 預設值；v0.3 開發週期支援 human / auto
    output_artifact: spec
    allowed_tools: [Read, Grep, Glob, WebFetch, WebSearch]
    hooks:
      prepare_input: [GitHubIssueFetcher, UserInputCollector]
      after_execute: [InteractiveQAHandler]
    "on":
      await_agent: plan
      confirm_output: spec
      need_clarification: spec
      need_permission: spec

  plan:
    skill: plan
    role: developer
    input_artifacts: [spec]
    output_artifact: plan
    allowed_tools: [Read, Grep, Glob, WebFetch, WebSearch]
    "on":
      await_agent: develop
      confirm_output: plan
      need_clarification: plan
      need_permission: plan

  develop:
    skill: develop
    role: developer
    input_artifacts: [spec, plan]
    output_artifact: code
    allowed_tools: [Read, Edit, Write, Grep, Glob, Bash, WebFetch, WebSearch]
    hooks:
      after_execute: [NoChangesNeededHandler, PermissionRetryHandler]
    "on":
      await_agent: review
      manual_handoff: pr
      need_clarification: develop
      need_permission: develop
      no_changes_needed: review

  review:
    skill: review
    role: reviewer
    input_artifacts: [spec, plan, code]
    output_artifact: review_feedback
    allowed_tools: [Read, Grep, Glob, "Bash(git:*)"]
    max_iterations: 5
    allowed_goto: [spec, develop, plan]
    hooks:
      before_execute: [NewChangesGate]
    "on":
      await_agent: pr
      manual_handoff: develop
      need_clarification: review

  pr:
    skill: pr
    role: developer
    input_artifacts: [spec, code, review_feedback]
    output_artifact: pr_result
    allowed_tools: [Read, Edit, Write, Grep, Glob, Bash, WebFetch, WebSearch]
    allowed_goto: [develop, review]
    hooks:
      publish_output: [GitHubPRCreator, PRCommentPoster]
    "on":
      await_agent: _done
      manual_handoff: develop
      need_permission: pr

entry_point: spec
```

### Step 欄位說明

| 欄位 | 必要 | 說明 |
|------|------|------|
| `type` | 否 | `skill`（預設，可省略）/ `subflow`（v0.2 僅 parse + validate，不執行） |
| `skill` | 是 | Skill folder 名稱（string），或 iteration-aware mapping（`{1: "skill_a", default: "skill_b"}`） |
| `role` | 是 | 執行此 step 的角色 |
| `assignee_type` | 否 | `agent`（預設）/ `human` / `auto`。v0.2 只實作 `agent`，其餘 parse + validate 但不執行（見預留入口章節） |
| `input_artifacts` | 否 | 從 Blackboard 讀取的 artifact 名稱列表 |
| `output_artifact` | 否 | 此 step 產出的 artifact 名稱 |
| `allowed_tools` | 否 | Agent 工具白名單（沿用 PermissionHandler 的 pattern grammar） |
| `valid_status_codes` | 否 | **Legacy / mock executor 限定**。intent-driven runtime 不再依賴此欄位 |
| `max_iterations` | 否 | 最大迭代次數（預設不限） |
| `allowed_goto` | 否 | goto baton 可跳轉的目標 step |
| `hooks` | 否 | Lifecycle hook 掛載（見 GenericPhase 章節） |
| `auto_snapshot` | 否 | 僅對產出 `WORKSPACE` artifact 的 step 生效。預設 `true`，設 `false` 則 dirty workspace 直接報錯 |
| `on` | 是 | intent key → next step 的轉換表（`await_agent` / `confirm_output` / `need_clarification` / `need_permission` / `manual_handoff` / `no_changes_needed` 等） |

### Role 與 Config 的優先順序

```
issue config > project config > playbook roles default > 報錯
```

- **Playbook `roles`** 定義「workflow 需要哪些角色」+ 預設 agent/cli（fallback）
- **Config `agents`** 是使用者的實際綁定（override）
- 如果 playbook 宣告 `security_engineer` 但 config 和 playbook default 都沒有 → `cafe make` 啟動時報錯，提示用戶 `cafe setup`
- Playbook `roles` 的 `default_agent`/`default_cli` 可省略 = 此角色必須由 config 提供

### Validation Policy

為了兼顧 repo 品質與使用者試驗性，validation 分層如下：

- **Builtin skill / playbook**
  - 結構或命名不一致 → **error**
  - 目標是保證 repo 內建資料永遠維持高一致性

- **Custom skill / playbook**
  - 非致命不一致 → **warning**
  - 目標是不阻擋使用者快速試驗與迭代

- **Strict mode**
  - `cafe skill validate --strict`
  - `cafe playbook validate --strict`
  - 會把 warning 升級為 error

這個規則適用於：
- skill frontmatter `name` 與 folder name 不一致
- playbook 的冗餘設定（如 `Bash` 與 `Bash(git diff)` 同時存在）
- 其他不影響基本執行、但會降低可維護性的設計問題

### allowed_tools Grammar

沿用現有 `PermissionHandler`（`permission.py`）的 pattern matching：

```
TOOL_PATTERN    = TOOL_NAME [ "(" COMMAND_PATTERN ")" ]
TOOL_NAME       = 字串（Read, Bash, Edit, Grep, Glob, Write, WebFetch, WebSearch）
COMMAND_PATTERN = 字串，支援 * 和 : wildcard
```

範例：
- `Read` — 所有讀取操作
- `Bash` — 所有 bash 命令
- `"Bash(git:*)"` — 所有 git 開頭的 bash 命令
- `"Bash(git status)"` — 只允許 git status

Enforcement：
- **Prompt**: 注入 SKILL.md 的 `{allowed_tools_instruction}`
- **Runtime**: 傳給 `PermissionHandler`，不在白名單的操作觸發 permission request
- `Bash` 和 `"Bash(git diff)"` 不應同時存在（`Bash` 已包含全部），Playbook validator warn

## GenericPhase — Skill 執行引擎

### Lifecycle Hooks

GenericPhase 定義固定的 hook 點，features 改為 hook 實作：

```
before_execute → prepare_input → execute_agent → after_execute → publish_output
```

| Hook 點 | 時機 | Builtin Hook 實作 |
|---|---|---|
| `before_execute` | Agent 執行前，前置檢查 | `NewChangesGate` — 檢查 code artifact 是否有新版本 |
| `prepare_input` | 準備 agent 的輸入 | `GitHubIssueFetcher` — 從 GitHub issue 取得初始輸入 |
| `prepare_input` | 準備 agent 的輸入 | `UserInputCollector` — 收集使用者輸入 |
| `after_execute` | Agent 執行後，處理結果 | `InteractiveQAHandler` — 解析 questions.xml、啟動互動 UI |
| `after_execute` | Agent 執行後，處理結果 | `PermissionRetryHandler` — 工具權限被拒時 retry |
| `publish_output` | 產出物發佈 | `GitHubPRCreator` — 建立/更新 GitHub PR |
| `publish_output` | 產出物發佈 | `PRCommentPoster` — 發佈 PR comment |

**判準**：影響 prompt 內容 → 放在 SKILL.md。影響執行流程（中斷等人、retry、呼叫外部 API）→ 必須是 hook 實作。

**v0.2.x 擴展**：使用者可在 Skill 的 `scripts/` 定義 custom hook（shell script），透過 playbook 的 `hooks:` 欄位掛載。

#### Script Hook Contract (v0.2.x)

v0.2.x 支援在 `before_execute` / `after_execute` 直接宣告 script hook（與字串型 builtin hook 並存）：

```yaml
hooks:
  after_execute:
    - script: sync_github.sh
      when_intents: [confirmed]
      args:
        phase: plan
        output: "{output_file}"
      schema:
        type: object
        required: [phase, output]
        additionalProperties: false
        properties:
          phase:
            type: string
            enum: [spec, plan]
          output:
            type: string
```

執行規則：
- `script` 必須是 skill `scripts/` 目錄下的相對路徑；禁止 absolute path、`..` traversal、或跳出 skill 目錄。
- `args` 會轉為 CLI 參數（`--key value`），並支援 placeholder interpolation（如 `{output_file}`）。
- `schema` 採最小 JSON-schema 子集：`type=object`、`required`、`properties`、`additionalProperties=false`、primitive type（string/boolean/integer/number）、`enum`。
- `timeout_seconds`（可選）可設定 script subprocess timeout；逾時會中止 pipeline 並回報 `script_hook` timeout 事件。
- `when_status_codes`（可選）只在 `after_execute` 使用，狀態不匹配時跳過執行。
- script 只允許掛在 `before_execute` / `after_execute`；若在 `prepare_input` 或 `publish_output` 宣告 script dict，playbook 載入時即報錯。
- script 以 sandbox 內 subprocess 執行；非 0 exit code、schema 驗證失敗、或 timeout 皆會中止 pipeline，並回報可觀測事件。
- `schema` 驗證失敗與 script 非 0 / timeout 目前都映射為 `need_permission` intent，表示需要 host/operator 介入處理。

blackboard 事件 payload（`event_type=script_hook`）：
- `type`, `step`, `skill`, `stage`, `script`, `status`
- `exit_code`, `stdout`, `stderr`
- `validation_errors`

### Hook Result Contract

每個 hook 回傳統一的 `HookResult`，GenericPhase 根據結果決定後續行為：

```python
@dataclass
class HookResult:
    continue_pipeline: bool = True       # False → 中止 pipeline，phase 回傳當前狀態
    retry_requested: bool = False        # True → 重新執行 agent（PermissionRetryHandler 用）
    artifact_ready: bool = True          # False → 這輪不 publish output artifact
    override_status_code: Optional[PhaseStatusCode] = None  # 覆寫 agent 回傳的 status code
    context_updates: Optional[dict] = None   # 注入下一輪 prompt context（key-value）
    events: List[EventEntry] = field(default_factory=list)   # hook 產生的 events
```

各 builtin hook 的使用方式：

| Hook | continue | retry | artifact_ready | override_status | context_updates |
|---|---|---|---|---|---|
| `NewChangesGate` | False（無新變更時） | - | - | - | - |
| `GitHubIssueFetcher` | True | - | - | - | `{github_issue: "..."}` |
| `UserInputCollector` | True | - | - | - | `{user_input: "..."}` |
| `InteractiveQAHandler` | True | - | **False** | - | `{qa_answers: "..."}` |
| `PermissionRetryHandler` | True | **True** | - | - | - |
| `GitHubPRCreator` | True | - | True | - | `{pr_url: "..."}` |
| `PRCommentPoster` | True | - | True | - | - |

**publish_output hook 失敗處理**：
- Agent 的 output.md 已寫出 = phase core output 成功
- External side effect（PR 建立、comment posting）失敗 → artifact **仍然 publish**
- Hook 記 warning event：`{type: "external_sync_failed", message: "GitHub PR creation failed: 403"}`
- PhaseResult 加 warnings，但不標為 failed
- 重跑時 hook 自己處理 idempotency（先查 PR 是否已存在）

### 執行流程

```
 1. 從 playbook step config 取得 skill 名稱（根據 iteration 選擇）
 2. 將 skill 安裝到當前 agent CLI 的 native skills directory，取得 invocation（例如 `$cafe-plan`）
 3. 執行 before_execute hooks — 任一回傳 continue=False 則中止
 4. 執行 prepare_input hooks — 收集最小 runtime context
 5. 組合極薄的 runtime prompt，主要只告知這輪要使用哪個已安裝 skill，以及 output/checklist/questions 檔案位置
 8. 呼叫 _execute_agent_iteration()（現有 Phase base class 基礎設施）
 9. 執行 after_execute hooks — 若 retry_requested 則回到步驟 8
10. 存 output 到 iteration_XXX/output.md
11. 判斷 artifact_ready（見下方規則）
12. 執行 publish_output hooks（如 GitHubPRCreator, PRCommentPoster）
13. 若 artifact_ready → 在 Blackboard 註冊 output artifact + 寫 iteration_N/artifact.json
14. 寫入所有 hook 產生的 events 到 Blackboard
15. 回傳 PhaseResult（含 status_code, warnings）
```

### 現有特化邏輯的去向

| 現有特化邏輯 | v0.2 去向 |
|---|---|
| Spec: interactive Q&A | hook `InteractiveQAHandler` + spec skill 的 SKILL.md 指令 |
| Spec: GitHub issue fetch | hook `GitHubIssueFetcher` |
| Spec: rigor level | SKILL.md placeholder `{rigor_level}` |
| Spec: user story input | hook `UserInputCollector` |
| Plan: template selection | plan skill 的 references/ 裡放模板，SKILL.md 指令引導選擇 |
| Develop: tool permission | hook `PermissionRetryHandler` |
| Develop: review feedback loop | playbook `on: manual_handoff: develop` |
| Review: check new commits | hook `NewChangesGate`（改用 Blackboard artifact version 判斷） |
| Review: single iteration | playbook `max_iterations: 1` |
| PR: GitHub PR creation | hook `GitHubPRCreator` |
| PR: post todo list | hook `PRCommentPoster` |

### Iteration-aware Skills

Playbook 的 `skill` 欄位支援 union type：

```yaml
# 簡單模式：所有 iteration 用同一個 skill
skill: review

# Iteration-aware：不同 iteration 用不同 skill
skill:
  1: spec_first          # 第一次迭代
  default: spec_revise   # 後續迭代
```

Pydantic 型別：`Union[str, Dict[Union[int, Literal["default"]], str]]`

Validator 規則：
- mapping 模式必須有 `default` key
- 所有引用的 skill name 必須存在

## Blackboard

### 結構

```python
class ArtifactKind(str, Enum):
    DOCUMENT = "document"     # 單一檔案（spec, plan, review_feedback）
    WORKSPACE = "workspace"   # git 狀態（code）
    METADATA = "metadata"     # 結構化資料（pr_result）

@dataclass
class ArtifactEntry:
    name: str
    kind: ArtifactKind
    version: int               # = phase iteration number（直接對齊）
    updated_by: str
    updated_at: str
    summary: Optional[str]
    # kind-specific
    path: Optional[str]        # DOCUMENT: 檔案路徑
    base_sha: Optional[str]    # WORKSPACE: base commit
    head_sha: Optional[str]    # WORKSPACE: head commit
    data: Optional[dict]       # METADATA: 結構化資料

@dataclass
class EventEntry:
    timestamp: str
    step: str
    event_type: str            # artifact_updated, decision, goto, error,
                               # human_task_created, human_task_completed（v0.3 開發週期）
    message: str
    data: Optional[dict]

@dataclass
class DecisionEntry:
    timestamp: str
    step: str
    decision: str
    rationale: str
    made_by: str               # step name 或 "human"

@dataclass
class Blackboard:
    schema_version: int                   # schema 版本（v0.2 = 1），向下相容用
    artifacts: Dict[str, ArtifactEntry]
    events: List[EventEntry]
    decisions: List[DecisionEntry]
```

### Artifact 清單（default playbook）

| Artifact | Kind | 產生者 | 消費者 |
|---|---|---|---|
| `spec` | document | spec step | plan, develop, review, pr |
| `plan` | document | plan step | develop, review |
| `code` | workspace | develop step | review, pr |
| `review_feedback` | document | review step | develop |
| `pr_result` | metadata | pr step | develop（PR 後修正） |
| `pr_comments` | document | pr step（fetch） | develop（PR 後修正） |

### Workspace Artifact（code）

`code` 不是單一檔案，而是 git 狀態：

```python
ArtifactEntry(
    name="code",
    kind=ArtifactKind.WORKSPACE,
    version=3,
    path="iteration_003/output.md",    # developer 的工作報告
    base_sha="abc1234",                # develop branch 的 base commit
    head_sha="def5678",                # develop 完成後的 HEAD
)
```

下游 step（review, pr）透過 `git diff {base_sha}...{head_sha}` 拿 diff。

**Dirty workspace 處理**：

優先要求 clean workspace，必要時可建立 internal snapshot commit（受 guardrails 限制的 fallback，非預設常態）。

1. GenericPhase 在 publish 前檢查 working tree 是否 clean
2. 如果 clean → 用當前 HEAD 作為 `head_sha`
3. 如果 dirty 且 `auto_snapshot: true`（預設）→ 嘗試建立 snapshot commit
4. 用 snapshot commit 的 SHA 作為 `head_sha`
5. 記 Blackboard event：`{type: "workspace_snapshot", message: "auto-committed dirty workspace"}`

**Snapshot commit guardrails**：
- **Commit message 可辨識**：固定格式 `cafe: workspace snapshot (iteration N)`
- **只在本地 branch，不 push remote**
- **Pre-commit hook 失敗**：不靜默忽略，明確報錯 `"Cannot create workspace snapshot: pre-commit hook failed. Please commit manually."`，phase 不 publish workspace artifact
- **可關閉**：playbook step 可設 `auto_snapshot: false`，dirty workspace 直接報錯不嘗試 auto commit

### Artifact Publish 規則

「iteration 正常結束」和「artifact 可 publish」是**兩個不同概念**：

- **iteration 正常結束** = step 寫出了可接受的 baton / terminal intent，且沒有 crash/timeout
- **artifact_ready** = 這輪產出了值得 publish 的 output

| Baton / 結果類型 | iteration 正常結束 | artifact_ready |
|---|---|---|
| `workflow_complete` / `done` | ✅ | ✅ |
| `review_requested` / 可供下一步消費的產出 | ✅ | ✅ |
| `clarification_needed` / `user` | ✅ | **❌**（只是吐 questions，還沒最終產出） |
| `permission_needed` / `user` | ✅ | ❌ |
| 失敗 / 中斷 / 無 transition | ❌ | ❌ |

Hook 可以 override `artifact_ready`（透過 HookResult）。

Publish 時同步寫 `iteration_N/artifact.json`（供 rebuild 用）：
```json
{
  "name": "spec",
  "kind": "document",
  "version": 3,
  "updated_by": "spec",
  "updated_at": "2026-04-05T10:30:00+08:00",
  "path": "iteration_003/output.md",
  "summary": "auth 改為 JWT"
}
```

讀取 `input_artifacts` 永遠拿 Blackboard 上的最新版本（= 最後一次 artifact_ready 的）。

### Version 語意

**Artifact version = phase iteration number，直接對齊。**

- 維持 v0.1 的目錄結構（`spec/iteration_003/output.md`）
- 每次 step 成功完成一個 iteration → artifact version = iteration number
- 失敗 iteration 不計入 artifact version，但 iteration counter 仍推進
- `iteration_*` 目錄是檔案的真實位置，`blackboard.json` 是指標 + 元資料
- `blackboard.json` 損壞時可從 `iteration_N/artifact.json` 重建（所有 kind 均可重建）

### Blackboard Digest

注入 agent prompt 的 `{blackboard_digest}`：

```markdown
## Blackboard

### Artifacts
| Name | Kind | Ver | Updated By | When |
|------|------|-----|-----------|------|
| spec | document | v3 | spec (iteration 3) | 10:30 |
| plan | document | v1 | plan (iteration 1) | 10:15 |
| code | workspace | v2 | develop (iteration 5) | 10:45 |

### Recent Events (since your last run, max 20)
- [10:45] develop: "code" updated (v1→v2) — "實作 auth JWT"
- [10:40] human: decision — "改用 JWT"
- [10:30] spec: "spec" updated (v2→v3) — "auth 改為 JWT"

### Input Files
- spec: .cafe/issues/issue42/spec/iteration_003/output.md
- plan: .cafe/issues/issue42/plan/iteration_001/output.md
- code: git diff abc1234...def5678
```

`generate_digest()` 的 events 上限預設 20 條，避免 token 膨脹。

### API

```python
class Blackboard:
    def get_artifact(self, name: str) -> Optional[ArtifactEntry]
    def put_artifact(self, entry: ArtifactEntry) -> None
    def list_artifacts(self) -> Dict[str, ArtifactEntry]
    def log_event(self, step: str, event_type: str, message: str, data: dict = {}) -> None
    def get_events_since(self, timestamp: str) -> List[EventEntry]
    def record_decision(self, step: str, decision: str, rationale: str, made_by: str) -> None
    def generate_digest(self, for_step: str, since: Optional[str] = None, max_events: int = 20) -> str
    def rebuild_from_iterations(self, issue_dir: Path) -> None  # 從 iteration_N/artifact.json 重建
```

持久化：`.cafe/issues/{issue}/blackboard.json`

## WorkflowInstance

從 v0.2 開始，`.cafe/issues/{issue}/` 正式定義為一個 **WorkflowInstance**：

```
.cafe/issues/{issue}/
  blackboard.json              # 此 instance 的共享狀態（含 schema_version）
  config.yaml                  # 此 instance 的設定覆蓋
  spec/iteration_001/          # 各 step 的歷史與產出
  plan/iteration_001/
  develop/iteration_001/
  ...
```

語意：
- 一個 issue = 一個 workflow instance
- `blackboard.json` = 這個 instance 的狀態
- instance 使用的 playbook 記錄在 config 或 blackboard 中
- v0.2 不需要 `WorkflowInstance` class，但文件上先定義好，讓 v0.4 開發週期的 subflow（「一個 instance 產生子 instance」）能自然落地

Runtime store 立場：
- v0.2~v0.3 以 file-based（`.cafe/issues/` + JSON/YAML/MD）為主
- v0.4 開發週期再評估是否抽離到 structured store（SQLite 或 remote）

## CAFE_GOTO

> Legacy note: `CAFE_GOTO` 與 status code 是舊 transition 模型的產物。`v0.2` 後半段的主路徑應以 blackboard baton 為準；這一節保留的是遷移期相容語意，而不是新核心的主要設計。

### 解析規則

Status code 和 CAFE_GOTO **獨立解析**：

```
CAFE_NEEDS_CHANGES          ← status_code: 這個 step 的結果
CAFE_GOTO:spec              ← goto: 下一步去哪

Spec 對 auth 需求有根本性誤解。
```

- status_code 決定「結果」
- CAFE_GOTO 決定「下一步」
- 例：`CAFE_NEEDS_CHANGES` + `CAFE_GOTO:spec` = review 結果是需要改，但指定回 spec（而非預設的 develop）

### 優先序

```
有 CAFE_GOTO 且在 allowed_goto 內 → 用 GOTO 目標
有 CAFE_GOTO 但不在 allowed_goto 內 → 忽略 GOTO，fallback 到 on[status_code]
無 CAFE_GOTO → 用 on[status_code]
```

GOTO 目標不合法時**不 fail fast**（agent 可能犯錯），而是安靜 fallback 並記錄 warning event。

### 安全限制

- `allowed_goto` 的目標必須存在於 playbook `steps` 中 → Playbook validator 在載入時檢查，不合法直接 validation error
- 每次 GOTO 記錄 Blackboard event：`{type: "goto", from: "review", to: "spec", reason: "..."}`
- **Max hop limit**：Runner 維護 hop counter，預設上限 20，超過停止報錯
- **Loop detection**：檢查最近 N 次 hop 是否形成循環（A→B→A→B），連續 3 次相同循環即停止

## SKILL.md Placeholder

### Eager（直接注入 prompt）

| Placeholder | 說明 |
|---|---|
| `{agent_file}` | Agent markdown 檔路徑 |
| `{blackboard_digest}` | Blackboard 摘要（精簡版，events 上限 20） |
| `{input_artifacts_list}` | 所有 input artifact 的路徑列表 |
| `{artifact:NAME}` | 指定 artifact 的檔案路徑 |
| `{output_file}` | Output 檔案路徑 |
| `{status_code_instruction}` | Valid status codes 說明 |
| `{allowed_tools_instruction}` | 可用工具說明 |
| `{base_branch}` | Base branch 名稱 |
| `{issue_name}` | Issue 名稱 |
| `{iteration}` | 目前迭代次數 |
| `{skill_dir}` | 此 Skill 的目錄路徑 |
| `{skill_references}` | references/ 目錄下的檔案清單（路徑，非內容） |

### Lazy（給路徑或指令，agent 自行讀取）

| Placeholder | 說明 |
|---|---|
| `{git_diff_command}` | `git diff {base_sha}...{head_sha}`，agent 自己執行 |
| `{prev_output_file}` | 上一次迭代 output 的檔案路徑 |

## Config 向下相容

```yaml
# v0.1 格式（依然可用 — playbook 預設 "default"）
agents:
  pm: {name: "Roger", cli: "gemini"}
  developer: {name: "David", cli: "claude"}
  reviewer: {name: "Richard", cli: "gemini"}

# v0.2 格式
playbook: secure_workflow
agents:
  pm: {name: "Roger", cli: "gemini"}
  developer: {name: "David", cli: "claude"}
  reviewer: {name: "Richard", cli: "gemini"}
  security_engineer: {name: "Sara", cli: "claude"}
```

## 自訂範例：加入 Security Audit Step

### 1. 建立 Skill

`.cafe/skills/security_audit/SKILL.md`:
```markdown
---
name: security_audit
description: "OWASP Top 10 安全審計"
version: 1.0.0
tags: [security, audit, owasp]
---

# Security Audit

## Role
Read your agent file: {agent_file}

## Context
{blackboard_digest}

## Input
{input_artifacts_list}

## Instructions

你是資安工程師，負責安全審計。

- [ ] 閱讀 spec 了解功能需求
- [ ] 閱讀 {skill_dir}/references/owasp_top10.md 了解檢查項目
- [ ] 執行 {git_diff_command} 檢視所有變更
- [ ] 逐一檢查 OWASP Top 10
- [ ] 將發現寫入 {output_file}

## Output
Write audit result to: {output_file}

## Status Code
{status_code_instruction}
```

### 2. 建立 Playbook

`.cafe/playbooks/secure_workflow.yaml`:
```yaml
playbook:
  id: secure_workflow
  name: "Secure Development Workflow"

roles:
  pm: {description: "Product Manager"}
  developer: {description: "Developer"}
  reviewer: {description: "Code Reviewer"}
  security_engineer: {description: "Security Engineer"}

steps:
  spec:
    skill: {1: spec_first, default: spec_revise}
    role: pm
    # ... 同 default

  develop:
    skill: develop
    role: developer
    "on":
      await_agent: security_audit       # 先做安全審計

  security_audit:
    skill: security_audit               # 自訂 skill
    role: security_engineer
    assignee_type: agent
    input_artifacts: [spec, plan, code]
    output_artifact: security_report
    allowed_tools: [Read, Grep, Glob, "Bash(git:*)"]
    "on":
      await_agent: review
      manual_handoff: develop

  review:
    skill: review
    role: reviewer
    input_artifacts: [spec, plan, code, security_report]  # 也看安全報告
    # ...

entry_point: spec
```

結果：`cafe make` 跑 spec → plan → develop → **security_audit** → review → pr。不需要改任何 Python 程式碼。

## interactive_qa 生命週期

`InteractiveQAHandler`（after_execute hook）的完整流程：

```
1. Agent iteration N 回傳 `need_clarification` intent + questions.xml
2. Hook 解析 questions.xml，存到 iteration_N/questions.xml（不進 Blackboard）
3. Hook 回傳 HookResult(artifact_ready=False, context_updates={qa_answers: ...})
4. Hook 記 event: {type: "interactive_qa_requested", message: "3 questions generated"}
5. 啟動互動 UI，user 填答案
6. 答案存到 iteration_N/answers.yaml
7. Hook 記 event: {type: "interactive_qa_answered", message: "3 answers submitted"}
8. 開新 iteration N+1，答案注入 {qa_answers} placeholder
9. Agent 基於答案修訂 output
```

`questions.xml` 是 step 內部暫存，不是 Blackboard artifact。但 events 會出現在 Blackboard digest 和 timeline 中，確保可觀測性。

## PR Step 語意與後修正流程

`pr` step 是**長期存在的 step** — 不是一次性的發佈動作：

- 首次執行：建立 PR
- 後續迴圈（`manual_handoff` → develop → review → pr）：更新 PR、refresh comments
- `await_agent: _done` baton 後才真正結束

Artifact 分工：
- `pr_result` (metadata) = PR 本身的狀態（number, url, state），每次 pr step 執行時更新
- `pr_comments` (document) = 從 GitHub PR 平台 fetch 的外部回饋快照，每次 pr step 執行時 refresh

```
1. PR step 建立 PR → publish pr_result (metadata: {pr_number, url})
2. PR 收到 review comments → pr step fetch → publish pr_comments (document)
3. on: manual_handoff: develop
4. Develop 讀 input_artifacts: [spec, plan, review_feedback, pr_comments]
5. Blackboard digest 顯示 "pr_comments v2 updated since your last run"
6. Developer 不會漏看任何 feedback 來源
```

## 新增模組

| 模組 | 用途 |
|------|------|
| `src/cafe/core/blackboard.py` | Blackboard 資料模型（ArtifactKind, ArtifactEntry, EventEntry, DecisionEntry）+ JSON 持久化 + rebuild |
| `src/cafe/core/playbook.py` | Playbook schema（Pydantic models）、YAML 載入器、驗證器（skill 存在性、allowed_goto 合法性、tool pattern 冗餘） |
| `src/cafe/skills/loader.py` | Skill 發現（掃描 builtin + custom）、frontmatter 解析、name/folder 一致性檢查 |
| `src/cafe/skills/native_bridge.py` | 將 CAFE skill 安裝到 repo-local CLI-native skills 目錄並回傳 invocation |
| `src/cafe/phases/generic_phase.py` | GenericPhase — lifecycle hooks pipeline + Skill 執行 |
| `src/cafe/core/hooks/` | Builtin hook 實作（GitHubIssueFetcher, UserInputCollector, InteractiveQAHandler, PermissionRetryHandler, NewChangesGate, GitHubPRCreator, PRCommentPoster） |
| `src/cafe/core/playbook_runner.py` | Legacy runner 過渡層；在新 runtime 接手前保留既有流程與測試支撐 |
| `src/cafe/data/skills/` | Builtin Skills（spec_first, spec_revise, plan, develop, review, pr） |
| `src/cafe/data/playbooks/` | Builtin Playbooks（default, hotfix） |

## 現有檔案修改

### 刪除
- `src/cafe/phases/spec_phase.py` — 邏輯移入 spec skill 的 SKILL.md + hooks
- `src/cafe/phases/plan_phase.py` — 同上
- `src/cafe/phases/develop_phase.py` — 同上
- `src/cafe/phases/review_phase.py` — 同上
- `src/cafe/phases/pr_phase.py` — 同上
- `src/cafe/utils/checklist_templates.py` — 內容拆入各 Skill 的 SKILL.md
- `src/cafe/utils/checklist_generator.py` — 由 skill_loader.py 取代

### 修改
- `src/cafe/core/phase.py` — 加入 Blackboard 屬性和 hook pipeline 基礎設施
- `src/cafe/ui/cli.py` — `make()` 委派 BlackboardWorkflowRuntime；動態 phases/roles
- `src/cafe/core/status_codes.py` — 僅保留 legacy status code / goto 相容邏輯，逐步退出主流程
- `src/cafe/utils/config.py` — 加入 playbook key；動態 roles
- `src/cafe/services/timeline_builder.py` — 動態 phase 列表

### 遷移對照（知識搬家）

| 現有位置 | 遷移目標 |
|---|---|
| `checklist_templates.py` → spec iteration 1 字串 | `data/skills/spec_first/SKILL.md` body |
| `checklist_templates.py` → spec iteration N 字串 | `data/skills/spec_revise/SKILL.md` body |
| `checklist_templates.py` → plan 字串 | `data/skills/plan/SKILL.md` body |
| `checklist_templates.py` → develop 字串 | `data/skills/develop/SKILL.md` body |
| `checklist_templates.py` → review 字串 | `data/skills/review/SKILL.md` body |
| `checklist_templates.py` → pr 字串 | `data/skills/pr/SKILL.md` body |
| `data/templates/spec/*.md` | `data/skills/spec_first/references/` |
| `data/templates/plan/*.md` | `data/skills/plan/references/` |

## 遷移計畫（3 Milestones）

### Milestone A: 知識搬家（不改執行模型）

目標：把 prompt/checklist 的來源從 Python 字串改為 Skill 檔案，但**保留現有 Phase class**。

1. **建立基礎設施**
   - `skill_loader.py` — discover, activate, get_reference
   - `playbook.py` — schema 定義、載入、驗證
   - 測試

2. **建立 Builtin Skills**
   - `data/skills/spec_first/SKILL.md`, `data/skills/spec_revise/SKILL.md`, ...
   - 遷移 `data/templates/` 到 skill references/
   - 測試：SkillLoader 載入正確，placeholder 解析正確

3. **現有 Phase class 改用 SkillLoader**
   - SpecPhase, PlanPhase 等改為「用 SkillLoader 產 checklist」
   - 刪除 `checklist_templates.py`, `checklist_generator.py`
   - 驗證：**行為與 v0.1 完全一致**，只是 prompt 來源改變

4. **建立 default.yaml playbook**
   - 目前只作為 schema 定義，尚未驅動執行

交付物：Skill 體系建立完成，現有功能不受影響。

### Milestone B: 執行模型重寫

目標：用 GenericPhase + BlackboardWorkflowRuntime 取代 5 個特化 Phase class，並讓 workflow transition 以 blackboard / baton / capability receipt 為主。

5. **Blackboard**
   - `blackboard.py` — 資料模型、持久化、rebuild
   - 測試

6. **GenericPhase + Hooks**
   - `generic_phase.py` — lifecycle hooks pipeline
   - `hooks/` — 所有 builtin hook 實作
   - 測試：GenericPhase + mock agent + Blackboard

7. **BlackboardWorkflowRuntime**
   - `workflow_runtime.py` — 編排、pause / resume、baton transition、capability receipt gate
   - `playbook_runner.py` / `status_codes.py` 只保留必要過渡層
   - 測試：完整 playbook 執行流程

8. **接線到 CLI（Wave 1）**
   - `make()` → BlackboardWorkflowRuntime
   - 個別 step 命令 → `BlackboardWorkflowRuntime.run(start_step=X, single_step=True)` 或對應 thin wrapper
   - `summary` / `show` / timeline 的 phase 列表改從 playbook 讀取
   - 刪除 5 個 Phase class

交付物：`cafe make` 走 BlackboardWorkflowRuntime，default playbook 的主流程不再依賴 status code 才能完成 transition。

### Milestone C: 全面動態化

目標：支援自訂 playbook + skill，CLI 完全動態，驗證非軟體開發流程。

9. **自訂 Skill + Playbook 支援**
   - `.cafe/skills/` 和 `.cafe/playbooks/` 的 custom override
   - Config 的 `playbook:` key
   - 驗證：security_audit 範例能正確運作

10. **Playbook Validation + Tooling**
    - `cafe playbook validate <playbook>` — 檢查 schema、skill 存在性、transition 合法性、tool pattern 冗餘
    - `cafe skill list/show/validate`
    - `cafe playbook list/show`

11. **CLI Wave 2**
    - 動態 CLI 命令（playbook 的 step 自動出現在 `cafe <step_name>`）

12. **非軟體開發流程 dry-run 驗證**
    - 用 custom playbook 定義至少一條非軟體開發流程（如招募、內容 production）
    - `cafe playbook validate` 通過
    - 驗證 playbook schema 是否夠通用、role model 是否太 dev-centric、artifact/event 模型是否只對 code workflow 有效

13. **更多 Builtin**
    - hotfix playbook, simple playbook
    - 更多 skill 範例

交付物：完整的自訂 playbook + skill 支援，至少一條非軟體開發流程可 validate。

> **Note**: `cafe playbook simulate`（mock executor 走 graph）列為 v0.2.x 目標，不是 Milestone C 的 blocking item。

## 目前待執行項目

以下清單是目前 `v0.2` blackboard runtime 重構的 live backlog。重點不是再補 status-code 相容，而是持續把 workflow 主路徑收斂到 `artifact + baton + capability receipt`。

- [x] 建立 `BlackboardWorkflowRuntime` 作為新的 workflow 核心入口，讓 workflow 主路徑與 phase alias 能 hand off 到 blackboard / baton 模型，而不是直接依賴 legacy `PlaybookRunner.run()`
- [x] 讓 `pr` step 改成 baton-driven + receipt-gated completion；host-side publish / comment / open-link hooks 也接受 `pr -> done` baton，不再只依賴 `CAFE_CONFIRMED`
- [x] 補齊 summary / timeline 對 baton-driven phases 的支援：phase `status.json` 缺席時，能從 iteration `context.json` + blackboard / baton 推導 phase 狀態，避免 `cafe summary` 失真
- [x] 讓 workflow 主入口可直接承接初始需求：`cafe make --user-input ...` / `cafe workflow --execute --user-input ...` 能直接把需求送進第一個 `spec` step，不再需要先跑 `cafe spec`
- [x] 統一人工編輯入口為 `cafe edit <phase>`，並將 `cafe spec edit` / `cafe plan edit` / `cafe review edit` 收斂為 legacy wrapper
- [x] 繼續縮小 `GenericWorkflowStepExecutor` 的責任邊界，移除剩餘的 status-code persistence 與 `context.json` / `status.json` 依賴，讓 step executor 只負責 iteration 執行與 artifact 產出
- [x] 清掉 generic workflow prompt / context 組裝裡殘留的 status-code 語意，避免新 runtime 仍被舊 completion model 反向污染
- [x] 把 `cafe spec`、`cafe plan`、`cafe develop`、`cafe review`、`cafe pr` 收斂成 `BlackboardWorkflowRuntime` 的 thin wrapper，不再維持各自獨立的 status-code-driven UX
- [x] 將 `spec/plan/develop/review/pr/dev` 從 `cafe --help` 主命令清單隱藏，並把 wrapper / runtime 內部的使用者提示全面導回 `cafe make`
- [x] 將純文字 `next_step.txt` 相容解析限制在 chat / CLI handoff 邊界；workflow runtime 與 PR baton alignment 預設只接受結構化 baton contract
- [x] 盤點並移除 CLI / workflow entry / resume / debug 路徑上剩餘的 legacy 狀態來源，確保 pause、resume、complete 的判定都只信 blackboard 與 baton
- [x] 決定 legacy phase alias 的最終退場形式：保留一段過渡期後直接刪除，或保留極薄兼容層但完全退出主要文件與互動流程
- [x] 將 `core/phase.py` 與 legacy `spec_phase.py`、`plan_phase.py`、`develop_phase.py`、`review_phase.py`、`pr_phase.py` 逐步隔離出 workflow 核心路徑，最後只保留過渡用途或直接刪除
- [x] 讓 `PlaybookRunner` 退出 active workflow path，只保留必要的過渡層與測試依賴；等新 runtime 接完主流程後再刪除
- [x] 當 default workflow 主路徑已完全切到 blackboard runtime 後，再集中做真實情境手測：`cafe make`、pause/resume、chat handoff、`cafe reset` 後續跑、`pr` publish / receipt

### 保留的相容層

- （issue #386 更新）`next_step.txt` 純文字 step name 相容格式已完全移除；`allow_legacy_text` 參數與 `key=value`、`chat_handoff` alias 解析路徑不再存在。所有讀取路徑（workflow runtime、CLI single-step alias、chat handoff、`cafe workflow`）只接受結構化 JSON baton contract，並以嚴格 `HandoffOwner` / `HandoffIntent` enum 驗證；無效或純文字 baton 一律回報 schema 錯誤而非被正規化。
- Workflow runtime、baton validation、PR feedback alignment 只讀結構化 baton contract，避免 Milestone C 期間繼續把 legacy handoff shape 當成核心執行模型。
- Hidden legacy phase commands (`cafe spec` / `plan` / `develop` / `review` / `pr`) 已於 issue #315 刪除；使用者導引與主要入口為 `cafe make` / `cafe workflow --start-step <step> --execute`。
- Generic workflow step 仍會接受現有 skill 回傳的 `CAFE_*` 結果，但只在 step executor 邊界轉成結構化 baton contract（`workflow.status_transition_adapter`）。`BlackboardWorkflowRuntime` 先消費 baton，再把 status parser 留給 mock / legacy executor 測試與過渡 wrapper。
- `core/phase.py` 在 generic workflow path 僅保留 iteration 目錄、session、token、checklist retry 等執行輔助；它不再是 workflow transition owner。Legacy `*_phase.py` class 僅供隱藏 phase alias 與過渡測試使用。
- `context.json` 保留作 iteration debug / summary input；`status.json` 不再由 generic workflow step 寫入，也不是 workflow pause、resume、complete 的權威來源。

### #227 smoke / verification

- Targeted runtime tests: `uv run --with pytest pytest tests/unit/test_generic_phase.py tests/unit/test_generic_workflow_step.py tests/unit/test_workflow_runtime.py`
- CLI / integration coverage: `uv run --with pytest pytest tests/unit/test_cli_workflow_command.py tests/integration/test_workflow_e2e.py`
- Full suite: `uv run --with pytest pytest` (`1899 passed, 5 skipped, 1 xfailed`)
- Temp repo smoke: `cafe workflow --issue issue227-smoke --dry-run --user-input "issue 227 smoke test"` 跑完 `spec -> plan -> develop -> review -> pr -> done`，PR 以 `BATON_WORKFLOW_COMPLETE` 完成。
- `cafe make` 由 `tests/unit/test_cli_make.py` 覆蓋入口轉接與 `--user-input`；此命令沒有 dry-run option，未在 smoke 中啟動真實 agent。
- `cafe reset pr` 在 dry-run smoke issue 上沒有 versioned iteration 可 reset；reset 對 real iteration 的行為由 `tests/unit/test_reset_command.py` 覆蓋，未在本 cleanup 引入新的 reset state source。

## v0.2 預留但不完整實作的入口

為了讓後續版本能自然演化，v0.2 在以下地方預留擴充點：

| 預留項目 | 預留方式 | 預計完整實作版本 |
|---|---|---|
| HumanTask | `assignee_type: human` parse + validate，runner 遇到時報錯（見下方） | v0.3 開發週期（`0.3.x`；完整 task schema / inbox / assignment） |
| HumanTask events | EventEntry 的 `event_type` 預留 `human_task_created` / `human_task_completed` | v0.3 開發週期（`0.3.x`） |
| Suspend / Resume | 不綁死在 `interactive_qa`，狀態機不假設人工介入只有 clarification | v0.3 開發週期（`0.3.x`） |
| Subflow | step schema 的 `type: subflow`（parse + validate，不執行） | v0.4 開發週期（`0.4.x`） |
| Object Reference | ArtifactKind 未來可擴充 `OBJECT_REF` | v0.4 開發週期（`0.4.x`） |
| Schema Version | `blackboard.json` 的 `schema_version` 欄位，新版可讀舊版 | 全版本 |

### assignee_type: human / auto 的 v0.2 行為

v0.2 **不賦予 `assignee_type: human` 任何 runtime 語意**，避免過早綁死互動模型：

- **Parse + Validate**：playbook 可以寫 `assignee_type: human`，schema 不報錯
- **Runner 遇到時報錯**：`"Step 'X' has assignee_type=human, which is not supported in v0.2. Use v0.3+ or change to assignee_type=agent."`
- **`cafe playbook validate`**：標註 `⚠ Step 'X': assignee_type=human (reserved for v0.3)`

`assignee_type: auto` 同理，v0.2 僅 parse，具體語意在 v0.3 開發週期定義。

這樣保留 schema 表達力和 playbook 驗證價值，但不承諾 runtime semantics。完整的 HumanTask model（task schema / inbox / assignment / SLA / 非同步完成）在 `0.3.x` 開發週期逐步設計與交付，並作為發布 `0.4.0` 前的完成範圍。

## v0.2 不做的東西

1. **不做 HumanTask 正式實作** — 納入 v0.3 開發週期（`0.3.x`），v0.2 只預留入口
2. **不做 scripts/ 自動執行** — v0.2.x 或 v0.3 開發週期開放 custom hooks via shell script
3. **不做 AI 驅動編排** — workflow 仍維持確定性編排；agent 只負責產出 artifact / baton，外部副作用由 host capability 執行與驗證
4. **不做 runtime 動態新增 step** — 所有 step 在 playbook 載入時確定
5. **不做特化 Phase class** — 全部用 GenericPhase + hooks
6. **不做 Business Object** — 納入 v0.4 開發週期，且傾向獨立系統（路徑 B），不演化 Blackboard

## 驗證方式

1. **Milestone A**: `cafe make` 行為與 v0.1 完全一致（prompt 來源改變，輸出不變）
2. **Milestone B**: `cafe make` 走 BlackboardWorkflowRuntime，default playbook 主流程以 blackboard / baton / receipt 完成 transition
3. **Milestone C**:
   - 自訂 Skill + Playbook 能正確執行（security_audit 範例）
   - 覆蓋 builtin skill 能正確載入 custom 版本
   - Blackboard 正確追蹤 artifact 版本，各 step 看到跨 step 更新
   - baton transition 與 capability receipt gate 正常
   - Progressive disclosure 只在需要時載入 Skill 內容
   - 向下相容：沒有 playbook key 的 config 預設 default
   - `cafe playbook validate` 能正確檢查自訂 playbook
   - 至少一條非軟體開發流程的 custom playbook 能通過 validate
   - `assignee_type: human` 能 parse + validate，runner 遇到時明確報錯（不靜默跳過）
