# CAFE Workflow Skill 規範

本規範從既有 builtin skills（`src/cafe/data/skills/`）抽象而來。新增或修改 skill 時必須遵守；
若既有 skill 與本規範衝突，以本規範為準並順手修正。

## 1. Skill 的四種類型與內外之分

| 類型 | 界別 | 用途 | 例子 |
| --- | --- | --- | --- |
| **Phase skill** | 內部 | 綁定 playbook 的一個 workflow step，由 runtime 注入 prompt 執行 | `cafe-spec`、`cafe-plan`、`cafe-develop`、`cafe-review`、`cafe-pr`、`cafe-draft`、`cafe-incident_triage` |
| **Shared skill** | 內部 | 跨 phase 的共用規則或工具，被 runtime 自動附掛或被其他 skill 引用 | `cafe-workflow-common`、`cafe-github_sync`、`cafe-common-chat-handoff` |
| **Chat skill** | 內部 | `cafe chat` 內處理特定變更類型，結尾走 common chat handoff | `cafe-chat-develop-change`、`cafe-chat-spec-revision`、`cafe-chat-plan-revision` |
| **Driver / meta skill** | 外部 | 給終端上的人或外層 agent 用，不注入 workflow phase | `use-cafe-workflow`、`write-cafe-phase`、`write-cafe-playbook` |

先判定類型，再套用對應章節的模板。一個 skill 只屬於一種類型。

**內部 vs 外部是管理邊界**：
- **內部 skill**（phase / shared / chat）由 runtime 在 step 執行或 `cafe chat` 啟動時，
  自動安裝到 worktree-local 的 CLI native 目錄（`.claude/skills/` 等）。
  資料夾名**必須自帶 `cafe-` 前綴**，安裝時原樣複製、不再改名，
  讓 playbook、prompt、安裝目錄、CLI 呼叫（`/cafe-spec`）全程同名。
- **外部 skill**（driver / meta）不經 bridge，由使用者手動 symlink / copy 到自己的
  skill 目錄。不加 `cafe-` 前綴，但名稱必須自帶 CAFE 語境（見 §3）。

## 2. 存放與探索規則

- 路徑：`<root>/skills/<skill-name>/SKILL.md`。root 依優先序為
  builtin（`src/cafe/data/skills/`）→ global（`~/.cafe/skills/`）→ project（`.cafe/skills/`），
  後者同名覆蓋前者。
- **資料夾名必須等於 frontmatter `name`**；builtin skill 不符會直接 raise。
- 舊名（含所有未加前綴的內部 skill 名）只能透過 loader 的 `_SKILL_ALIASES` 過渡
  （如 `spec` → `cafe-spec`），不要留兩份內容。
- 專案層（`.cafe/skills/`）要覆蓋 builtin 時，資料夾名必須用**含前綴的正式名**
  （`cafe-spec`），用舊名不會覆蓋、只會變成另一個 skill（loader 會警告）。
- **Custom playbook 用的 skill 放專案層 `.cafe/skills/`**，與 `.cafe/playbooks/*.yaml`
  一起進版控；個人跨專案重用才放 global `~/.cafe/skills/`。命名建議照 builtin 慣例
  加 `cafe-` 前綴（editorial example 的做法），安裝時原樣複製，禁用泛用名與
  deprecated 舊名（`review`、`draft` 這類）。
- skill 資料夾內只允許 `SKILL.md`、`references/`、`scripts/`、`assets/`。
  不要建立 `README.md`、`CHANGELOG.md` 或設計筆記。

## 3. Frontmatter

```yaml
---
name: <與資料夾同名>
description: "<何時使用這個 skill，不是它包含什麼>"
version: 1.0.0
---
```

### Workflow metadata contract

Phase skills may declare workflow-facing behavior in frontmatter. Runtime-owned
paths and blackboard state remain outside this block; a declaration only states
how the skill consumes them:

