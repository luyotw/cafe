---
name: workflow-common
description: Use this skill at the start of any CAFE workflow phase to load the latest workflow handoff from blackboard, identify the current baton state, and ground the phase in the shared workflow context before reading phase-specific artifacts.
version: 1.0.0
---

# Workflow Common

## Purpose
- Apply the shared CAFE workflow discipline before any phase-specific work.
- Treat the blackboard as the primary handoff surface between user turns, chat turns, and workflow phases.

## First Steps
1. Read the latest shared workflow blackboard from the runtime-provided path.
2. Identify the latest handoff summary, relevant recent events, and current workflow step.
3. Read the runtime-provided baton path if you need to understand or update the next workflow target.
4. Only after grounding yourself in the blackboard, continue into phase-specific artifacts and instructions.

## Shared Rules
- Use the blackboard handoff as the default source of current workflow intent.
- If blackboard and older phase artifacts disagree, prefer the latest blackboard handoff, then verify against current artifacts.
- Do not ignore a new user-request handoff just because the previous artifact looks complete.
- Keep blackboard updates concise and factual.
- Preserve valid JSON when updating the blackboard.
- `current_step` is the workflow pointer.
- Built-in workflow phases `user` and `done` are valid values when the workflow should pause for the user or end completely.
- When you explicitly hand off to another step in the current run, write only that step name into the runtime-provided next-step file.
- Use workflow-defined step names for agent handoff, or built-in targets `user` and `done`.
- Do not use response text status codes to control workflow transitions. The next-step baton is the control surface.

## What Not To Do
- Do not re-explain the shared workflow model in every phase artifact.
- Do not invent a new handoff format outside blackboard and the baton file.
- Do not skip the blackboard read just because the phase prompt also includes artifact paths.
