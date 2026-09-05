---
name: cafe-plan
description: "產出可執行的開發計畫"
version: 1.8.0
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
      prompt: Review the proposed direction, then confirm it or describe the needed adjustment.
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
    follow_instruction: Follow the Plan template only after the solution direction is confirmed
---

# Plan

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}

## Confirmed artifact sync

- Do not execute `scripts/sync_github.sh`. After confirmation, the trusted
  runtime evaluates the fixed `cafe.github.issue_comment` capability gate.

## Instructions
- Plan 必須在同一 phase 內依序完成兩個 stage，不得另建 phase：先完成 solution alignment，
  取得 user 明確確認後，才撰寫 detailed Plan。
- Checklist 的 iteration selector 只區分首次進入與已有前次輸出的後續執行；它不代表
  solution alignment 只能進行一輪，實際 stage 一律由下述 canonical marker 判斷。
- `{output_file}` 第一個非空白行必須是唯一的 canonical marker：方案階段使用
  `<!-- plan-stage: solution-alignment -->`；完整計畫階段使用
  `<!-- plan-stage: detailed-plan -->`。只依第一個非空白行判斷 stage，不得從 Development
  Guide、feedback 或其他 user content 中搜尋／推斷 marker。
- 方案階段的第二個非空白行固定為
  `Plan confirmation answer: <localized exact answer>`；answer 應簡短並使用 agent native
  language。只依這個 canonical 位置取得 expected answer，不得從其他內容推斷。
- Solution alignment 階段先讀 spec 與足以做決策的最少 repo 證據，提供一個建議方向、
  `會做`、`不做`、`關鍵取捨`；範圍需同時檢查有無漏做，以及有無超出需求、引入不必要
  複雜度／抽象／延伸工作。沒有實質取捨時明寫「無」，不得虛構替代方案。
- Alignment output 必須保留原始 `## Development Guide`，並明標尚未確認、不可執行；此時
  使用 `# Unconfirmed Solution Direction` 與警告 `Status: UNCONFIRMED — not executable`，
  並依序寫 `## Recommended Direction`、`## Will Do`、`## Will Not Do`、
  `## Key Trade-offs`。不得寫 Test List、implementation tasks 或逐檔實作步驟，也不得
  交給 `develop`。
- 用 id=`solution_direction_confirmation` 的單一 `questions.xml` 問題請 user 確認。`<title>`
  必須以短版重述建議方向（1–2 句）、會做（最多 3 點）、不做（最多 3 點）及關鍵取捨
  （最多 2 點），讓未顯示 output 的 HumanTask surface 也能獨立理解；唯一顯式 option 必須與
  canonical `Plan confirmation answer` 的本地化文字完全相同。調整使用 UI 既有的
  Other/free-text，不要手寫 Other option。
- 只接受一種且不得混用兩種 HumanTask 投影：durable/event-driven 的唯一
  `solution_direction_confirmation:` answer，或 local legacy 的單一 `Q1:`/`A1:` pair。
  去除 answer 首尾空白後，只有與前一份 output 的 canonical `Plan confirmation answer` 完全
  相等才可進入 detailed Plan。substring、否定句、額外文字、缺漏、混合格式或 Other 回答
  一律視為未確認，更新方案後再次走 solution alignment。
- 前一份 output 的 canonical marker 已是 `detailed-plan` 時，維持既有 Plan revision；只有
  feedback 實質改變方案方向時，才切回 `solution-alignment` 並重新確認。
- Detailed Plan 階段才依規格拆解實作步驟，先列測試，再列實作
- 嚴格遵守 TDD，避免直接寫程式碼
- 選定執行或部署架構前，除非 repo、規格或本輪對話已有明確證據，否則預設 user
  不熟悉主機、網路與雲端維運。先透過 `questions.xml` 用生活化問題確認會在哪些裝置與
  地點使用、個人電腦關機時是否仍需可用、能否接受第三方雲端與可能費用，以及願意承擔的
  維護程度；只詢問會實質改變方案的最少問題，已有答案不得重問。
- 收到使用情境後，先用 user 可感知的結果與取捨提出一個適合的預設建議，再說明技術方案；
  不得假設 user 擁有固定 IP、會讓個人電腦或 NAS 24 小時開機，或能自行維運伺服器。
  建議外部服務不等於取得採用、付費、建立資源或部署授權；各項授權仍須依既有 mandate 與
  human-task 規則處理，並在 plan 明列哪些遠端 mutation 需要另行確認。
- Detailed Plan 產出前必須完成 **`## Test List`**（`Unit tests (N)` 與 `Integration tests (M)`，每項有標籤並對應 invariant 或 user journey；N 或 M 為 0 時簡述原因）
- 撰寫 Test List 與斷言規則時請閱讀 `references/test_invariants_policy.md`（integration 以 journey/invariant 描述，不以 UI component 列項）
- 只把與變更直接相關的 targeted checks 安排為 develop task；repository 的 pre-commit、pre-push、CI、coverage 與 release gate 另標示為外部品質閘門，不得把它們重複寫成 phase 執行任務，也不得同時要求外層 script 與其內含的子指令
- 在計畫輸出確認前，請依儲存庫證據及 shared skill「cafe-workflow-common」的
  `references/issue_decomposition.md` 評估實作範圍的拆分需求。請使用其中固定的
  `Decision: `keep` or `split``, `Rationale`, `Current issue scope`、`Trigger`
  及後續 issue 表格欄位：`Title`、`Goal`、`Depends on`、`Scope boundary`、
  `Non-goals` 與 `Definition of Done`。計畫可調整相依順序，但不得悄悄改變已確認的
  產品範圍；僅提出建議，不建立 issue、更新路線圖、變更優先順序，也不得讓尚未解決的
  `split` 進入 develop。
- 延續既有計畫格式與使用者需求
- User 確認暫停、交給 `develop` 前的 GitHub sync 與 baton 順序：請依 shared skill「cafe-workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**；phase agent 不直接執行 sync wrapper。
- 計畫草稿需 user 確認時：把 next-step baton 寫入 `user`，不要直接交給 `develop`（其餘細節以 cafe-workflow-common 為準）。

### Required plan sections (must be filled before handoff)

Every detailed Plan output must include these **labeled** sections (see built-in templates under `assets/templates/`):

0. **Confirmed Implementation Approach** — the confirmed direction, included and excluded scope, and material tradeoffs; it must agree with the last solution-alignment checkpoint.

1. **Negative space** — what deps/abstractions we will **not** introduce, each with a one-line reason. If none apply, state explicitly (e.g. "No new dependencies expected.").
2. **Layering map** — where business logic, persistence, and UI live, with **concrete file or module paths**.
3. **Dependency ADR** — table or list of runtime/dev deps to add (why, alternatives, requirement served), or explicit "No new dependencies expected."

Empty placeholders are incomplete. The user must see all four sections at plan confirm (`confirm_output`).

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