```yaml
workflow:
  required_tools:
    - "Bash(cafe verification check:*)"
  prompt_inputs:
    - artifacts: [research_notes]
      placeholder: evidence_file
      required: true
  prompt_references:
    optional_evidence_instruction: optional_evidence_instruction.md
  checklist:
    context_references:
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {iteration: 1}
        sections: [{reference: execution_first.md}]
      - when: {artifact_present: [editor_feedback]}
        sections: [{reference: execution_feedback.md}]
    include_role_guidance: true
    compact_agent_guidance: false
  output_templates:
    catalog: research-report
```

- `required_tools` lists tool contracts the skill cannot execute correctly
  without. Every playbook step selecting the skill must grant each declaration
  through `allowed_tools`; strict playbook validation rejects missing grants.
- An exact grant is preferred. A broad grant for the same tool, such as `Bash`
  or `Bash(*)`, also satisfies a narrower declaration, but should only be used
  when the step genuinely needs that breadth.
- Keep optional diagnostics out of `required_tools`; otherwise every binding is
  forced to grant a tool the normal path does not need.

### Human-task policy contract

When a phase may pause for a person, declare its reusable policy under
`workflow.human_tasks`; the playbook then binds that policy to a trigger and
declares the allowed continuations. The skill owns wording and input validation,
while the playbook owns routing. Runtime state, files, and baton mutation remain
runtime-owned.

```yaml
workflow:
  human_tasks:
    - id: output-review
      pattern: confirm_output
      prompt: Review the result and choose how to continue.
      input_schema: decision
      decisions:
        - id: confirm
          label: Confirm and continue
        - id: revise
          label: Request revision
          requires_feedback: true
```

- The valid patterns and matching schemas are: `confirm_output` → `decision`,
  `answer_questions` → `answers`, `revision_feedback` → `feedback`,
  `no_changes_needed` → `decision`, and `select_next_step` → `target`.
- Decision policies declare every choice; `requires_feedback: true` makes the
  feedback field mandatory for that choice. Answer policies use inline questions
  or `questions_from_xml: true`; target policies declare `allowed_targets`.
- `required: false` is only for optional feedback. Use `correction_guidance` for
  the actionable message shown after invalid interactive or command input.
- Keep policy identifiers stable. Do not duplicate a policy's wording or input
  rules in a playbook, hook, or CLI branch. If a matching policy/binding is
  absent, the workflow remains paused and records a configuration error rather
  than guessing a continuation.

- `prompt_inputs` are resolved in listed candidate order. Required inputs stop
  before agent invocation with the placeholder and candidates named; absent
  optional inputs are omitted.
- `prompt_references` are named `references/*.md` sections interpolated into the
  skill body. Use one for an instruction that depends on an optional input: it
  renders only when every placeholder inside that reference is available;
  otherwise the named marker and its instruction are omitted. Do not place an
  optional input placeholder directly in `SKILL.md` prose.
- Checklist references must remain under `references/`; variants are evaluated
  in declaration order using bounded iteration, artifact-presence, or feedback
  selectors. Role guidance is included only when explicitly requested.
  `compact_agent_guidance: true` appends that guidance without inserting a
  separator, for references that intentionally own the preceding spacing; the
  default keeps a separating newline.
- A template catalog is the owning skill's `assets/templates/` directory. A
  selection is read from `<step>.template` in `issue.yaml`; `auto` leaves the
  catalog available without selecting a file.

- `name`：小寫。**內部 skill 一律 `cafe-` 開頭**，後段依類型沿用既有慣例：
  phase skill 用 snake_case（`cafe-brief_first`、`cafe-incident_triage`）、
  shared / chat skill 用 kebab-case（`cafe-chat-develop-change`、`cafe-workflow-common`）。
