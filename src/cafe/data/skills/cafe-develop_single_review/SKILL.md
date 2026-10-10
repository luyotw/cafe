---
name: cafe-develop_single_review
description: Single native reviewer overlay with per-invocation scope checkpoints.
version: 1.0.2
workflow:
  required_tools: [Agent]
  human_tasks:
    - id: iteration-limit
      pattern: confirm_output
      prompt: {message_key: human_task.single_review.iteration_limit.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.single_review.iteration_limit.prompt}
      input_schema: decision
      decisions:
        - id: resume
          label: {message_key: human_task.single_review.iteration_limit.resume}
          label_locales:
            zh-TW: {message_key: human_task.single_review.iteration_limit.resume}
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
context's `native_reviewer_type`) and follow `native_review_instructions` for
its native tool arguments, identity and completion events. The projected
reviewer definition restricts its native tools or permissions to inspection;
supply Git diffs and targeted results from the parent. Do not substitute a
general-purpose agent that inherits write tools or start another CLI session.
Include `CAFE_REVIEW_CHECKPOINT:<receipt_id>` in the native reviewer task prompt
and use the actual parent provider session ID for the checkpoint. Record the
provider-native child or tool-call identity specified by the execution context
as `reviewer_id`; the host correlates native delegation and its terminal result.
Do not add model or permission overrides unless the projected instructions
explicitly require the confirmed reviewer model.
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
