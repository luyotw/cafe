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

## Where policies live (canonical index)

| Concern | Canonical location |
| --- | --- |
| Blackboard-first read, baton hygiene, `user` / `done` | This skill (**First Steps**, **Shared Rules**) |
| Spec/plan GitHub issue sync (`scripts/sync_github.sh`) | `github_sync` skill (script contract and stdout JSON) |
| PR: local artifact vs remote publish ordering | Generic runtime prompt repeats PR-only lines on purpose; `pr` skill covers PR modes and title/body structure |
| develop ↔ review disagreements and user arbitration | This skill (**Develop and review disagreement protocol**) |

## Confirming spec and plan with the user

- When a **spec** or **plan** draft needs human approval before the next playbook step, pause for the user: align blackboard `current_step` and the baton with `user` per **Shared Rules**. Do not jump straight to `plan` or `develop` while the user still owes a decision.
- After the user has confirmed and you are advancing to the next step, optionally sync the approved artifact to GitHub **when your issue/playbook enables it**: run `scripts/sync_github.sh` with the correct `--phase` and `--output`, consume the JSON on stdout, then move the baton. Command details stay in the `github_sync` skill so phase skills stay short.

## Develop and review disagreement protocol

Follow these in addition to **Shared Rules** whenever you are in **develop** or **review**.

- The runtime prompt includes concrete paths to the blackboard and `next_step` baton; read them before changing workflow state.
- `blackboard.current_step` is the workflow pointer; you may set it to the built-in `user` phase when pausing for a human.
- **Reasonable feedback:** if the other role’s request is technically sound, implement or accept it without extra handoff drama.
- **Disagreement:** if you reject the other role’s position, first read their full `output.md` and the dispute summary in blackboard `events` before deciding; then write technical reasoning in this iteration’s `output.md` and append a short dispute summary to blackboard `events`.
- **First pushback from develop:** set blackboard `current_step` to `review` and write the baton target `review`.
- **Round limit:** the same disagreement may go back and forth at most **three** times between develop and review. If the blackboard already shows three rounds without convergence, do **not** send the baton to the other engineering step again.
- **User arbitration:** if you still disagree after the limit (or the issue is product-level), capture both sides in `questions.xml`, record whether the developer or reviewer requested arbitration in blackboard `events`, set `current_step` to `user`, and write the baton target `user`.
- **Normal completion:** when develop work is done and review should run next, set the baton to `review`. When review approves the default playbook path, set the baton to `pr` unless your playbook says otherwise.
- Avoid infinite loops on the same unresolved point without new information.