- **外部（driver / meta）skill 不加 `cafe-` 前綴**，但會被裝到使用者的通用 skill 目錄
  （如 `~/.claude/skills/`），名稱必須自帶 CAFE 語境（`use-cafe-workflow`、`write-cafe-phase`、`write-cafe-playbook`），
  不要用泛用動詞片語（`write-skill`、`review` 這類名字幾乎必撞）。
- `description`：必寫「何時使用」。phase skill 可用中文動詞片語（如「審查程式碼品質與風險」）；
  shared / meta skill 用英文 "Use this skill when ..."。
- `version`：semver，一律加上；行為變更時 bump。
- frontmatter 在 activate 時會被剝除，不要把指令寫在 frontmatter 裡。

### Driver diagnosis 與 declarative repair 邊界

- `use-cafe-workflow` 可以做 bounded self-diagnosis，但不能把診斷擴張成無界的 CAFE
  refactor。它先排除 project config、暫時性 provider/network、CLI/model mismatch、stale
  install 與 agent 未遵守有效契約，再分類問題。
- `write-cafe-playbook` 只擁有 writable source-of-truth playbook YAML；
  `write-cafe-phase` 只擁有 writable source-of-truth phase/shared/chat skill 與其 supporting
  resources。兩者都不得直接修改 generated artifact、installed package 或 global CLI skill copy。
- driver/meta skill（包含 `use-cafe-workflow`）、CAFE CLI/runtime Python、workflow state
  machinery 與 host infrastructure 不屬於上述 declarative repair layer。不要建立隱含的
  `write-cafe-driver` fallback，也不要調整 declarative contract 來掩蓋 runtime defect。
- 遇到 driver 或 CAFE core defect 時，driver 應保存 sanitized evidence、read-only 搜尋 open
  與 closed upstream issues、告知 user，並建議 follow 或新開 issue；沒有 user 明確授權不得
  自動 create、comment 或 close issue。

## 4. Phase skill 標準結構

段落順序固定如下；不適用的段落整段省略，不要留空段。

```markdown
# <Title>

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Available scripts
- `scripts/<name>.sh` — 一句話說明用途

    bash scripts/<name>.sh --help

## Instructions
- 條列、動詞開頭、寫程序（procedure）而非宣告（declaration）
- 給預設值，不給選單
- 跨 phase 規則一律引用 shared skill，不重複敘述（見 §7）

## Output
Write <artifact> to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
```

規則：

- `## Role` 與 `## Handoff` 每個 phase skill 必有，且內容逐字使用上面兩行，不要改寫。
- `## Context` 只列 playbook `input_artifacts` 會提供的檔案（見 §5 的 placeholder 表）。
- `## Available scripts` 只在 `scripts/` 存在時出現。
- `## Output` 固定一行 `Write <artifact> to: {output_file}`。
- 路由決策（什麼情況把 baton 寫到哪個 step）寫在 `## Instructions`，
  用 playbook step 名或內建的 `user` / `done`；baton 的機制與 schema 不要重述（§6）。

## 5. Placeholder 契約

Placeholder 是 activate 時的**純文字替換**（`{key}` → 值），不支援條件或運算。
除了 runtime-owned key 外，skill 可用 `workflow.prompt_inputs` 宣告任意自己的
placeholder 名稱。不要依賴 artifact 名稱或新增 Python mapping；未宣告 placeholder
不會被 runtime 猜測或補成 development-phase 的檔案。

| Placeholder | 提供時機 |
| --- | --- |
| `{agent_file}` | 所有 phase step |
| `{output_file}` | 所有 phase step |
| `{handoff_summary}` | 所有 phase step |
| `{blackboard_path}`、`{next_step_path}` | 所有 phase step |
| `{valid_to_steps}`、`{step_transitions}` | 所有 phase step |
| skill-declared input | `workflow.prompt_inputs` 解析到記錄的 artifact |
| `{template_file}`、`{template_catalog}` | skill 宣告 `output_templates` 時提供 |
| `{commits}`、`{base_branch}` | runtime-owned Git context（需要時提供） |

