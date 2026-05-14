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

## Baton Schema

The baton contract (`next_step.txt`) is a JSON object with the following fields. Write only valid enum values — the runtime auto-corrects common mistakes but logs a `baton_auto_corrected` warning event every time it does so.

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

### Example JSON for common transitions

**Agent → next automated step (e.g. develop → review)**
```json
{
  "version": 1,
  "from_step": "develop",
  "to_owner": "agent",
  "to_step": "review",
  "intent": "await_agent",
  "status_code": "",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "develop"
}
```

**Agent → user for output confirmation (spec or plan)**
```json
{
  "version": 1,
  "from_step": "plan",
  "to_owner": "user",
  "to_step": "user",
  "intent": "confirm_output",
  "status_code": "",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "plan"
}
```

**Agent → user for clarification**
```json
{
  "version": 1,
  "from_step": "spec",
  "to_owner": "user",
  "to_step": "user",
  "intent": "need_clarification",
  "status_code": "",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "spec"
}
```

**Agent → user for permission**
```json
{
  "version": 1,
  "from_step": "develop",
  "to_owner": "user",
  "to_step": "user",
  "intent": "need_permission",
  "status_code": "",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "develop"
}
```

**Agent → done (workflow complete)**
```json
{
  "version": 1,
  "from_step": "pr",
  "to_owner": "done",
  "to_step": "done",
  "intent": "workflow_complete",
  "status_code": "confirmed",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "pr"
}
```

### Auto-correction rules

The runtime normalizes these common mistakes before validation and logs a `baton_auto_corrected` warning event with the original and corrected values:

| Field | Wrong value(s) | Corrected to | Condition |
| --- | --- | --- | --- |
| `to_owner` | `human`, `reviewer`, `developer` | `user` | always |
| `to_owner` | any | `done` | when `to_step == "done"` |
| `intent` | `complete`, `confirmed`, `done` | `workflow_complete` | when `to_step == "done"` |
| `intent` | `confirmed` | `await_agent` | when `to_step` is a playbook step |
