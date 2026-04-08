---
name: review
description: "審查程式碼品質與風險"
version: 1.0.0
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}

## Instructions
- 以缺陷與風險為主
- 先確認需求、計畫與實作是否一致
- 優先指出行為回歸、缺少測試與高風險問題
- 若需修改回傳 `CAFE_NEEDS_CHANGES`
- 通過時回傳 `CAFE_CONFIRMED`

## Output
Write review result to: {output_file}

## Status Code
{status_code_instruction}
