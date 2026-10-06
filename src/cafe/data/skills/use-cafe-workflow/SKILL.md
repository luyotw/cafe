---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including passive supervision, bounded recovery, and declarative repair when execution leaves its safe operating envelope.
metadata: {version: 1.73.0}
---

# Use CAFE Workflow

## Purpose

Drive the selected playbook's effective graph without bypassing its artifacts,
baton, confirmed Manager contract, user-owned decisions, or action-specific
authority. Prefer non-interactive commands so execution remains durable and
reconstructible across Manager sessions.

## Progressive disclosure

Read this file completely, then load only the references for the current
decision. Resolve paths relative to this `SKILL.md`. When several rows apply,
read the union once; do not preload the rest.

| Current decision | Read before acting |
| --- | --- |
| Check or apply runtime, catalog, or bundled-helper updates | `references/project_global_skill_sync.md` |
| Select a playbook for a new kickoff | `references/playbook_selection.md` |
| Prepare kickoff inputs, render, or reconfirm a kickoff | First `references/playbook_selection.md`; compact mode then loads `references/compact_kickoff.md`; full mode loads `references/kickoff_inputs.md`, `references/kickoff.md`, `references/model_selection.md`, `references/strategic_context.md` |
| Write or change confirmed phase chains after confirmation | `references/model_selection.md`, then `references/phases_yaml.md` |
| Prepare and activate after kickoff confirmation | `references/kickoff_execution.md` |
| Start, resume, or supply declared input to ordinary execution | `references/project_global_skill_sync.md`, then `references/running_workflow.md` |
| Supervise active work or classify a pause, timeout, interruption, retry, or recovery | `references/supervision_and_recovery.md`; read `references/running_workflow.md` only when its disposition permits a retry/resume, and `references/diagnosis_and_repair.md` only for incorrect or ambiguous behavior |
| Handle a HumanTask, confirmation, clarification, permission, alignment, or scheduled proactive review | `references/handoffs_and_alignment.md` and `references/strategic_context.md`; also read the proactive-review section of `references/running_workflow.md` when a configured review is due |
| Receive an issue split proposal from any step, or start/resume linked work | `references/issue_decomposition.md`, `references/strategic_context.md`, and `references/handoffs_and_alignment.md` |
| Diagnose or repair a playbook, phase, Manager, or runtime defect | `references/diagnosis_and_repair.md` plus the reference for the failing boundary |
| Consider direct closeout, verify completion, handle a Git delivery conflict, or handle follow-up work | `references/completion_and_authority.md` |
| Present the initial kickoff | Compact: `references/compact_kickoff.md`; full: `references/kickoff.md`; both: `references/workflow_progress.md` |
| Render any other user-visible question, progress, error, or completion reply | `references/workflow_progress.md` |
| Measure fresh-versus-resumed correction efficiency | `references/correction_ab_experiment.md` |

For a new kickoff, start with `prepare_kickoff.py draft --issue-id <id> --output
<draft.json>` (or `--issue-name <name>`). Pass an already selected playbook via
`--playbook-id` and explicit model overrides via repeated `--phase-chain`.
The program creates the request and prefills reusable values before you edit it;
do not write a starter request JSON yourself. If the playbook is undecided,
select from the returned discovery and reassemble the same request into an
updated draft. Read the generated draft and fill its unresolved fields, retaining
the prefilled values unless current requirements demand an override. Keep the
same preference/evidence stores throughout, capture the required check reports
and render the complete proposal. Invalid fields block rendering while remaining
editable alongside the preserved values; repair those fields instead of
reconstructing the request. Validated evidence
can be reused for the facts it covers. Existing workflows retain their confirmed
contract; new preferences never rewrite it.

## Operating sequence

1. Inspect durable status, current workflow/step/iteration/session identity,
   pending tasks, baton, and active process ownership. Never act from chat memory
   alone.
2. If the issue lacks a current confirmed contract, perform the kickoff route.
   The complete kickoff contract is the first blocking gate: do not run
   `cafe prepare`, mutate the repository, or execute a phase before confirmation.
3. Prepare in the confirmed worktree and persist only through each owning
   contract. Configure phase chains before their first execution.
4. Start or resume only through `scripts/run_workflow.py` as specified by
   `references/running_workflow.md`, under the confirmed operating mode and
   persisted baton. Never reconstruct the `cafe workflow` arguments from prose.
5. While work is active, apply the non-intervention envelope in
   `references/supervision_and_recovery.md`. The continuous workflow worker owns
   ordinary advancement.
6. At an existing pause, route the exact active task through its declared owner
   and schema. At the declared terminal state, use the completion route.

## Always-on boundaries

- You are the primary Manager: use your current CLI/session as the primary
  `--event-manager`; phase model choices do not change your Manager identity.
- During ordinary execution, the Manager observes process and durable workflow
  state only. Do not use `cafe chat`, inspect implementation code or diffs, do
  phase work, manually resume/select a step, or mutate workflow state merely to
  supervise.
- Route every HumanTask through its declared owner and schema. The Manager never
  infers or supplies a user-owned answer; Manager-owned exceptions exist only
  where the confirmed task contract explicitly grants them.
- Default new kickoff proposals to overall `need_clarification: manager_confirmable`.
  Offer phase/task overrides only when the user requests finer control. Explicit
  task ownership takes precedence; answers still require evidence within the
  confirmed scope, constraints and authority. Existing contracts retain their
  confirmed policy and never acquire this default merely by being read.
- Treat a wrapper directive with `action: yield` as terminal for the current
  Manager turn. Do not poll the background worker after that directive.
- For an initial kickoff confirmation request, present the complete stdout of
  `scripts/format_kickoff_contract.py` in the effective conversation language
  instead of replacing it with a prose summary. Translate presentation text
  without changing literal commands, identifiers, or policy semantics; follow `kickoff.md` for that
  boundary. Its readable contract covers the user's decisions once; internal
  policy JSON and diagnostic metadata are not part of the response. The
  formatter owns the single confirmation prompt and final progress block;
  retain its project-preference section and the distinct confirm-only versus
  confirm-and-remember replies in the final message. A question sent while
  preparing the contract never replaces that visible choice;
  do not append a second request or diagram. For every other user-visible reply,
  end with the diagram from `scripts/render_workflow_progress.py`, following
  `workflow_progress.md`. Rendering is read-only and never justifies polling,
  resuming, confirming, or performing an external action.
- Follow only the effective graph, confirmed contract, and action-specific
  authority. “Continue” or workflow completion grants no repair, merge, deploy,
  publish, close, delete, cleanup, or other external mutation authority. The
  closeout exceptions are an exact `closeout_plan` argv array confirmed as part
  of the complete Delivery Contract, or `cafe close --archive-only` after the
  user explicitly selects terminal archive; both execute only through
  `completion_and_authority.md`.
