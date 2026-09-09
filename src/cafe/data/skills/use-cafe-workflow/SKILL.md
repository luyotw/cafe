---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including bounded diagnosis and declarative repair when the workflow behaves incorrectly.
metadata: {version: 1.39.0}
---

# Use CAFE Workflow

## Purpose

- Drive CAFE through the selected playbook’s effective step graph without bypassing its artifacts, blackboard state, or baton handoffs.
- Keep driver decisions grounded in the confirmed kickoff contract and
  `.cafe/strategic_context.yaml`.
- Preflight runtime updates and all three catalogs read-only; keep catalog publication non-blocking and apply only explicitly requested, exact approved tokens.
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
| Reach a terminal state or receive a request for follow-up actions | `references/completion_and_authority.md` |
| Measure fresh-versus-resumed correction efficiency | `references/correction_ab_experiment.md` |

If more than one situation applies, read every listed reference before acting; do not preload unrelated references.

## Core invariants

- The complete kickoff contract is the first blocking gate. Do not run `cafe
  prepare`, mutate the repository, or execute the first workflow phase before the user confirms it.
- Resolve a playbook from explicit or durable authority; otherwise use `references/playbook_selection.md` to enumerate every effective candidate, filter by graph sufficiency, and compare valid applicability contracts. Record why the closest alternatives are insufficient. Never silently apply a common example or builtin default. Keep playbook selection issue-owned. Never write or update a playbook default in `.cafe/config.yaml` or `.cafe/strategic_context.yaml`; after kickoff confirmation, persist the selected `playbook_id` only in `.cafe/issues/<issue-name>/issue.yaml`.
- Assess the issue nature, scale, and risk before kickoff. Include one exact
  primary model with any user-approved fallbacks per phase in the contract. Resolve provider-neutral phase execution profiles from the active
  playbook skills, classify the remaining work into a capability band, and
  record a phase-specific selection rationale; no provider or model is built
  into this driver skill.
- During that assessment, decide `required` or `not_required` proactive review for every agent-executed phase. Only a phase followed by an existing scheduled confirmation pause before workflow advancement is eligible for `required`; use `not_required` when the workflow would advance immediately. Prefer the smallest useful eligible set, give each an issue-specific rationale, and obtain complete user confirmation before preparation, execution, or persistence.
- Resolve the effective conversation locale from a direct user override first,
  then a reliably inferred user preference from the current thread, and finally
  the active playbook. Use that locale for every driver-to-user message.
- Obtain explicit kickoff confirmation for the repository content locale used
  by documentation and code comments. Conversation-locale inference may supply
  a recommended default, but it cannot confirm this convention for the user.
- Use `.cafe/strategic_context.yaml` as the single source for strategic
  documents and authority. Do not invent strategy or silently create issue
  overrides.
- Confirm a complete versioned Delivery Contract in the same kickoff before `cafe prepare`: outcome, full scope, acceptance/evidence, implementation direction, constraints, permitted variations and deviation triggers. Keep hard invariants limited to explicit user requirements and externally meaningful behavior. Put unresolved internal mechanisms and reasonable technical choices in `allowed_variations` so ordinary implementation decisions do not reopen the contract. Store it only in `driver/contract.json`. At existing eligible output gates, follow the evidence comparison in `references/handoffs_and_alignment.md`; a smaller implementation must preserve all requirements. Never add a gate or assume particular step/artifact names.
- Treat planned output confirmation, reactive user handoffs, and semantic
  alignment as separate decisions. The driver owns alignment; phase agents do
  not approve themselves.
- Make every user-owned handoff self-contained in conversation: assume no terminal, repository, or artifact access; state where the workflow paused, why it needs the user, every option with its practical consequence, and a plain-language reply example. Add evidence, scope, risk, next-phase, model, or external-effect details only when they materially affect the decision. Bare confirmation, link-only, and raw-artifact-dump handoffs are invalid.
- On a later user-facing turn, inspect durable state first. If a user-owned task is still pending and no adequate handoff has appeared in the current conversation, answer the user's immediate question briefly and append the same compact task summary. Do not repeat an adequate handoff unless the task or options changed or the user asks.
- Validate issue-decomposition assessments before confirming spec or plan;
  coordinate any authorized split through existing authority boundaries and
  reconstruct linked-work position from durable records.
- Resolve exactly one workflow operating mode in the confirmed kickoff: attached with positive polling, unattended background execution, or event-driven background execution with a non-empty ordered chain of distinct conforming CLIs. In event-driven mode, the primary entry uses the current user session and therefore stores no model; every fallback entry requires one explicit exact model. Default new workflows to event-driven unless the user explicitly chooses another mode or an existing issue contract already fixes the mode. Keep the selected mode visible in the kickoff confirmation. Store it only in `.cafe/issues/<issue>/driver/contract.json`; CAFE core and `issue.yaml` do not contain Driver-mode policy.
- For a Driver-managed launch, validate Driver-owned entry authority after bounded preflight, then invoke generic CAFE through its existing command. The validation does not read, project, bind, or authorize `issue.yaml`, playbook, phase, or capability configuration; those remain under their generic contracts. Event callback dispatch derives its primary CLI and fallback CLI/model order from the Driver contract; it never passes a model override when waking the primary session. Mutable dispatch state stores only digest and delivery progress. Session acquisition and actual callback durable acceptance are separate boundaries: every unbound entry bootstraps with a request exactly equivalent to `say "HI"`, persists the provider-created session ID before actual delivery, and never counts bootstrap as event delivery or acceptance. Bind the provider acknowledgement to the exact callback event identity; an ambiguous outcome stops forward routing.
- Use `cafe workflow --execute --mute-agent-output` when the invocation needs
  direct workflow controls such as `--start-step` or a manual diagnostic
  `--single-step`. After `cafe prepare`, `cafe make` is also a valid launcher;
  launcher choice and background process ownership are invocation mechanics,
  not driver policy.
