---
name: cafe-spec
description: "收集、整理或修訂需求規格（依 iteration 切換行為）"
version: 1.0.0
workflow:
  checklist:
    context_references:
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_steps_iteration_1.md
          - template_catalog: true
          - optional_checklist: basic_principles.md
          - reference: dod_instruction_composed.md
      - when: {min_iteration: 2, max_iteration: 3}
        sections:
          - reference: execution_steps_iteration_n.md
          - optional_checklist: basic_principles.md
          - reference: dod_instruction_composed.md
      - when: {min_iteration: 4}
        sections:
          - reference: execution_steps_iteration_n.md
          - optional_checklist: basic_principles.md
          - reference: important_notes_iteration_4_plus_composed.md
          - reference: dod_instruction_after_notes_composed.md
    include_role_guidance: true
    compact_agent_guidance: true
  output_templates:
    catalog: spec
    follow_instruction: Follow template structure when writing analysis results
---

# Spec

## Role
Read your agent file: {agent_file}

## Available scripts

- `scripts/sync_github.sh` — Sync confirmed spec/plan output to GitHub issue comment when enabled

```bash
bash scripts/sync_github.sh --help
```

## Instructions

依 iteration 不同採取不同流程：

- **第一輪（iteration == 1）**：閱讀原始需求與既有輸出，整理規格內容並寫入輸出檔。
- **後續輪（iteration > 1）**：讀取上一版 spec 輸出與使用者回饋，修訂內容並寫回指定輸出檔；不要在輸出中暗示先前迭代的存在。

### Project principles alignment (scope guard)

在動工之前，若專案有 `.cafe/strategic_context.yaml`，讀取它並用以下方式校準範圍：

- 讀 `documents.principles.path`：
  - 若 `status: exists`：把該檔案載入為本輪 spec 的必讀文件，並在 spec 中對齊「principles 對應」「不做清單／紅線」「拿掉本 feature 會缺什麼完成標準」等欄位（範例見 spec template）。
  - 若 `status: missing` 或欄位不存在：跳過 principles 對齊欄位，spec 行為照常。
- 讀 `mandate.out_of_mandate`（清單）：
  - 若本 issue 的意圖與清單上任一項重疊（例如 deployment configuration、payment integration、或 user 額外指定的項目），**不要直接寫進 spec**。改為輸出 `questions.xml` 並依 cafe-workflow-common 暫停給 `user`，請其確認是否例外納入或排除。

### Common rules

- User 確認暫停、交給 `plan` 前是否執行 GitHub sync、以及 baton 順序：請依 shared skill「cafe-workflow-common」的 **Confirming spec and plan with the user**、**Where policies live**，並搭配 `cafe-github_sync` skill；本 skill 不重複敘述。
- 第一次草稿需 user 確認時：把 blackboard `current_step` 改成 `user`，並把 next-step baton 寫入 `user`，不要直接交給 `plan`（其餘細節以 cafe-workflow-common 為準）。
- 後續輪若仍需 user 再看一輪：同樣把 next-step baton 寫入 `user`。
- 若資訊不足，輸出 `questions.xml` 並依 cafe-workflow-common 暫停給 `user`。

## Output
Write spec to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
