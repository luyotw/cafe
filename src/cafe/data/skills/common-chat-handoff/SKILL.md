---
name: common-chat-handoff
description: "Shared chat workflow handoff rules for all agent roles"
version: 1.0.0
---

# Common Chat Handoff

## Purpose
- Use this shared skill whenever a user is working through project decisions inside `cafe chat`.
- Treat chat as part of the existing CAFE workflow, not as a side channel.

## When To Apply
- Use this skill together with the relevant chat skill for develop changes, spec revisions, or plan revisions.
- Any agent role may need this skill.

## Rules
- Keep the conversation grounded in the existing project workflow.
- If work is completed directly in chat, say so clearly.
- If the discussion changes spec or plan expectations, say which artifact changed.
- CAFE will provide the exact blackboard path and next-step file path at runtime.
- Before you print a workflow handoff closing block, first update the shared blackboard directly.
- Preserve valid JSON when editing the blackboard. Do not replace the whole file with prose.
- Append a concise natural-language handoff event to `events`.
- Use blackboard `current_step` to show where the workflow should go after chat.
- If this chat decides the next responsible phase, update blackboard `current_step` to that phase.
- If the workflow should wait for the user, set blackboard `current_step` to `user`.
- If the workflow is fully complete, set blackboard `current_step` to `done`.
- Write the same phase name into the provided next-step file when you want `cafe make` to consume it immediately.
- The next-step file must contain only one valid workflow step name, with no explanation around it.
- Choose the next responsible step after this chat. If you updated spec, hand off to planning. If you updated plan, hand off to development. If you updated code, hand off to review or the next downstream step allowed by the workflow.
- `user` and `done` are valid built-in handoff phases.
- Do not send the user to phase-specific commands.
- Only produce the required closing format when you are explicitly wrapping up the chat, summarizing completed work, or helping the user leave the session.
- During normal back-and-forth conversation, answer naturally and do not append the closing format to every reply.
- When you do produce a workflow-related handoff, end by telling the user to exit chat and run `cafe make`.

## Required Closing Format
- `Handled in chat: yes|no`
- `What changed: ...`
- `Workflow status: code updated | spec updated | plan updated | follow-up needed`
- `Next step: exit chat and run cafe make`
