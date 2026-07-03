# CAFE Workflow Skill 規範

本規範從既有 builtin skills（`src/cafe/data/skills/`）抽象而來。新增或修改 skill 時必須遵守；
若既有 skill 與本規範衝突，以本規範為準並順手修正。

## 1. Skill 的四種類型與內外之分

| 類型 | 界別 | 用途 | 例子 |
| --- | --- | --- | --- |
| **Phase skill** | 內部 | 綁定 playbook 的一個 workflow step，由 runtime 注入 prompt 執行 | `cafe-spec`、`cafe-plan`、`cafe-develop`、`cafe-review`、`cafe-pr`、`cafe-draft`、`cafe-incident_triage` |
| **Shared skill** | 內部 | 跨 phase 的共用規則或工具，被 runtime 自動附掛或被其他 skill 引用 | `cafe-workflow-common`、`cafe-github_sync`、`cafe-common-chat-handoff` |
| **Chat skill** | 內部 | `cafe chat` 內處理特定變更類型，結尾走 common chat handoff | `cafe-chat-develop-change`、`cafe-chat-spec-revision`、`cafe-chat-plan-revision` |
| **Driver / meta skill** | 外部 | 給終端上的人或外層 agent 用，不注入 workflow phase | `use-cafe-workflow`、`write-cafe-skill` |

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

- `name`：小寫。**內部 skill 一律 `cafe-` 開頭**，後段依類型沿用既有慣例：
  phase skill 用 snake_case（`cafe-brief_first`、`cafe-incident_triage`）、
  shared / chat skill 用 kebab-case（`cafe-chat-develop-change`、`cafe-workflow-common`）。
- **外部（driver / meta）skill 不加 `cafe-` 前綴**，但會被裝到使用者的通用 skill 目錄
  （如 `~/.claude/skills/`），名稱必須自帶 CAFE 語境（`use-cafe-workflow`、`write-cafe-skill`），
  不要用泛用動詞片語（`write-skill`、`review` 這類名字幾乎必撞）。
- `description`：必寫「何時使用」。phase skill 可用中文動詞片語（如「審查程式碼品質與風險」）；
  shared / meta skill 用英文 "Use this skill when ..."。
- `version`：semver，一律加上；行為變更時 bump。
- frontmatter 在 activate 時會被剝除，不要把指令寫在 frontmatter 裡。

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
**只能使用下表的 key**；用了 runtime 沒提供的 key，`{x}` 會原樣漏進 agent prompt
（既有反例：`cafe-spec` skill 曾用沒人提供的 `{blackboard_digest}`）。

| Placeholder | 提供時機 |
| --- | --- |
| `{agent_file}` | 所有 phase step |
| `{output_file}` | 所有 phase step |
| `{handoff_summary}` | 所有 phase step |
| `{blackboard_path}`、`{next_step_path}` | 所有 phase step |
| `{valid_to_steps}`、`{step_transitions}` | 所有 phase step |
| `{spec_file}` | step 的 `input_artifacts` 含 `spec`，或 issue 目錄已有最新 spec |
| `{plan_file}` | step 的 `input_artifacts` 含 `plan`，或 issue 目錄已有最新 plan |
| `{develop_file}` | `input_artifacts` 含 `code` |
| `{feedback_file}` | `input_artifacts` 含 `review_feedback` 或 `pr_result` |
| `{commits}`、`{base_branch}` | 僅 `cafe-pr` skill |

新增 placeholder 必須同步修改 `generic_workflow_step.py` 的 `_build_context()`，並更新本表。

## 6. Handoff 與 baton：單一權威在 cafe-workflow-common

- baton 機制、JSON schema、合法 `to_owner` / `intent` 值、範例——**只存在於 `cafe-workflow-common`**。
  phase skill 一律不得重述或另舉範例。
- phase skill 只寫「路由決策」，例如：
  - 草稿需 user 確認 → 「把 next-step baton 寫入 `user`，不要直接交給 `<下一步>`」
  - review 要求修改 → 「把 next-step baton 寫成 `develop`」
- 引用下一步時優先用「playbook 的下一個 step」描述，避免 hardcode 只在某條 playbook 存在的名字；
  必要時註明預設 playbook 的值（如「預設 playbook 為 `pr`」）。

## 7. Shared rules 的放置

- 一條規則若適用於多個 phase，就放進 shared skill（通常是 `cafe-workflow-common`），
  並更新它的 **Where policies live** 索引表；不要複製到各 phase skill。
- phase skill 引用共用規則的固定句式：

  > 請依 shared skill「cafe-workflow-common」的 **<Section 名>**；本 skill 不重複敘述。

- runtime 對每個 phase 自動附掛的 shared skills 定義在
  `generic_workflow_step.SHARED_WORKFLOW_SKILLS`（目前為 `cafe-workflow-common`、`cafe-github_sync`）。
  新的 shared skill 若要自動附掛，需同步修改該常數。

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

## 13. 驗收 checklist

- [ ] 類型判定正確，段落順序符合該類型模板
- [ ] `name` = 資料夾名；`description` 說明何時使用；有 `version`
- [ ] 只使用 §5 的 placeholder
- [ ] 沒有重述 baton schema、chat handoff 格式或其他 shared 規則；引用句式符合 §7
- [ ] `## Handoff` 為固定一行
- [ ] references / scripts 有明確觸發條件；對外 mutation 走 host-side hook
- [ ] 若是共用規則，已更新 `cafe-workflow-common` 的 Where policies live 索引
