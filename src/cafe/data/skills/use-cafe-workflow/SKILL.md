---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including passive supervision, bounded recovery, and declarative repair when execution leaves its safe operating envelope.
metadata: {version: 1.71.1}
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
| Select a playbook for a new kickoff | Use the preparation route below; its owner guidance includes selection policy |
| Prepare kickoff inputs, render, or reconfirm a kickoff | Start with `references/kickoff_inputs.md` and its early summary command; use `assemble --summary --guidance-output <guide.md> --draft-output <draft.json>` for the current `references/kickoff.md` and other owner sections plus editable inputs; load other owner sections only for uncovered decisions |
| Write or change confirmed phase chains after confirmation | `references/model_selection.md`, then `references/phases_yaml.md` |
| Start, resume, or supply declared input to ordinary execution | `references/project_global_skill_sync.md`, then `references/running_workflow.md` |
| Supervise active work or classify a pause, timeout, interruption, retry, or recovery | `references/supervision_and_recovery.md`; read `references/running_workflow.md` only when its disposition permits a retry/resume, and `references/diagnosis_and_repair.md` only for incorrect or ambiguous behavior |
| Handle a HumanTask, confirmation, clarification, permission, alignment, or scheduled proactive review | `references/handoffs_and_alignment.md` and `references/strategic_context.md`; also read the proactive-review section of `references/running_workflow.md` when a configured review is due |
| Receive an issue split proposal from any step, or start/resume linked work | `references/issue_decomposition.md`, `references/strategic_context.md`, and `references/handoffs_and_alignment.md` |
| Diagnose or repair a playbook, phase, Manager, or runtime defect | `references/diagnosis_and_repair.md` plus the reference for the failing boundary |
| Consider direct closeout, verify completion, handle a Git delivery conflict, or handle follow-up work | `references/completion_and_authority.md` |
| Present the initial kickoff | Use the rendered contract and the preparation guidance's presentation section |
| Render any other user-visible question, progress, error, or completion reply | `references/workflow_progress.md` |
| Measure fresh-versus-resumed correction efficiency | `references/correction_ab_experiment.md` |

For a new kickoff, keep inherited preference/evidence stores separate from temporary
proposal outputs. First use `prepare_kickoff.py stores --request-file <request.json>`
and execute its path-pinned `next_command`; intentional store overrides remain
explicit. See `references/kickoff_inputs.md` for the request and store-selection
interface. Obtain the local preparation summary before loading candidate
playbooks, phase SKILL bodies, model research or delivery documentation. If a
playbook is already explicitly chosen, start with `assemble --summary --guidance-output <guide.md> --draft-output <draft.json>` even
while incomplete: it supplies the selected graph, validated evidence, a draft
and missing decisions. Otherwise use `discover --summary` for the full candidate
set. Assess current scope, strategy, suitability and authority against that
evidence. The first guided summary includes the current strategy and suitability owner
sections. Apply them once; inspect source details only for missing or invalidated
facts. Do not preload the full kickoff/model/strategy references before this
summary or reread their projected sections. Fill the emitted typed draft and
render it directly once decisions and complete preflight reports are available. A validated selected graph supplies resolved
profiles and gates without rereading every phase body. Reuse references already
read in this preparation. Resume continues to use its confirmed contract.

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