- Configure all phase chains before execution as initial values. The Driver cannot change a chain on its own. When the user explicitly requests a different phase model chain, apply that exact phase-only update for the next phase start or iteration through `model_selection.md`; let any running iteration finish, and do not alter the event-driven callback chain or reopen the full kickoff contract.
- Do not manually edit workflow artifacts, blackboard state, or
  `next_step.txt` except when repairing confirmed broken workflow state.
- Do not bypass CAFE by directly asking an agent to implement the issue.
- Modify source-of-truth playbooks and phase skills, never generated artifacts
  or installed global copies. Driver and CAFE core defects require escalation
  unless the user explicitly authorizes that source change.
- Follow only the effective graph, declared capabilities, confirmed gates, and explicit user authority. “Finish”, “complete the rest”, or “continue to the end” covers only already-scoped steps; it never grants merge, deploy, close, delete, publish, or other external mutation authority. Each action needs its own authority; completion adds none.

## Driver and phase-agent responsibility boundary

- The phase agent owns implementation exploration: reading source code and
  diffs, choosing local edits, and running the plan's phase-level checks. The
  driver must not shadow the same work while the phase is progressing normally.
- During an active phase, the driver defaults to process-only monitoring. Track
  the current phase and iteration, command liveness, baton and task state,
  execution evidence, repeated failures, unnecessary phase restarts, and
  unexpected full-suite reruns through `cafe status`, `cafe show`, and bounded
  process output. Reading declared artifacts at the actual confirmation and handoff
  boundaries remains part of the driver's duties.
- Do not inspect implementation code or diffs merely to watch progress. Enter
  bounded code-level diagnosis only when the same failure repeats without new
  evidence, the workflow is stuck, an agent crosses the confirmed scope or
  authority boundary, or reported success conflicts with durable evidence.
- At declared review and confirmation boundaries, inspect the relevant evidence
  under their contracts. Do not add an extra phase or final review gate.

## Driver checklist
### Start or resume

- [ ] Run runtime and combined catalog preflight before a new kickoff or stale
  resume; record unavailable status and keep both approval scopes separate.
- [ ] Resolve or select the active playbook using `references/playbook_selection.md`,
  recording its rationale and independent-QA decision without creating a repository
  default; then resolve locale, confirmation gates, reactive handoffs, mandate, and worktree behavior.
- [ ] Assess issue nature, scale, and risk; resolve every phase's execution profile,
  capability band, exact primary and any fallbacks, rationale, cached or tested
  primary evidence, and configured fallback smoke evidence.
- [ ] Assess every agent-executed phase for proactive review and render one `required` or `not_required` decision and rationale for each.
- [ ] Include the complete Delivery Contract in the deterministic kickoff table and obtain semantic user confirmation once; no exact approval-string matching.
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
- [ ] In a user-facing driver turn, relay only an explicit mandatory/user-required HumanTask answer with `cafe task complete --no-resume --json`, verify it durably, then resume using the confirmed mode. A Driver may submit an active declared non-advancing correction revise only after proactive-review consensus; advancing mandatory/user-required confirmation and every other user-owned decision remain with the user. A confirmed `driver_confirmable` gate may be verified and completed by a Driver, but detached callbacks cannot collect or infer user answers.
- [ ] Timestamp proactive polls and user updates; handle substantive output, completion, errors, and HumanTasks immediately.
- [ ] At each contract-defined pause or completion, inspect new phase evidence. Keep phase model chains unchanged unless the user explicitly requests an exact phase-only update for subsequent execution.
- [ ] At the existing scheduled confirmation pause after each executed required phase, the current Driver completes its review before one `cafe chat <role> -p` consensus exchange, uses the declared correction path only for agreed durable changes, re-reviews every formal correction iteration, and does not launch a separate reviewer or recursively review the result.
- [ ] When CAFE pauses, classify the handoff before supplying any input.
- [ ] When behavior is wrong, stop normal execution and use the bounded
  diagnosis reference.

### Complete
- [ ] Follow `references/completion_and_authority.md`: verify the declared terminal
  state and its required evidence, then proactively help the user close out.
- [ ] Present the deliverables and only relevant remaining actions, grounded in
  the actual playbook and user goal. Continue authorized assistance; ask only
  for missing action-specific authority. Do not invent an extra workflow gate.
- [ ] Report verified results and any user-owned next step in the effective locale.

## Reference index
- `references/kickoff.md` — locale, confirmation contract, formatter, prepare.
- `references/project_global_skill_sync.md` — silent check and approved updates.
- `references/strategic_context.md` — documents, mandate, protected overrides.
- `references/running_workflow.md` — commands, inspection, retries, operating rules.
- `references/model_selection.md` — phase profiles, model preflight, and reassessment.
- `references/phases_yaml.md` — confirmed-chain writer contract and non-authoritative field guidance.
- `references/handoffs_and_alignment.md` — user pauses and driver decisions.
- `references/diagnosis_and_repair.md` — bounded classification and disposition.
- `references/completion_and_authority.md` — proactive closeout and separate action authority.
- `references/correction_ab_experiment.md` — controlled efficiency experiment.
- `references/issue_decomposition.md` — validation, authority, and project position.
