---
name: cafe-develop_single_review
description: Single native reviewer overlay with per-invocation scope checkpoints.
version: 1.0.0
workflow:
  required_tools: [Agent]
  human_tasks:
    - id: iteration-limit
      pattern: confirm_output
      prompt: Review the retained blocker and decide whether to grant another bounded correction cycle.
      prompt_locales:
        zh-TW: 請檢視保留的阻礙，決定是否授予另一輪有界的修正次數。
      input_schema: decision
      decisions:
        - id: resume
          label: Grant another bounded correction cycle
          label_locales:
            zh-TW: 授予另一輪有界的修正次數
  checklist_overlay:
    variants:
      - when: {}
        sections:
          - reference: single_review_gate.md
---

# Single native review

This overlay applies only where the playbook declares it. It does not replace
or weaken an existing dual-review policy.

After implementation and targeted checks, use the resolved execution context's
`checkpoint_command` with `--boundary before_review`, a unique `--round-id`, the
actual `--parent-id`, and `--output <iteration>/scope_checkpoint.json`. Check
the command's exit status and the explicit `passed` result. Start no reviewer
when scope evidence is unavailable, fails, or belongs to an old authority.

Invoke exactly one independent native subagent, configured read-only and with
the confirmed provider-effective model behavior. Give it the receipt/round ID,
authoritative request, current implementation and targeted test evidence.
Use the provider-native reviewer type supplied in the resolved execution
context (`cafe_reviewer` for the integrated Claude projection). Its available
tools are Read, Glob and Grep; supply Git diffs and targeted results from the
parent. Do not substitute a general-purpose agent that inherits write tools.
Include `CAFE_REVIEW_CHECKPOINT:<receipt_id>` in the native reviewer task prompt
and use the actual parent provider session ID for the checkpoint. Use the
provider's Agent tool-use ID as `reviewer_id`; the host captures the matching
synchronous tool result and verifies it against the phase-owned evidence.
Background-only progress cannot establish completion. Never write or fabricate
the host-owned `native_invocations.json` metadata.
The reviewer covers correctness, completeness, unnecessary changes,
architectural placement and tests. It may inspect but must not edit, commit,
change workflow state or perform external mutations. Self-review is ineligible.

Retain the native invocation ID, effective configuration and explicit terminal
result in `native_review.json`: `version: 1`, `round_id`, `checkpoint`, and one
`invocations` entry containing `parent_id`, `reviewer_id`, `configuration`,
`terminal` (`result` or `turn.completed`), `exit_status`, `findings`,
`targeted_tests` and a durable `result_reference`. Findings contain `severity`
(`blocking` or `nonblocking`) and `detail`. Progress and process exit alone are
not terminal review evidence. Verify the provider's actual behavior; unsupported
native review or missing effective configuration blocks this flow.

Fix blocking findings within approved scope, rerun relevant checks and repeat
the checkpoint and one fresh reviewer. Any relevant content change invalidates
the old receipt and review. Preserve old rounds as history. For a disputed
blocker, supply concrete evidence and obtain a fresh independent conclusion;
never downgrade it unilaterally. No-change reasoning also receives review.
Use the playbook's declared attempt budget, not an unbounded inner loop. If
another round is needed, retain the findings and use the declared development
self-loop so the runtime counts the attempt. Review unavailability, incomplete
terminal evidence or exhausted budget uses the existing human handoff.

Write the evidence file only when it truthfully covers the current content.
The runtime independently recomputes scope and content before accepting an
advancing handoff. Nonblocking findings alone permit advancement.

The native reviewer must return one JSON object with exactly `findings` and
`targeted_tests`. Each finding has `severity` (`blocking` or `nonblocking`) and
`detail`. Copy that independent conclusion into the invocation evidence without
rewriting it. Host-observed conclusions are compared before advancement.

For another correction round, use the explicit declared self-loop baton with
`version: 1`, `to_owner: agent`, `to_step: <current step>` and
`intent: manual_handoff`. Outcome-only success is reserved for `await_agent`.
