---
name: cafe-integrate
description: Human confirmation and reporting policies for verified post-review delivery.
version: 1.0.0
workflow:
  prompt_inputs:
    - {artifacts: [workflow_feedback], placeholder: feedback_file, required: false}
  human_tasks:
    - id: integration-destination
      pattern: confirm_output
      prompt: Confirm the complete destination and accepted source shown in this task. A proposal never authorizes integration.
      prompt_locales:
        zh-TW: 請確認任務所列的完整目的地與已接受的來源。提出候選目的地不代表授權整合。
      input_schema: decision
      decisions:
        - id: confirm
          label: Confirm exact destination
          label_locales: {zh-TW: 確認此目的地}
        - id: revise
          label: Revise destination with cafe integration select
          label_locales: {zh-TW: 使用 cafe integration select 修改目的地}
    - id: human-integration
      pattern: confirm_output
      prompt: A human must integrate the approved PR or feature commit into the exact destination shown below. Report performed, already performed, or blocked, with an optional work report. CAFE only inspects read-only evidence; reporting alone cannot complete delivery. Resolve conflicts yourself and renew review if the source changes.
      prompt_locales:
        zh-TW: 請由人將下列已接受的 PR 或 feature commit 整合到指定目的地，並回報已執行、先前已執行或受阻，可附工作回報。CAFE 只會讀取驗證證據，單憑回報無法完成交付。衝突須由人處理，來源變更須重新審查。
      input_schema: decision
      decisions:
        - id: performed
          label: Human integration performed
          label_locales: {zh-TW: 人已執行整合}
        - id: already_performed
          label: Already integrated by a human
          label_locales: {zh-TW: 人先前已完成整合}
        - id: blocked
          label: Blocked; human correction required
          label_locales: {zh-TW: 受阻，需要人處理}
---

# Verified Human Integration

These policies are bound by the playbook's optional `integration` declaration.
The runtime supplies immutable review/destination identities and performs bounded
read-only inspection. This skill runs no agent, merge, fetch, push or teardown.

Use `cafe integration select` to stage a candidate, and `cafe task inspect` to
obtain the exact confirmation/report contract. Report through `cafe task complete`.
Use `cafe integration status` for remaining work, `cafe integration verify` to
retry inspection after human correction, and `cafe workflow --execute` to resume.
A changed reviewed source follows the declared correction/review route.
