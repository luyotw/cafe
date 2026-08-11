---
name: cafe-workflow-common
description: Use this skill at the start of any CAFE workflow phase to load the bounded workflow digest, identify the current baton state, and ground the phase in shared context before reading phase-specific artifacts.
version: 1.5.0
---

# Workflow Common

## Purpose
- Apply the shared CAFE workflow discipline before any phase-specific work.
- Treat the blackboard as the primary handoff surface between user turns, chat turns, and workflow phases.

## First Steps
1. Read the runtime-provided **Bounded blackboard digest** and latest handoff summary.
2. Identify the current workflow step, baton, artifact pointers, and recent event summaries from that digest.
3. Continue into phase-specific artifacts and instructions after this bounded grounding pass.

The full `blackboard.json` is an unbounded audit history. Do **not** read or print the whole file during normal phase startup. If a concrete conflict requires older history, query only the exact field or matching event summaries needed. Read a full event payload only when the bounded summary is insufficient to resolve a specific disagreement.

## How workflow transitions work

You control the next workflow step by writing a **baton** — a JSON object written to the runtime-provided `next_step.txt` path. The runtime reads your baton to decide where to go next.

1. Do your phase work (write output.md, checklist.md, questions.xml, code, etc.).
2. Write the baton to `next_step.txt` with the correct `to_owner`, `to_step`, and `intent`.
3. The runtime reads your baton and transitions accordingly.

If you do NOT write a baton, the runtime falls back to your response's status code to derive a transition — but this is less precise and may not match your intent. **Always write the baton for precise control.**

## Baton Schema

Write a JSON object to `next_step.txt` with these fields:

```json
{
  "version": 1,
  "from_step": "<current step name>",
  "to_owner": "<agent|user|done>",
  "to_step": "<target step name or user or done>",
  "intent": "<intent value>",
  "status_code": "",
  "created_at": "<ISO 8601 timestamp>",
  "source": "<from_step>"
}
```

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
| `no_changes_needed` | Declaring no further edits are required and handing off per playbook |
| `manual_handoff` | Explicitly routing to a non-default next step |
| `workflow_complete` | Final step finished; workflow ends |

### Invalid baton values are rejected

If you write an invalid `to_owner` or `intent` value, the runtime will **reject** the baton and ask you to rewrite it with a correct value. You will be re-invoked with a feedback message telling you which field was wrong and what the valid values are. After 3 failed attempts the workflow will crash.

**Common mistakes to avoid:**
| Field | Wrong value | Correct value |
| --- | --- | --- |
| `to_owner` | `human`, `reviewer`, `developer` | `user` |
| `intent` | `complete`, `done` | `workflow_complete` |
| `intent` | `confirmed` | `await_agent` |

### Example batons for common transitions

**Agent → next automated step (e.g. spec → plan)**
```json
{
  "version": 1,
  "from_step": "spec",
  "to_owner": "agent",
  "to_step": "plan",
  "intent": "await_agent",
  "status_code": "",
  "created_at": "2026-05-14T10:00:00+08:00",
  "source": "spec"
}
```

**Agent → user for output confirmation**
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

## Shared Rules
- Use the blackboard handoff as the default source of current workflow intent.
- If blackboard and older phase artifacts disagree, prefer the latest blackboard handoff, then verify against current artifacts.
- Do not ignore a new user-request handoff just because the previous artifact looks complete.
- `current_step` is the workflow pointer.
- Built-in workflow phases `user` and `done` are valid values when the workflow should pause for the user or end completely.
- Use workflow-defined step names for agent handoff, or built-in targets `user` and `done`.
- Control workflow transitions by writing the baton — this is the precise control surface.

## Bounded repository inspection

- Locate candidate files with `rg -l` or a path-limited `rg` before printing matching lines. Then inspect only the relevant files with at most 3–5 lines of context.
- Keep each read or search result below roughly 200 lines and 32 KiB. Narrow the path or pattern before reading more; do not raise the cap merely because the first query was broad.
- Exclude generated runtime data from repository searches. Never include `streaming.jsonl` in a normal search or read it to recover context already available in the bounded digest, artifacts, or iteration metadata.
- Do not search `.cafe/` together with source and test trees. Query a specific issue artifact or metadata file only when the current handoff cannot be resolved from the runtime-provided paths.
- Do not read a phase's own `streaming.jsonl` or full unbounded blackboard as a progress summary. Those are diagnostic evidence for a concrete failure, not normal workflow context.

## Develop-to-review verification receipts

