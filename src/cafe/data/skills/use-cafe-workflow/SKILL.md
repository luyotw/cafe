---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including bounded diagnosis and declarative repair when the workflow behaves incorrectly.
metadata: {version: 1.31.0}
---

# Use CAFE Workflow

## Purpose

- Drive CAFE through spec, plan, develop, review, and PR without bypassing its artifacts, blackboard state, or baton handoffs.
- Keep driver decisions grounded in the confirmed kickoff contract and
  `.cafe/strategic_context.yaml`.
- Preflight runtime updates and all three catalogs read-only; apply only exact, separately approved tokens and recheck afterward.
- Prefer non-interactive commands so work can run unattended and resume cleanly.

## Progressive disclosure

Read this file completely, then load only the references required by the current situation. Resolve every path relative to this `SKILL.md`.

| Situation | Read before acting |
| --- | --- |
| Start or resume workflow execution; answer a locale, playbook-selection, or kickoff question | `references/project_global_skill_sync.md`, `references/playbook_selection.md`, `references/kickoff.md`, `references/model_selection.md`, and `references/strategic_context.md` |
| Run, resume, inspect, retry, or recover ordinary workflow work | `references/running_workflow.md` |
| Handle `to_owner=user`, confirmation, clarification, permission, or alignment | `references/handoffs_and_alignment.md`; also read `references/strategic_context.md` |
| Start or resume linked work; confirm a spec or plan with an issue-decomposition assessment | `references/issue_decomposition.md`; also read `references/strategic_context.md` and `references/handoffs_and_alignment.md` |
| Diagnose incorrect workflow behavior or choose a repair layer | `references/diagnosis_and_repair.md`; also read the relevant runtime reference above |
| Review or ship after the PR phase | `references/convergent_pr_review.md`; also read `references/strategic_context.md` |
| Measure fresh-versus-resumed correction efficiency | `references/correction_ab_experiment.md` |

If more than one situation applies, read every listed reference before acting; do not preload unrelated references.

## Core invariants

- The complete kickoff contract is the first blocking gate. Do not run `cafe prepare`, mutate the repository, or execute the first workflow phase before the user confirms it.
- Resolve a playbook from explicit or durable authority; otherwise use `references/playbook_selection.md` to enumerate every effective candidate, filter by graph sufficiency, and compare valid applicability contracts. Record why the closest alternatives are insufficient. Never silently apply a common example or builtin default. Keep playbook selection issue-owned. Never write or update a playbook default in `.cafe/config.yaml` or `.cafe/strategic_context.yaml`; after kickoff confirmation, persist the selected `playbook_id` only in `.cafe/issues/<issue-name>/issue.yaml`.
- Assess issue nature, scale, and risk before kickoff. Include one exact primary model with user-approved fallbacks and adjustment authority; resolve profiles, classify capability, and record a phase-specific rationale without built-in providers or models.
- During that assessment, decide `required` or `not_required` proactive review for every agent-executed phase. Prefer the smallest useful set, give each an issue-specific rationale, and obtain complete user confirmation before preparation, execution, or persistence.
- Resolve the effective conversation locale from a direct user override first,
  then a reliably inferred user preference from the current thread, and finally
  the active playbook. Use that locale for every driver-to-user message.
- Obtain explicit kickoff confirmation for the repository content locale used
  by documentation and code comments. Conversation-locale inference may supply
  a recommended default, but it cannot confirm this convention for the user.
- Use `.cafe/strategic_context.yaml` as the single source for strategic
  documents and authority. Do not invent strategy or silently create issue
  overrides.
- Treat planned output confirmation, reactive user handoffs, and semantic
  alignment as separate decisions. The driver owns alignment; phase agents do
  not approve themselves.
- Make every user-owned handoff self-contained in conversation: assume no terminal, repository, or artifact access; render phase, purpose, questions, options, and plain-language reply format (paths/links are optional support only).
- Validate issue-decomposition assessments before confirming spec or plan;
  coordinate any authorized split through existing authority boundaries and
  reconstruct linked-work position from durable records.
- Resolve exactly one workflow operating mode in the confirmed kickoff: attached
  with positive polling, unattended background execution, or event-driven
  background execution with one supported CLI and the user's explicit exact
  model. Store an event-driven binding only under the active issue's
  `.cafe/issues/<issue>/driver/`; CAFE core and `issue.yaml` do not contain
  driver-mode policy.
- Use `cafe workflow --execute --mute-agent-output` when the invocation needs
  direct workflow controls such as `--start-step` or a manual diagnostic
  `--single-step`. After `cafe prepare`, `cafe make` is also a valid launcher;
  launcher choice and background process ownership are invocation mechanics,
  not driver policy.
- Configure all phase chains before execution; change them only under `model_selection.md`.
- Do not manually edit workflow artifacts, blackboard state, or
  `next_step.txt` except when repairing confirmed broken workflow state.
