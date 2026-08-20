---
name: cafe-plan
description: "產出可執行的開發計畫"
version: 1.6.1
workflow:
  execution_profile:
    workload: planning
    reasoning: high
    risk_domains: [architecture, integration]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: development-guide
      pattern: revision_feedback
      prompt: "Please enter development guide (can be left empty)"
      input_schema: feedback
      required: false
    - id: output-review
      pattern: confirm_output
      prompt: Review the implementation plan and choose how to continue.
      input_schema: decision
      decisions:
        - id: confirm
          label: Confirm and continue
        - id: revise
          label: Request revision
          requires_feedback: true
          correction: true
    - id: clarification-answers
      pattern: answer_questions
      prompt: Answer the requested clarification questions.
      input_schema: answers
      questions_from_xml: true
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: true
    - artifacts: [spec]
      placeholder: spec_file_path
      required: true
  checklist:
    context_references:
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_steps_iteration_1.md
          - template_catalog: true
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2}
        sections:
          - reference: execution_steps_iteration_n.md
          - template_catalog: true
          - optional_checklist: basic_principles.md
    include_role_guidance: true
  output_templates:
    catalog: plan
    follow_instruction: Follow template structure when writing plan
---

# Plan

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}

## Available scripts

- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Instructions
- 依規格拆解實作步驟，先列測試，再列實作
- 嚴格遵守 TDD，避免直接寫程式碼
- 選定執行或部署架構前，除非 repo、規格或本輪對話已有明確證據，否則預設 user
  不熟悉主機、網路與雲端維運。先透過 `questions.xml` 用生活化問題確認會在哪些裝置與
  地點使用、個人電腦關機時是否仍需可用、能否接受第三方雲端與可能費用，以及願意承擔的
  維護程度；只詢問會實質改變方案的最少問題，已有答案不得重問。
- 收到使用情境後，先用 user 可感知的結果與取捨提出一個適合的預設建議，再說明技術方案；
  不得假設 user 擁有固定 IP、會讓個人電腦或 NAS 24 小時開機，或能自行維運伺服器。
  建議外部服務不等於取得採用、付費、建立資源或部署授權；各項授權仍須依既有 mandate 與
  human-task 規則處理，並在 plan 明列哪些遠端 mutation 需要另行確認。
- 產出計畫前必須完成 **`## Test List`**（`Unit tests (N)` 與 `Integration tests (M)`，每項有標籤並對應 invariant 或 user journey；N 或 M 為 0 時簡述原因）
- 撰寫 Test List 與斷言規則時請閱讀 `references/test_invariants_policy.md`（integration 以 journey/invariant 描述，不以 UI component 列項）
- 在計畫輸出確認前，請依儲存庫證據及 shared skill「cafe-workflow-common」的
  `references/issue_decomposition.md` 評估實作範圍的拆分需求。請使用其中固定的
  `Decision: `keep` or `split``, `Rationale`, `Current issue scope`、`Trigger`
  及後續 issue 表格欄位：`Title`、`Goal`、`Depends on`、`Scope boundary`、
  `Non-goals` 與 `Definition of Done`。計畫可調整相依順序，但不得悄悄改變已確認的
  產品範圍；僅提出建議，不建立 issue、更新路線圖、變更優先順序，也不得讓尚未解決的
  `split` 進入 develop。
- 延續既有計畫格式與使用者需求
- User 確認暫停、交給 `develop` 前是否執行 GitHub sync、以及 baton 順序：請依 shared skill「cafe-workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**，並搭配 `cafe-github_sync` skill 的腳本說明；本 skill 不重複敘述。
- 計畫草稿需 user 確認時：把 next-step baton 寫入 `user`，不要直接交給 `develop`（其餘細節以 cafe-workflow-common 為準）。

### Required plan sections (must be filled before handoff)

Every plan output must include these **labeled** sections (see built-in templates under `assets/templates/`):

1. **Negative space** — what deps/abstractions we will **not** introduce, each with a one-line reason. If none apply, state explicitly (e.g. "No new dependencies expected.").
2. **Layering map** — where business logic, persistence, and UI live, with **concrete file or module paths**.
3. **Dependency ADR** — table or list of runtime/dev deps to add (why, alternatives, requirement served), or explicit "No new dependencies expected."

Empty placeholders are incomplete. The user must see all three sections at plan confirm (`confirm_output`).

### Project principles alignment (graceful degradation)

Before finalizing the plan, if the project has `.cafe/strategic_context.yaml`:

- Read `documents.principles.path`:
  - If `status: exists`: load that file (do not hardcode `PRINCIPLES.md`). Ground **Negative space** (red lines / out-of-scope) and **Dependency ADR** (capability or requirement ties) using principles.
  - If `status: missing` or the field is absent: keep the three sections in pure technical form; optional note "Principles document not configured; leave principles cross-refs blank."

### Stale major versions (prompt-level)

When the Dependency ADR proposes a **new major** of a package, note whether that major was released within the last **30 days** (default window). If so, justify the risk in the ADR or pick a stable alternative. Full registry automation is out of scope; checklist/template language is the minimum deliverable.

## Output
Write plan to: {output_file}

## Context packets

The complete plan Markdown is the only semantic authority. Keep ordinary
headings, the Test List, and executable checkboxes, but do not add
packet-specific IDs, an authoritative delivery-ID list, or a `Downstream
Contract`. Runtime-generated structural packets derive checkbox identity from
document order and safely fall back to the complete source when unavailable.

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
