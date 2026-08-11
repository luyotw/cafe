---
name: cafe-plan
description: "產出可執行的開發計畫"
version: 1.1.0
workflow:
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
- 產出計畫前必須完成 **`## Test List`**（`Unit tests (N)` 與 `Integration tests (M)`，每項有標籤並對應 invariant 或 user journey；N 或 M 為 0 時簡述原因）
- 撰寫 Test List 與斷言規則時請閱讀 `references/test_invariants_policy.md`（integration 以 journey/invariant 描述，不以 UI component 列項）
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

## Downstream Contract

Keep exactly one versioned `## Downstream Contract` in every produced plan. Synchronize stable IDs and each top-level task's pending/completed state with the complete plan before user confirmation; legacy artifacts without this section deliberately remain full-source inputs.

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