- Do not bypass CAFE by directly asking an agent to implement the issue.
- Modify source-of-truth playbooks and phase skills, never generated artifacts
  or installed global copies. Driver and CAFE core defects require escalation
  unless the user explicitly authorizes that source change.
- A phase or PR reporting success is evidence, not final proof. Ship only after
  the independent driver review has no unresolved in-mandate blockers.

## Driver and phase-agent responsibility boundary

- The phase agent owns implementation exploration: reading source code and
  diffs, choosing local edits, and running the plan's phase-level checks. The
  driver must not shadow the same work while the phase is progressing normally.
- During an active phase, the driver defaults to process-only monitoring. Track
  the current phase and iteration, command liveness, baton and task state,
  execution evidence, repeated failures, unnecessary phase restarts, and
  unexpected full-suite reruns through `cafe status`, `cafe show`, and bounded
  process output. Reading spec, plan, review, and PR artifacts remains part of
  the driver's confirmation and handoff duties.
- Do not inspect implementation code or diffs merely to watch progress. Enter
  bounded code-level diagnosis only when the same failure repeats without new
  evidence, the workflow is stuck, an agent crosses the confirmed scope or
  authority boundary, or reported success conflicts with durable evidence.
- After the PR phase, perform the independent convergent review once, in a
  batch. That final review deliberately inspects the implementation and is not
  replaced by process-only monitoring.

## Driver checklist
### Start or resume

- [ ] Run runtime and combined catalog preflight before a new kickoff or stale
  resume; record unavailable status and keep both approval scopes separate.
- [ ] Resolve or select the active playbook using `references/playbook_selection.md`,
  recording its rationale and independent-QA decision without creating a repository
  default; then resolve locale, confirmation gates, reactive handoffs, mandate, and worktree behavior.
- [ ] Assess issue nature, scale, and risk; resolve every phase's execution profile,
  capability band, exact primary and any fallbacks, rationale, cached or tested
  primary evidence, configured fallback smoke evidence, and adjustment authority.
- [ ] Assess every agent-executed phase for proactive review, render one `required` or `not_required` decision and rationale for each, then persist the user-confirmed contract in its skill-owned `driver/` area.
- [ ] Present the deterministic kickoff table and obtain explicit confirmation.
- [ ] Record the confirmed operating mode. For event-driven, create its exact
  per-issue callback binding with the bundled callback script before launch.

### Run

- [ ] Start or resume according to the confirmed driver mode and input; otherwise
  follow the persisted baton without forcing `--start-step`.
- [ ] In attached mode, honor the full positive poll cadence from the first
  wait; transport-only yields continue the same deferred wait without inspection.
- [ ] In event-driven mode, start the same continuous background worker as
  unattended with the trusted callback. A callback is an asynchronous,
  best-effort notification:
  it never gates the next phase and must not use `--single-step`.
- [ ] In a user-facing driver turn, relay only an explicit mandatory/user-required HumanTask answer with `cafe task complete --no-resume --json`, verify it durably, then resume using the confirmed mode; a confirmed `driver_confirmable` gate may be verified and completed by a driver, but detached callbacks cannot collect or infer user answers.
- [ ] Timestamp proactive polls and user updates; handle substantive output, completion, errors, and HumanTasks immediately.
- [ ] At each contract-defined pause or completion, inspect new phase evidence and revise only remaining model chains within confirmed authority.
- [ ] At a valid observation point for each executed required phase, the current Driver reviews its durable output directly, routes consolidated blockers through the existing correction path, and does not launch a separate reviewer or recursively review the result.
- [ ] When CAFE pauses, classify the handoff before supplying any input.
- [ ] When behavior is wrong, stop normal execution and use the bounded
  diagnosis reference.

### Complete
- [ ] Confirm the terminal state is `Workflow completed ... next=done`.
- [ ] Read the convergent PR review reference and finish its full review matrix.
- [ ] Merge only after all blockers are resolved, close the linked issue, run
  `cafe close`, and confirm the issue is absent from `cafe ls`.
- [ ] Report the relevant test evidence and final state in the effective locale.

## Reference index
- `references/kickoff.md` — locale, confirmation contract, formatter, prepare.
- `references/project_global_skill_sync.md` — silent check and approved updates.
- `references/strategic_context.md` — documents, mandate, protected overrides.
- `references/running_workflow.md` — commands, inspection, retries, operating rules.
- `references/model_selection.md` — phase profiles, model preflight, and reassessment.
- `references/phases_yaml.md` — confirmed-chain writer contract and non-authoritative field guidance.
- `references/handoffs_and_alignment.md` — user pauses and driver decisions.
- `references/diagnosis_and_repair.md` — bounded classification and disposition.
- `references/convergent_pr_review.md` — batched final review, merge, close, teardown.
- `references/correction_ab_experiment.md` — controlled efficiency experiment.
- `references/issue_decomposition.md` — validation, authority, and project position.
