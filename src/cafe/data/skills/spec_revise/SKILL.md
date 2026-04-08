---
name: spec_revise
description: "依回饋修訂需求規格"
version: 1.0.0
---

# Spec Revise

## Role
Read your agent file: {agent_file}

## Context
{blackboard_digest}

## Instructions
- 讀取上一版 spec 輸出與使用者回饋
- 修訂內容並寫回指定輸出檔
- 若仍缺資訊，回傳 `CAFE_NEED_CLARIFICATION`

## Output
Write spec to: {output_file}

## Status Code
{status_code_instruction}