- The develop phase records its final repository-defined full test result with `cafe verification run`. The receipt is iteration-local, machine-generated, and valid only for the clean Git HEAD that was tested.
- The review phase validates that evidence with `cafe verification check` and confirms the reported command is the repository-defined full suite required by the plan and test policy. A valid full receipt replaces a duplicate run of the same full suite or coverage command; it does not replace static review or requirement checks. For a concrete review risk, `cafe verification focus` accepts only a direct pytest receipt plus relative test-file or node-id selectors; it rejects shell/script runners and never replaces or edits the full receipt.
- A missing, failed, stale, wrong-scope, or dirty-worktree receipt is not reusable. Review routes the exact problem back to develop instead of creating or repairing develop evidence itself.
- Develop may run targeted tests and failing full attempts while changing code. It creates the reusable final full receipt only after all tracked changes are committed. If that run fails, fix and commit before replacing the receipt; after a passing receipt, do not change HEAD or tracked files.

## Risk-driven operation decisions

- Before launching a long-running operation, the agent records `risk`, `monitoring`, `log_policy`, a stop condition, and recovery guidance in the existing operation artifact.
- Use `low` / `final-only` / `summary-only` for local checks, `medium` / `periodic` / `incremental-tail` for replayable integration work, and `high` / `active` / `filtered-stream` for difficult-to-reverse work.
- Runtime commands only persist and inspect this decision. They do not schedule polling, choose when to read logs, or relaunch an existing operation.

## What Not To Do
- Do not re-explain the shared workflow model in every phase artifact.
- Do not invent a new handoff format outside the baton mechanism.
- Do not read the full `blackboard.json` as an initial discovery step; use the runtime-provided bounded digest.
- Do not repeat the same blackboard query when neither the baton nor relevant files have changed.
- Do not write `blackboard.json` — only write `next_step.txt`. The runtime updates the blackboard based on your baton.
- Do not use status codes in your response text as the primary transition mechanism — write the baton instead.
- Do not invoke high-level workflow-driving skills (for example `use-cafe-workflow`) inside a running phase. Follow only the shared skills and phase skill already listed by the runtime prompt.

## Where policies live (canonical index)

| Concern | Canonical location |
| --- | --- |
| Blackboard-first read, baton-first transitions, `user` / `done` | This skill (**First Steps**, **How workflow transitions work**, **Baton Schema**) |
| Spec/plan GitHub issue sync (`scripts/sync_github.sh`) | `cafe-github_sync` skill (script contract and stdout JSON) |
| PR: local artifact vs remote publish ordering | Generic runtime prompt repeats PR-only lines on purpose; `cafe-pr` skill covers PR modes and title/body structure |
| develop ↔ review disagreements and user arbitration | This skill (**Develop and review disagreement protocol**) |
| Bounded code/search output and generated-log exclusions | This skill (**Bounded repository inspection**) |
| Reuse of final full-test evidence across develop → review | This skill (**Develop-to-review verification receipts**) |
| Issue decomposition assessment contract and phase-agent boundary | `references/issue_decomposition.md` |

## Confirming spec and plan with the user

- When a **spec** or **plan** draft needs human approval before the next playbook step, write a baton with `to_owner: "user"`, `to_step: "user"`, `intent: "confirm_output"`. Do not jump straight to `plan` or `develop` while the user still owes a decision.
- After the user has confirmed and you are advancing to the next step, optionally sync the approved artifact to GitHub **when your issue/playbook enables it**: run `scripts/sync_github.sh` with the correct `--phase` and `--output`, consume the JSON on stdout, then write a baton with `intent: "await_agent"` targeting the next step.

## Develop and review disagreement protocol

Follow these in addition to **Shared Rules** whenever you are in **develop** or **review**.

- The runtime prompt includes the bounded digest plus concrete paths to the blackboard and baton. Use the digest and read the small baton file before writing a new baton; keep the full blackboard path for selective diagnostics only.
- **Reasonable feedback:** if the other role's request is technically sound, implement or accept it and write a baton targeting the next step (e.g. develop → review, review → pr).
- **Disagreement:** if you reject the other role's position, first read their full `output.md` and selectively query the matching dispute event summaries before deciding. Read a matching event's full payload only if its summary lacks the necessary technical detail. Then write technical reasoning in this iteration's `output.md` and a baton routing back to the other engineering step.
- **First pushback from develop:** write a baton with `to_owner: "agent"`, `to_step: "review"`, `intent: "manual_handoff"`.
- **Round limit:** the same disagreement may go back and forth at most **three** times between develop and review. If the blackboard already shows three rounds without convergence, do **not** write a baton targeting the other engineering step again.
- **User arbitration:** if you still disagree after the limit (or the issue is product-level), capture both sides in `questions.xml` and write a baton with `to_owner: "user"`, `intent: "need_clarification"`.
- **Normal completion:** when develop work is done and review should run next, write `to_step: "review"`, `intent: "await_agent"`. When review approves, hand off to the step that comes next **in your playbook** — use the runtime prompt's `valid to_step values` and `this step's defined transitions`, not a hardcoded name. In the standard spec→plan→develop→review→pr pipeline that next step is `pr`; other playbooks may differ, or it may be `done` if review/the current step is the last one. Never write a `to_step` that is not in the prompt's valid list.
- Avoid infinite loops on the same unresolved point without new information.