新增 artifact placeholder 必須修改 skill metadata 與此契約說明，不得新增
`generic_workflow_step.py` 的 skill-name 分支。

## 6. Handoff 與 baton：單一權威在 cafe-workflow-common

- baton 機制、JSON schema、合法 `to_owner` / `intent` 值、範例——**只存在於 `cafe-workflow-common`**。
  phase skill 一律不得重述或另舉範例。
- phase skill 只寫「路由決策」，例如：
  - 草稿需 user 確認 → 「把 next-step baton 寫入 `user`，不要直接交給 `<下一步>`」
  - review 要求修改 → 「把 next-step baton 寫成 `develop`」
- 引用下一步時優先用「playbook 的下一個 step」描述，避免 hardcode 只在某條 playbook 存在的名字；
  必要時註明預設 playbook 的值（如「預設 playbook 為 `pr`」）。

### Planned user confirmation gate

- phase output 需要在正常流程中讓 user 審核時，skill 的 routing decision 必須暫停給 `user`，且綁定的 playbook step 必須宣告 `on.confirm_output`。兩者缺一都不完整：skill-only pause 不會成為 kickoff 契約候選；playbook-only gate 則沒有 phase 行為保證會產生該 handoff。
- `on.confirm_output` 是 planned kickoff confirmation gate 的 playbook source of truth。外層 driver 以 `cafe playbook confirmation-gates <id>` 列出候選；phase skill 不得自行寫 `user_required`、`driver_confirmable` 或 repo-wide 預設。
- `need_clarification`、`need_permission`、`alignment_checkpoint` 是條件式安全中斷，不是預定停點；不要為了讓它們出現在 kickoff 契約而改寫成 `confirm_output`。
- stop contract 以 playbook step name 為單位。如果同一 phase 有兩個需要不同 user/driver ownership 的確認時點，應拆成兩個 playbook steps；不要發明 `phase.preview`、`phase.plan` 等 pseudo-step gate 名稱。
- 新增、移除或拆分 planned gate 後，執行 `cafe playbook confirmation-gates <id>`，並回報既有 issue 的 confirmation contract 已可能 stale，必須在下一次 `cafe make` 前重新確認。

## 7. Shared rules 的放置

- 一條規則若適用於多個 phase，就放進 shared skill（通常是 `cafe-workflow-common`），
  並更新它的 **Where policies live** 索引表；不要複製到各 phase skill。
- phase skill 引用共用規則的固定句式：

  > 請依 shared skill「cafe-workflow-common」的 **<Section 名>**；本 skill 不重複敘述。

- runtime 依 active playbook `skills.workflow` 宣告解析每個 phase 的 shared
  skills；新增 shared skill 時，在 playbook 宣告，不要修改 Python 常數。
- chat 的共用與修訂能力同樣由 playbook `skills.chat` 宣告。這只決定技能環境，
  不改變 baton、HumanTask 或 alignment policy 的 owner。

## 8. Iteration 行為的兩種做法

- **差異小**：單一 skill 內依 iteration 分支（`cafe-spec` 的做法：「第一輪 / 後續輪」各一段），
  細節放 `references/execution_steps_iteration_1.md` 與 `execution_steps_iteration_n.md`。
- **差異大**：拆成兩個 skill，由 playbook 以 dict 切換（`editorial` 的做法：
  `skill: {1: cafe-brief_first, default: cafe-brief_revise}`）。

預設選前者；當兩輪的角色認知或輸出型態根本不同時才拆 skill。

## 9. references/ 與 scripts/

