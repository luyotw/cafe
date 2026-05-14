---
name: workflow-common
description: Use this skill at the start of any CAFE workflow phase to load the latest workflow handoff from blackboard, identify the current baton state, and ground the phase in the shared workflow context before reading phase-specific artifacts.
version: 1.1.0
---

# Workflow Common

## Purpose
- Apply the shared CAFE workflow discipline before any phase-specific work.
- Treat the blackboard as the primary handoff surface between user turns, chat turns, and workflow phases.

## First Steps
1. Read the latest shared workflow blackboard from the runtime-provided path.
2. Identify the latest handoff summary, relevant recent events, and current workflow step.
3. Only after grounding yourself in the blackboard, continue into phase-specific artifacts and instructions.

## How workflow transitions work

**You do NOT write blackboard.json or next_step.txt.** The runtime does that automatically based on the status code you return in your response.

1. Do your phase work (write output.md, checklist.md, questions.xml, code, etc.).
2. End your response with a single status code line — one of:
   - `await_agent` — hand off to the next playbook step (normal continuation)
   - `confirm_output` — pause for user review/approval
   - `need_clarification` — ask the user a question (must also write questions.xml)
   - `need_permission` — request user permission for a blocked operation
   - `manual_handoff` — hand off to a specific step (runtime decides which)
   - `workflow_complete` — the entire workflow is done
3. The runtime reads your status code, resolves the next step from the playbook, and writes the baton and blackboard for you.

**Never attempt to edit or write `blackboard.json` or `next_step.txt`.** Your tool permissions do not include those files.

## Shared Rules
- Use the blackboard handoff as the default source of current workflow intent.
- If blackboard and older phase artifacts disagree, prefer the latest blackboard handoff, then verify against current artifacts.
- Do not ignore a new user-request handoff just because the previous artifact looks complete.
- `current_step` is the workflow pointer.
- Built-in workflow phases `user` and `done` are valid values when the workflow should pause for the user or end completely.
- Use workflow-defined step names for agent handoff, or built-in targets `user` and `done`.
- Control workflow transitions ONLY via your response status code — never by writing baton/blackboard files directly.

## What Not To Do
- Do not re-explain the shared workflow model in every phase artifact.
- Do not invent a new handoff format outside the status code + runtime mechanism.
- Do not skip the blackboard read just because the phase prompt also includes artifact paths.
- Do not attempt to write or edit `blackboard.json` or `next_step.txt` — you lack permission and the runtime handles this automatically.

## Where policies live (canonical index)

| Concern | Canonical location |
| --- | --- |
| Blackboard-first read, status code transitions, `user` / `done` | This skill (**First Steps**, **How workflow transitions work**) |
| Spec/plan GitHub issue sync (`scripts/sync_github.sh`) | `github_sync` skill (script contract and stdout JSON) |
| PR: local artifact vs remote publish ordering | Generic runtime prompt repeats PR-only lines on purpose; `pr` skill covers PR modes and title/body structure |
| develop ↔ review disagreements and user arbitration | This skill (**Develop and review disagreement protocol**) |

## Confirming spec and plan with the user

- When a **spec** or **plan** draft needs human approval before the next playbook step, return `confirm_output` as your status code. The runtime will pause the workflow for the user. Do not jump straight to `plan` or `develop` while the user still owes a decision.
- After the user has confirmed and you are advancing to the next step, optionally sync the approved artifact to GitHub **when your issue/playbook enables it**: run `scripts/sync_github.sh` with the correct `--phase` and `--output`, consume the JSON on stdout, then return `await_agent`. Command details stay in the `github_sync` skill so phase skills stay short.

## Develop and review disagreement protocol

Follow these in addition to **Shared Rules** whenever you are in **develop** or **review**.

- The runtime prompt includes concrete paths to the blackboard; read it before deciding on your status code.
- **Reasonable feedback:** if the other role's request is technically sound, implement or accept it and return `await_agent` to continue the normal flow.
- **Disagreement:** if you reject the other role's position, first read their full `output.md` and the dispute summary in blackboard `events` before deciding; then write technical reasoning in this iteration's `output.md`. The runtime will record the dispute event on the blackboard based on your status code.
- **First pushback from develop:** return `manual_handoff` with your reasoning — the runtime will route back to review.
- **Round limit:** the same disagreement may go back and forth at most **three** times between develop and review. If the blackboard already shows three rounds without convergence, do **not** return a status code that sends the baton to the other engineering step again.
- **User arbitration:** if you still disagree after the limit (or the issue is product-level), capture both sides in `questions.xml` and return `need_clarification`. The runtime will pause for the user.
- **Normal completion:** when develop work is done and review should run next, return `await_agent`. When review approves, return `await_agent` to proceed to pr unless your playbook says otherwise.
- Avoid infinite loops on the same unresolved point without new information.

## Baton Schema (for reference)

The runtime writes batons using these values. You may see them when reading the blackboard. The runtime auto-corrects common mistakes and logs a `baton_auto_corrected` warning event every time it does so.

### Valid `to_owner` values

| Value | When to use |
| --- | --- |
| `agent` | Next target is an automated workflow step |
| `user` | Pausing for human input (confirmation, clarification, permission) |
| `done` | Workflow is complete; no further steps |

### Valid `intent` values

| Value | When to use |
| --- | --- |
| `await_agent` | Handing off to the next automated step |
| `confirm_output` | Asking the user to approve a spec or plan artifact |
| `need_clarification` | Asking the user a question before proceeding |
| `need_permission` | Requesting a capability or resource the agent cannot self-authorize |
| `manual_handoff` | Pausing for the user to take a manual action |
| `workflow_complete` | Final step finished; workflow ends |

### Auto-correction rules

The runtime normalizes these common mistakes before validation:

| Field | Wrong value(s) | Corrected to | Condition |
| --- | --- | --- | --- |
| `to_owner` | `human`, `reviewer`, `developer` | `user` | always |
| `to_owner` | any | `done` | when `to_step == "done"` |
| `intent` | `complete`, `confirmed`, `done` | `workflow_complete` | when `to_step == "done"` |
| `intent` | `confirmed` | `await_agent` | when `to_step` is a playbook step |
