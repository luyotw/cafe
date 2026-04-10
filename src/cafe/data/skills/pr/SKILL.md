---
name: pr
description: "整理提交內容並產出 pull request 標題與描述"
version: 1.0.0
---

# PR

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Commits
{commits}

## Instructions
- 閱讀需求規格、實作計畫與目前分支上的 commits
- 編輯指定的 PR 輸出檔，產出 PR title 與 description
- Title 必須放在第一行 `#` 標題，內容需精簡清楚，且不超過 80 字元
- Body 必須維持 `Summary`、`Changes`、`Test Plan` 結構
- `Summary` 需引用原始需求背景
- `Changes` 需列出主要變更與相關 commits
- `Test Plan` 需描述具體驗證方式
- blackboard `current_step` 代表目前 workflow 接下來要去哪個 phase。
- 如果 PR 文案已是最新、沒有新的 branch 進度需要整理，而且接下來應由 user 決定是否還有後續需求：
  - 把 blackboard `current_step` 改成 `user`
  - 在 blackboard summary/event 說明「PR 已是最新，等待 user 決定下一步」
  - 寫入 next-step baton，內容只放 `user`
- 不要在回應中重述內容，只回傳狀態碼

## Output
Write PR content to: {output_file}

## Status Code
{status_code_instruction}
