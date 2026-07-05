---
name: cafe-chat-alignment-decision
description: "Guide a CAFE alignment checkpoint conversation inside cafe chat"
version: 1.0.0
---

# Chat Alignment Decision

## Use This Skill When
- `CAFE_CHAT_MODE=alignment` is present in the environment.
- The user is discussing an `alignment_checkpoint` inside `cafe chat`.

## Purpose
- Help the user understand why the workflow paused for alignment.
- Discuss whether the current issue should continue, narrow scope, revise spec/plan,
  update strategic documents first, pause, reject, or defer.
- Produce a structured decision proposal for CAFE to validate after chat exits.

## Runtime Inputs
- Read `CAFE_ALIGNMENT_REQUEST_FILE` for the policy-generated checkpoint payload.
- Write the final decision to `CAFE_ALIGNMENT_DECISION_FILE` only after the user has
  clearly chosen a direction.
- Treat `CAFE_ALIGNMENT_FROM_STEP`, `CAFE_ISSUE_NAME`, and `CAFE_ISSUE_DIR` as
  workflow context.

## Rules
- Do not approve or resume the workflow by editing the blackboard directly.
- Do not write `next_step.txt` for alignment decisions.
- Do not treat alignment approval as permission to execute host-side capabilities.
- Use the request payload's `allowed_decisions` when recommending choices.
- If strategic documents are missing or stale, explain the tradeoff before suggesting
  approval.
- If the user wants strategic docs updated first, treat that as the start of a
  strategic alignment conversation, not as approval of document content.
- Before finalizing high-level strategy documents, ask at least one concrete
  alignment question about product direction, positioning, audience, non-goals,
  principles, or roadmap tradeoffs.
- You may draft or update the relevant strategic document files in this chat, but
  draft content is not final until the user explicitly confirms it after seeing
  the draft or a concrete summary of the final content.
- Decision mapping in chat:
  - User chooses "update docs first" / option 2: ask alignment questions and draft
    or revise the documents. This choice alone is not content confirmation.
  - User explicitly confirms the final strategic document content after review:
    final JSON decision may be `strategic_documents_updated`.
  - User chooses to pause without edits: final JSON decision is
    `update_strategic_documents_first`.
- When drafting a configured missing strategic document without final user
  confirmation, keep `.cafe/strategic_context.yaml` status as `draft` or
  `missing`; do not mark it `exists`.
- When the user explicitly confirms the final strategic document content, update
  `.cafe/strategic_context.yaml` so confirmed documents have `status: exists` and
  `path` points to the file.
- After confirmed strategic documents are updated, write
  `strategic_documents_updated` with confirmation evidence in the JSON payload.
- Write `update_strategic_documents_first` only when the user explicitly wants to
  pause without having you edit the documents now, or when the documents cannot be
  safely updated in this chat.

## Decision Payload
Write JSON to `CAFE_ALIGNMENT_DECISION_FILE`:

```json
{
  "decision": "approve",
  "reason": "Short rationale for the decision.",
  "correction": "Optional scope/spec/plan guidance for narrow_scope, revise_spec, revise_plan, manual_pause, or reject_or_defer.",
  "user_confirmed": false,
  "user_confirmation": ""
}
```

For `strategic_documents_updated` from chat, `user_confirmed` must be `true` and
`user_confirmation` must summarize the user's explicit confirmation after they
reviewed the final draft or concrete final-content summary. Do not set these
fields merely because the user selected option 2.

Allowed `decision` values:
- `approve`
- `narrow_scope`
- `revise_spec`
- `revise_plan`
- `update_strategic_documents_first`
- `strategic_documents_updated`
- `manual_pause`
- `reject_or_defer`

## Closing
- Tell the user to exit chat and run `cafe make`.
- Explain that CAFE will validate and apply the decision after chat exits.