**references/**
- 只放「條件才需要讀」的細節，避免撐大 SKILL.md；一層深、依主題命名。
- SKILL.md 內必須明說「何時」打開哪個 reference。
- `references/execution_steps_*.md` 放程序性步驟：有順序，且可依 normal/correction、iteration 或其他模式拆分。
- `references/basic_principles.md` 放常備規則：repo 慣例、風格、不變式、反覆出現的檢核點。內容使用 `- ` bullet list；runtime 會以 opt-in 方式轉成 checklist 的 `## Basic Principles` 段，且不分 normal/correction 模式一律附掛。沒有這個檔案時不阻塞，也不改變既有 checklist。
- agent 檔 guidelines 放個人風格與角色偏好：跟 agent 走、跨 phase 生效，不應取代 workflow skill 的常備規則。

三個管道的分工：

| 管道 | 語意 | 模式 |
| --- | --- | --- |
| `references/execution_steps_*.md` | 程序：有順序、分 normal/correction 或 iteration | 分模式 |
| `references/basic_principles.md` | 常備規則：repo 慣例、風格、不變式 | 不分模式、一律附掛 |
| agent 檔 guidelines | 個人風格，跟 agent 走 | 跨 phase |

**scripts/**
- 用於重複執行的固定命令，或需要外網、憑證、GitHub/API mutation 的操作。
- 慣例：progress / error 走 stderr，結構化 JSON result 走 stdout；可重跑（idempotent）。
- 對外 mutation（push、開 PR、發 comment）由 **host-side hook** 執行 script，
  agent 只準備 local artifact，不得在 sandbox 內直接呼叫（`cafe-pr` + `sync_pr.sh` 的模式）。
- 多個 skill 共用同一 script 時，抽成 shared skill（`cafe-github_sync` 的模式），維持 CLI/JSON 契約穩定。

## 10. Chat skill 標準結構

```markdown
# <Title>

## Use This Skill When
- <觸發情境條列>

## Instructions
- <該變更類型的處理規則>
- Finish with the required common chat handoff format.
```

- 結尾格式、blackboard / baton / commit 順序等規則屬於 `cafe-common-chat-handoff`，chat skill 不重述。

## 11. 語言慣例

- 段落標題與結構詞一律英文（`## Role`、`## Instructions`、`## Handoff`）。
- Instructions 內文跟隨該 playbook 領域的既有慣例：軟體與非軟體 phase skill 為中文；
  shared / chat / meta skill 為英文。
- 同一 skill 內不要中英夾雜換行風格；引用他 skill 的 section 名保持原文。

## 12. 新增 phase skill 時的 playbook 綁定

skill 本體不含綁定；要讓它跑起來，還需在 playbook YAML 的 `steps.<step>` 設定
`skill`、`role`、`input_artifacts`、`output_artifact`、`allowed_tools`、`hooks`、`on` transitions。
skill 文件內不要假設只有某一條 playbook 會用它。

若 phase 有 planned user approval，`on` transitions 必須包含
`confirm_output: <current-step>`；完成綁定後用
`cafe playbook confirmation-gates <id>` 驗證該 step 出現在候選清單。

## 13. 驗收 checklist

- [ ] 類型判定正確，段落順序符合該類型模板
- [ ] `name` = 資料夾名；`description` 說明何時使用；有 `version`
- [ ] 只使用 §5 的 placeholder
- [ ] 沒有重述 baton schema、chat handoff 格式或其他 shared 規則；引用句式符合 §7
- [ ] `## Handoff` 為固定一行
- [ ] references / scripts 有明確觸發條件；對外 mutation 走 host-side hook
- [ ] 常備規則放 `references/basic_principles.md`，不要散落在多個 `execution_steps_*` 變體中重複維護
- [ ] 若是共用規則，已更新 `cafe-workflow-common` 的 Where policies live 索引
- [ ] plan → execute pair 使用 `output_artifact: plan` → `input_artifacts: [plan]`，execute 的 `## Context` 包含 `{plan_file}`
- [ ] 若 phase 同時 execute 舊 plan 並產生下一份 plan，已依 §15 區分 `{plan_file}` 與 `{output_file}`、先完成舊 checklist、處理 `not_required` 分支
- [ ] implementation tasks 位於 plan artifact 並使用 `- [ ]`／`- [x]`；沒有另建重複的 plan-derived checklist
- [ ] planned user approval 同時有 phase routing decision 與 playbook `on.confirm_output`；reactive interruption 未混入 kickoff 候選
- [ ] 必要工具已集中宣告在 `workflow.required_tools`，所有綁定 step 的 `allowed_tools` 均滿足宣告，選用診斷工具沒有誤列為必要工具
- [ ] 若 planned gate set 有變更，已執行 `cafe playbook confirmation-gates <id>` 並回報 issue contract 需要重新確認

## 14. Plan → Execute phase pair 的 artifact contract

當一個 phase 負責確認解法、下一個 phase 負責實作時，預設沿用 default playbook 的 `plan` → `develop` contract。這是 artifact 與 ownership convention，不限軟體開發；影片後製、資料處理、內容生產等 domain 也一樣。

### Playbook binding

```yaml
steps:
  domain_plan:
    type: skill
    skill: cafe-domain_plan
    output_artifact: plan
    "on":
      confirm_output: domain_plan
      await_agent: domain_execute
    # ...role, tools, hooks, other transitions...

  domain_execute:
    type: skill
    skill: cafe-domain_execute
    input_artifacts: [plan]
    output_artifact: domain_result
    # ...role, tools, hooks, transitions...
```

- artifact key 必須是 **`plan`**，runtime 才會提供 `{plan_file}`。skill／step 可以使用 domain 名稱，但不要把 artifact key 改成 `postproduction_plan`、`migration_plan` 等自訂名後仍期待 `{plan_file}` 自動存在。
- 若確實需要新的 artifact key 與 placeholder，必須先擴充 `generic_workflow_step.py` 的 context contract、更新 §5 與測試；不得只在 skill 裡自行發明 placeholder。
- playbook 尚未建立時，可以先寫 skill，但交付時必須明說 pair 尚未 wired，並列出以上 binding；不得宣稱 runtime 已會自動傳遞 plan。

### Plan phase output

plan phase 的 `{output_file}` 是下一個 execute phase 的 implementation plan，不是只有分類、建議或散文摘要。至少包含：

- `## Test List`：列出穩定 invariants 與 end-to-end validation；不適用的 unit／integration 類別要明寫為 0 的原因。
- `## Development Task Breakdown`（或 domain 等價標題）：依 dependency order 使用 `- [ ]`，每項有穩定 ID、inputs、action、output、validation 與 dependencies。
- 明確的 source of truth、negative space／排除項、依賴或外部服務決策、Definition of Done。
- user 確認狀態與需要另外授權的外部 mutation／費用。

plan phase 只規劃與取得確認：

- 不執行 implementation task，不提前把 task 勾成 `- [x]`。
- user 確認前把 baton 寫入 `user`；確認後才交給 execute step。
- scope、分類、依賴、外部服務或 cost 改變時，必須更新 plan 並重新確認。

### Execute skill contract

execute skill 必須宣告：

```markdown
## Context
- Implementation Plan: {plan_file}
```

並遵守：

- 先讀 `{plan_file}`，依 task dependency order 實作；不得靠搜尋目錄猜測另一份 plan。
- 每完成一項，就直接在同一份 `{plan_file}` 將 `- [ ]` 改成 `- [x]`；不得複製 task list 到 sidecar 再各自漂移。
- 新增／修改的測試與 QA 必須對應 plan 的 Test List。scope 或 invariant 改變時，退回 plan phase更新與重新確認。
- 完成前確認所有 implementation tasks 都為 `- [x]`、Test List invariants 全部通過、輸出與 evidence 已記錄。

### Plan tasks vs runtime checklist

兩者都要保留，不能互相取代：

| 機制 | 來源 | 生命週期 | 用途 |
| --- | --- | --- | --- |
| Plan task checkboxes | plan artifact 的 task breakdown | 跨 plan → execute phases | 要實作什麼；execute 直接更新 `[ ]` → `[x]` |
| Runtime `checklist.md` | `references/execution_steps_*` 加上 opt-in `references/basic_principles.md` | 單一 phase iteration | agent 是否遵守該 phase 的程序與不變式 |

不要額外產生 `implementation_checklist.md`、`execute_checklist.md` 等重複 plan tasks 的 sidecar。只有當 artifact 本身不是 implementation plan，且 playbook 明確定義不同 contract 時，才另設 domain artifact。

## 15. Forward-only plan chain convention

當一個 phase 的 **user-confirmed output** 才能決定下一個 phase 要執行的精確工作時，不必另外插入只負責抄寫 checklist 的 planning phase。讓目前 phase 在完成自身驗收後，直接產生下一份 implementation plan。

典型例子：

- 逐字稿確認後，transcribe phase 產出 audio-repair plan。
- audio repair 依 incoming plan 執行、由 user 確認音訊後，產出只含殘留嘴型問題的 lipsync plan。
- 若沒有殘留問題，audio repair 產出 `not_required` plan，playbook 直接跳過 lipsync。

### Serial `plan` binding

```yaml
steps:
  discover_and_plan:
    type: skill
    skill: cafe-domain_discover
    output_artifact: plan

  execute_and_plan_next:
    type: skill
    skill: cafe-domain_execute
    input_artifacts: [plan]
    output_artifact: plan

  execute_next:
    type: skill
    skill: cafe-domain_next_execute
    input_artifacts: [plan]
    output_artifact: domain_result
```

CAFE 在 step 啟動時先從 artifact state 解析 incoming `plan`，再為本 iteration 建立獨立 `{output_file}`，完成後才把新的 `plan` 註冊為 latest artifact。因此 bridge skill 中：

- `{plan_file}` 永遠是本 phase 要執行並勾選的 incoming implementation plan。
- `{output_file}` 永遠是下一個 phase 的新 implementation plan。
- 兩者不得是同一檔案；不得把 incoming plan 改寫成另一個 domain 的 plan。
- incoming plan 的 checkbox 仍由本 phase 原地更新為 `- [x]`；execution report 存在 domain workspace，並由 next plan 引用，不占用 `{output_file}`。

### Bridge phase order

1. 驗證 incoming `{plan_file}` 已 confirmed，依 dependency order 執行並更新 checkboxes。
2. 產生 preview／結果，留在本 phase 與 user 反覆修正，直到 user 明確接受。
3. 根據已接受結果判斷下一 phase 是否有工作。
4. 有工作時，把 scope、sources、Test List、費用／外部服務授權與 `- [ ]` task breakdown 寫入 `{output_file}`，取得 user 確認後標為 `confirmed`。
5. 沒有工作時，寫入狀態為 `not_required` 的 plan，說明 skip reason，且不得留下未勾選 implementation tasks；baton 直接路由到可選 phase 之後的 step。

### Ownership and loops

- plan producer 必須在交棒前完成 scope confirmation；execute phase 不重新搜尋素材或重新判斷要做哪些項目。
- user 對目前 phase 結果的修正留在目前 phase。只要 confirmed source of truth 與目標不變，可更新 execution parameters 或在 user 明確確認後補上同 domain 漏項。
- 不要只為了修改 checklist 建立正常的 backward transition。只有 user 推翻已確認 transcript、source media、需求或其他 source of truth 時，才人工 reopen upstream phase。
- next plan 尚未 confirmed 時 baton 指向 `user`；不得先執行下一 phase、呼叫付費 API 或消耗 credits。

### When not to produce a checklist for the next phase

若下一 phase 只是讀取前一結果後進行新的分析、規劃或固定程序，而不是執行上游已決定的工作，就不需要 plan checklist。一般 report、manifest 或 source path 足夠；不要為每個相鄰 step 都機械式產生 `plan`。
