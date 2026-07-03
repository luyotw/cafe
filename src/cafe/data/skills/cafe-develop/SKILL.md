---
name: cafe-develop
description: "依計畫進行程式開發與測試"
version: 1.0.0
---

# Develop

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Instructions
- 依計畫逐項完成
- 先補測試再改程式
- 新增或修改的測試必須對應計畫 **Test List** 項目（範圍變更時先更新計畫）
- 斷言以 invariant 為主：避免綁定 UI copy、CSS class、DOM 結構、內部 state shape；允許 a11y role/label、`data-testid`、以及規格明訂的文案（見 `cafe-plan/references/test_invariants_policy.md`）
- 每輪完成後更新 checklist
- 維持既有 commit 風格與程式碼註解語言
- 優先重用現有模式與工具
- 與 reviewer 往返、blackboard/baton 更新、以及 user 仲裁等跨 phase 規則：請依 shared skill「cafe-workflow-common」的 **Develop and review disagreement protocol** 與 **Shared Rules**；本 skill 不重複敘述。

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
