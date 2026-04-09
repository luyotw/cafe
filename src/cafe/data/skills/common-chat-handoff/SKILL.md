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
- Do not send the user to phase-specific commands.
- Only produce the required closing format when you are explicitly wrapping up the chat, summarizing completed work, or helping the user leave the session.
- During normal back-and-forth conversation, answer naturally and do not append the closing format to every reply.
- When you do produce a workflow-related handoff, end by telling the user to exit chat and run `cafe make`.

## Required Closing Format
- `Handled in chat: yes|no`
- `What changed: ...`
- `Workflow status: code updated | spec updated | plan updated | follow-up needed`
- `Next step: exit chat and run cafe make`
